"""원본 → 집계 테이블 (PIPE-05 집계, PIPE-06 재집계).

규칙: data/docs/calculation-rules.md 7장
- 한 날짜의 집계는 그날 원본으로 처음부터 다시 만든다 (지우고 새로 넣는다)
- 회원 한 명의 집계는 한 트랜잭션. 중간에 끊기면 그 회원은 하나도 바뀌지 않는다
- 다시 계산할 날짜: 원본 ingested_at이 그 회원 집계의 마지막 computed_at보다 나중인 기록이 걸친 날짜와 앞뒤 하루
  (심박·산소포화도가 전날 밤 수면, 자정을 넘는 운동에 걸칠 수 있어서)
- 집계 직후 같은 트랜잭션에서 생활 습관 점수(lifestyle_score)를 다시 계산한다 (score-rules.md).
  새 데이터는 CANDiY를 조회할 때만 들어오므로, 집계와 같은 실행에서 계산하면 집계와 점수가 엇갈리지 않는다
- 적재에 성공한 지표의 조회 준비 상태(sync_state.initial_sync_done, last_data_at 등)도 같은 트랜잭션에서 바꾼다
  (aggregate_and_mark_ready). 조회 쪽은 준비 완료와 집계 결과를 언제나 함께 본다 (S15P31A403-78)
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from . import rules, score
from .loader import mark_ready
from .metrics import PRIMARY_APP
from .rules import Sample, kst_date

AGGREGATE_TABLES = ("activity_daily", "exercise_session", "sleep_session", "heart_daily", "body_daily")
# 심박 기준선: 그날 깬 수면 이전 28일 주 수면 (score-rules.md 4장). 팀 제안
HR_BASELINE_DAYS = 28


@dataclass(frozen=True)
class Raw:
    metric: str
    data_id: str
    app: str
    start_at: datetime
    end_at: datetime | None
    payload: dict

    @property
    def last_at(self) -> datetime:
        return self.end_at or self.start_at


# ----- 다시 계산할 날짜 -----


def _span(start: datetime, end: datetime) -> set[date]:
    first, last = kst_date(start) - timedelta(days=1), kst_date(end) + timedelta(days=1)
    return {first + timedelta(days=i) for i in range((last - first).days + 1)}


def dates_to_aggregate(conn: psycopg.Connection, member_id, *, all_dates: bool = False) -> set[date]:
    """새로 들어오거나 바뀐 원본이 걸친 날짜. all_dates면 원본이 있는 모든 날짜 (규칙이 바뀌었을 때)."""
    if all_dates:
        watermark = None
    else:
        watermark = conn.execute(
            "SELECT max(t) FROM ("
            + " UNION ALL ".join(
                f"SELECT max(computed_at) AS t FROM {t} WHERE end_user_id = %(m)s" for t in AGGREGATE_TABLES
            )
            + ") x",
            {"m": member_id},
        ).fetchone()[0]

    rows = conn.execute(
        "SELECT start_at, coalesce(end_at, start_at) FROM raw_health_record "
        "WHERE end_user_id = %(m)s AND (%(w)s::timestamptz IS NULL OR ingested_at > %(w)s) "
        # 원본은 그대로인데 걸음 누적값 버전만 새로 들어온 경우 (늦게 도착한 이전 버전 등)
        "UNION ALL "
        "SELECT r.start_at, coalesce(r.end_at, r.start_at) FROM step_snapshot s "
        "JOIN raw_health_record r ON r.end_user_id = s.end_user_id AND r.metric = 'steps' AND r.data_id = s.data_id "
        "WHERE s.end_user_id = %(m)s AND (%(w)s::timestamptz IS NULL OR s.ingested_at > %(w)s)",
        {"m": member_id, "w": watermark},
    ).fetchall()
    result: set[date] = set()
    for start, end in rows:
        result |= _span(start, end)
    return result


# ----- 원본 읽기 -----


def _load(conn: psycopg.Connection, member_id, dates: set[date]) -> dict[str, list[Raw]]:
    lo = rules.day_start(min(dates) - timedelta(days=1))
    hi = rules.day_start(max(dates) + timedelta(days=2))
    rows = conn.execute(
        "SELECT metric, data_id, app, start_at, end_at, payload FROM raw_health_record "
        "WHERE end_user_id = %s AND start_at < %s AND coalesce(end_at, start_at) >= %s",
        (member_id, hi, lo),
    ).fetchall()
    by_metric: dict[str, list[Raw]] = defaultdict(list)
    for metric, data_id, app, start, end, payload in rows:
        by_metric[metric].append(Raw(metric, data_id, app, start, end, payload))
    return by_metric


def _load_step_versions(conn: psycopg.Connection, member_id, data_ids: list[str]) -> dict[str, list]:
    """걸음 기록별 누적값 버전 (갱신 시각, 누적값)."""
    if not data_ids:
        return {}
    rows = conn.execute(
        "SELECT data_id, modified_at, step_count FROM step_snapshot WHERE end_user_id = %s AND data_id = ANY(%s)",
        (member_id, data_ids),
    ).fetchall()
    by_id: dict[str, list] = defaultdict(list)
    for data_id, modified_at, count in rows:
        by_id[data_id].append((modified_at, count))
    return by_id


def _starting_on(records: list[Raw], d: date) -> list[Raw]:
    return [r for r in records if kst_date(r.start_at) == d]


def _fully_inside(records: list[Raw], start: datetime, end: datetime) -> list[Raw]:
    return [r for r in records if r.start_at >= start and r.last_at <= end]


# ----- 테이블별 계산 -----


def _overlaps_primary(ex: Raw, sessions: list[Raw]) -> bool:
    """삼성 헬스가 아닌 운동이 삼성 헬스 운동과 시간이 겹치는지 (7.4, S15P31A403-231).

    워치에서 시작한 걷기는 삼성 헬스와 LiNK가 둘 다 운동으로 보낸다. 겹치면 같은 운동이라 삼성 헬스 것만 센다.
    """
    if ex.app == PRIMARY_APP:
        return False
    return any(
        o.app == PRIMARY_APP and o.end_at is not None and o.start_at < ex.end_at and ex.start_at < o.end_at
        for o in sessions
    )


def _session_speed(distance_m: float | None, start: datetime, end: datetime, speeds: list[Raw]) -> float | None:
    """운동 평균 속도 m/s (7.4, S15P31A403-260).

    거리 ÷ 운동 시간(시작~끝, 초). 멈춘 시간까지 든 실제 평균이다. 속도 샘플은 움직이는 동안만 찍혀
    평균을 내면 실제보다 빠르게 나와, 거리가 없거나 0일 때만 출처 하나의 샘플 평균을 쓴다.
    """
    seconds = (end - start).total_seconds()
    if distance_m is not None and distance_m > 0 and seconds > 0:
        return round(distance_m / seconds, 2)
    overlapping = [r for r in speeds if r.start_at <= end and r.last_at >= start]
    samples = rules.within(
        rules.valid_speed([s for r in rules.one_source(overlapping) for s in rules.samples(r.payload, "speed_mps")]),
        start,
        end,
    )
    return round(sum(s.value for s in samples) / len(samples), 2) if samples else None


def _exercise_rows(raw: dict[str, list[Raw]], heart: list[Sample], d: date) -> list[dict]:
    rows = []
    for ex in _starting_on(raw["exercise_sessions"], d):
        code = ex.payload.get("exercise_type")
        if ex.end_at is None or not isinstance(code, int):
            continue
        if _overlaps_primary(ex, raw["exercise_sessions"]):
            continue  # 삼성 헬스 운동과 같은 운동 (중복)
        if not rules.exercise_duration_ok(code, ex.start_at, ex.end_at):
            continue  # 이상치 운동 (8장)
        hr = rules.heart_stats(rules.within(heart, ex.start_at, ex.end_at))
        # 폰 Health Connect 걷기 구간이 운동 시간에 겹쳐 와도 출처 하나만 더한다 (7.1)
        distance = rules.total(
            [
                rules.non_negative(rules.number(r.payload, "distance_m"))
                for r in rules.one_source(_fully_inside(raw["distances"], ex.start_at, ex.end_at))
            ]
        )
        avg_speed = _session_speed(distance, ex.start_at, ex.end_at, raw["speeds"])
        title = ex.payload.get("title")
        rows.append(
            {
                "data_id": ex.data_id,
                "date": d,
                "code": code,
                "title": title if isinstance(title, str) and title else None,
                "start_at": ex.start_at,
                "end_at": ex.end_at,
                "duration_minutes": rules.minutes_between(ex.start_at, ex.end_at),
                # 운동 시작~끝 안에 완전히 들어간 기록만, 출처 하나만 더한다 (7.4, 7.1)
                "calories_kcal": rules.total(
                    [
                        rules.non_negative(rules.number(r.payload, "energy_kcal"))
                        for r in rules.one_source(_fully_inside(raw["calories_burned"], ex.start_at, ex.end_at))
                    ]
                ),
                "distance_m": distance,
                "avg_speed_mps": avg_speed,
                "hr_avg": hr["avg"],
                "hr_max": hr["max"],
                # 운동 시간에 넣는지 (S15P31A403-116). 점수와 같은 판정을 저장된(반올림한) 속도로 한다
                "counts_as_exercise": score.counts_as_exercise(code, avg_speed),
            }
        )
    return rows


def _activity_row(
    raw: dict[str, list[Raw]],
    exercises: list[dict],
    step_versions: dict[str, list],
    d: date,
) -> dict | None:
    # 삼성 헬스 하루 기록과 폰 Health Connect 짧은 기록이 함께 오면 삼성 헬스만 쓴다 (7.1, S15P31A403-209)
    steps = rules.one_source(_starting_on(raw["steps"], d))
    if not (steps or exercises):
        return None
    step_records = [
        rules.StepRecord(r.start_at, r.end_at, c, tuple(step_versions.get(r.data_id, ())))
        for r in steps
        if (c := rules.number(r.payload, "count")) is not None
    ]
    step_total = rules.total([s.count for s in step_records])
    if step_total is not None and step_total > rules.STEPS_DAY_MAX:
        step_total, step_records = None, []  # 하루 10만 보 초과는 이상치 → 그날 걸음 결측 (8장)
    # 운동 횟수·시간은 운동으로 인정된 세션만 센다 (S15P31A403-117).
    # 느린 걷기만 있는 날도 위에서 행을 만들어 "기록 없음"이 아니라 운동 0분이 된다
    counted = [e for e in exercises if e["counts_as_exercise"]]
    return {
        "date": d,
        "steps": round(step_total) if step_total is not None else None,
        # 하루 단위 기록은 누적값 버전 사이 차이로 나눈다 (7.3)
        "hourly_steps": rules.hourly_steps(step_records, d),
        # 칼로리·거리는 운동 세션 값의 합이다 (7.3). 운동 밖 기록(LiNK 15분 칼로리, 폰 걷기 거리)은 운동량이 아니다.
        # 이상치 운동은 세션에서 이미 빠져 있다 (8장)
        "exercise_calories_kcal": rules.total([e["calories_kcal"] for e in exercises]),
        "exercise_distance_m": rules.total([e["distance_m"] for e in exercises]),
        "exercise_count": len(counted),
        "exercise_minutes": sum(e["duration_minutes"] for e in counted),
    }


def _sleep_rows(raw: dict[str, list[Raw]], heart: list[Sample], d: date) -> tuple[list[dict], list[str]]:
    """그날 깬 수면 행과, 행에 들어간 모든 조각의 data_id.

    날짜로 거르기 전에 먼저 나뉜 수면을 묶는다 (7.5). 묶은 덩어리 하나가 한 행이고 data_id는 첫 조각의 것이다.
    """
    rows = []
    fragment_ids: list[str] = []
    groups = rules.merge_sleeps([sl for sl in raw["sleeps"] if sl.end_at is not None])
    for group in groups:
        start = min(sl.start_at for sl in group)
        end = max(sl.end_at for sl in group)
        if kst_date(end) != d:
            continue
        fragment_ids += [sl.data_id for sl in group]
        if end - start > rules.SLEEP_SESSION_MAX:
            continue  # 14시간 넘는 수면은 이상치 (8장). 예전 행은 지우고 새 행은 만들지 않는다

        st = sorted((s for sl in group for s in rules.stages(sl.payload)), key=lambda s: s.start)
        if st and len(group) > 1:
            # 조각 사이 빈 시간은 깨어 있던 시간이다
            st = sorted(st + rules.gap_stages(group), key=lambda s: s.start)
        minutes = rules.stage_minutes(st)
        # 실제로 잔 시간은 조각마다 구해 더한다. 한 조각이라도 알 수 없으면 전체를 알 수 없다
        per_fragment = [rules.sleep_minutes(sl.payload, rules.stages(sl.payload)) for sl in group]
        slept = None if any(m is None for m in per_fragment) else sum(per_fragment)
        if slept is not None and slept > rules.minutes_between(start, end):
            slept = None  # 잔 시간이 세션보다 길면 모순이라 잔 시간만 알 수 없음으로 둔다 (8장)
        # 개수(spo2_low_count)와 시각 목록(spo2_low_times)은 같은 샘플로 만든다 (S15P31A403-302)
        spo2_samples = [
            Sample(r.start_at, v)
            for r in raw["oxygen_saturations"]
            if start <= r.start_at <= end
            and (v := rules.number(r.payload, "percentage")) is not None
            and rules.valid_spo2(v)
        ]
        spo2 = rules.spo2_stats([s.value for s in spo2_samples])
        # 단계 기록이 있으면 잠든 단계 안의 심박만, 없으면 세션 전체 (7.5, S15P31A403-210)
        in_session = rules.within(heart, start, end)
        hr = rules.heart_stats(rules.asleep_samples(in_session, st) if st else in_session)
        timeline = rules.sleep_timeline(st)
        rows.append(
            {
                "data_id": group[0].data_id,
                "date": d,
                "start_at": start,
                "end_at": end,
                "is_main": False,
                "session_minutes": rules.minutes_between(start, end),
                "sleep_minutes": slept,
                **{f"{k}_minutes": v for k, v in minutes.items()},
                "sleep_timeline": Jsonb(timeline) if timeline is not None else None,
                "spo2_count": spo2["count"],
                "spo2_avg": spo2["avg"],
                "spo2_min": spo2["min"],
                "spo2_max": spo2["max"],
                "spo2_low_count": spo2["low_count"],
                "spo2_low_times": rules.spo2_low_times(spo2_samples),
                "hr_avg": hr["avg"],
                "hr_min": hr["min"],
                "hr_max": hr["max"],
            }
        )
    main_id = rules.pick_main_sleep(rows)
    for row in rows:
        row["is_main"] = row["data_id"] == main_id
    return rows, fragment_ids


def _heart_row(heart: list[Sample], d: date) -> dict | None:
    today = [s for s in heart if kst_date(s.time) == d]
    if not today:
        return None
    stats = rules.heart_stats(today)
    hourly = rules.hourly_heart(today)
    return {
        "date": d,
        # 최저·최고·개수는 모든 샘플, 평균만 시간대별 평균의 평균이다 (7.6)
        "hr_avg": rules.daily_heart_avg(today),
        "hr_min": stats["min"],
        "hr_max": stats["max"],
        "sample_count": stats["count"],
        "hourly_hr": Jsonb(hourly) if hourly is not None else None,
    }


def _body_rows(raw: dict[str, list[Raw]], d: date) -> list[dict]:
    rows = []
    for metric, (key, unit) in rules.BODY_FIELDS.items():
        measured = [
            (r, v)
            for r in _starting_on(raw[metric], d)
            if (v := rules.number(r.payload, key)) is not None and rules.valid_body(metric, v)
        ]
        if not measured:
            continue
        last, value = max(measured, key=lambda rv: rv[0].start_at)
        rows.append(
            {
                "metric": metric,
                "date": d,
                "value": round(value, 2),
                "unit": unit,
                "measured_at": last.start_at,
                "data_id": last.data_id,
            }
        )
    return rows


# ----- 쓰기 -----


def _insert(conn: psycopg.Connection, table: str, member_id, row: dict) -> None:
    cols = ["end_user_id", *row.keys()]
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        (member_id, *row.values()),
    )


def aggregate_member(conn: psycopg.Connection, member_id, dates: set[date]) -> Counter:
    """날짜마다 집계 테이블 5개를 다시 만든다. 테이블별 만든 행 수를 돌려준다."""
    counts: Counter = Counter()
    if not dates:
        return counts
    raw = _load(conn, member_id, dates)
    # 같은 워치 심박이 삼성 헬스와 LiNK 둘로 오므로 기록 시작 날짜마다 출처 하나만 쓴다 (7.1, S15P31A403-231).
    # 범위 밖 샘플은 여기서 한 번에 뺀다. 하루·수면·운동 심박과 운동 속도가 모두 이 샘플을 쓴다 (8장)
    heart_by_day: dict[date, list[Raw]] = defaultdict(list)
    for r in raw["heart_rates"]:
        heart_by_day[kst_date(r.start_at)].append(r)
    heart = rules.valid_heart(
        sorted(
            (
                s
                for records in heart_by_day.values()
                for r in rules.one_source(records)
                for s in rules.samples(r.payload, "beatsPerMinute")
            ),
            key=lambda s: s.time,
        )
    )
    step_versions = _load_step_versions(conn, member_id, [r.data_id for r in raw["steps"]])

    with conn.transaction():
        for d in sorted(dates):
            exercises = _exercise_rows(raw, heart, d)
            sleeps, sleep_fragment_ids = _sleep_rows(raw, heart, d)
            activity = _activity_row(raw, exercises, step_versions, d)
            heart_row = _heart_row(heart, d)
            bodies = _body_rows(raw, d)

            for table in AGGREGATE_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s AND date = %s", (member_id, d))
            # 시작·종료 시각이 바뀌어 날짜가 옮겨진 세션, 묶이기 전 따로 저장됐던 수면 조각의 예전 행
            for table, ids in (
                ("exercise_session", [r["data_id"] for r in exercises]),
                ("sleep_session", sleep_fragment_ids),
            ):
                if ids:
                    conn.execute(
                        f"DELETE FROM {table} WHERE end_user_id = %s AND data_id = ANY(%s)",
                        (member_id, ids),
                    )

            for row in exercises:
                _insert(conn, "exercise_session", member_id, row)
            for row in sleeps:
                _insert(conn, "sleep_session", member_id, row)
            if activity:
                _insert(conn, "activity_daily", member_id, activity)
            if heart_row:
                _insert(conn, "heart_daily", member_id, heart_row)
            for row in bodies:
                _insert(conn, "body_daily", member_id, row)

            counts["exercise_session"] += len(exercises)
            counts["sleep_session"] += len(sleeps)
            counts["activity_daily"] += activity is not None
            counts["heart_daily"] += heart_row is not None
            counts["body_daily"] += len(bodies)

        counts["lifestyle_score"] += _score_member(conn, member_id, dates)
    return counts


# ----- 생활 습관 점수 (score-rules.md) -----

SCORE_COLUMNS = (
    "total_score",
    "activity_score",
    "activity_grade",
    "steps_score",
    "exercise_score",
    "sleep_score",
    "sleep_grade",
    "heart_score",
    "heart_grade",
    "check_wear",
    "steps",
    "exercise_minutes",
    "sleep_minutes",
    "sleep_hr_avg",
    "hr_baseline",
    "hr_baseline_nights",
)


def _score_member(conn: psycopg.Connection, member_id, dates: set[date]) -> int:
    """집계가 바뀐 날짜와, 그 날짜를 심박 기준선(28일)에 쓰는 뒤 날짜의 점수를 다시 만든다.

    방금 집계한 행을 같은 트랜잭션에서 읽는다. 집계 행이 하나도 없는 날짜는 점수 행을 지운다.
    """
    lo, hi = min(dates), max(dates) + timedelta(days=HR_BASELINE_DAYS)
    params = {"m": member_id, "lo": lo - timedelta(days=HR_BASELINE_DAYS), "hi": hi}
    steps = dict(
        conn.execute(
            "SELECT date, steps FROM activity_daily WHERE end_user_id = %(m)s AND date BETWEEN %(lo)s AND %(hi)s",
            params,
        ).fetchall()
    )
    exercises: dict[date, list[score.Exercise]] = defaultdict(list)
    for d, code, minutes, speed in conn.execute(
        "SELECT date, code, duration_minutes, avg_speed_mps FROM exercise_session "
        "WHERE end_user_id = %(m)s AND date BETWEEN %(lo)s AND %(hi)s",
        params,
    ).fetchall():
        exercises[d].append(score.Exercise(code, minutes, speed))
    sleeps = {
        d: (minutes, hr)
        for d, minutes, hr in conn.execute(
            "SELECT date, sleep_minutes, hr_avg FROM sleep_session "
            "WHERE end_user_id = %(m)s AND is_main AND date BETWEEN %(lo)s AND %(hi)s",
            params,
        ).fetchall()
    }

    conn.execute("DELETE FROM lifestyle_score WHERE end_user_id = %s AND date BETWEEN %s AND %s", (member_id, lo, hi))
    written = 0
    days = sorted(d for d in steps.keys() | sleeps.keys() if lo <= d <= hi)
    for d in days:
        sleep_minutes, sleep_hr = sleeps.get(d, (None, None))
        window = [d - timedelta(days=i) for i in range(1, HR_BASELINE_DAYS + 1)]
        nights = [sleeps[w][1] for w in window if w in sleeps and sleeps[w][1] is not None]
        s = score.day_score(steps.get(d), exercises.get(d, []), sleep_minutes, sleep_hr, nights)
        _insert(conn, "lifestyle_score", member_id, {"date": d, **{c: getattr(s, c) for c in SCORE_COLUMNS}})
        written += 1
    return written


def aggregate_and_mark_ready(conn: psycopg.Connection, member_id, dates: set[date], loaded: list[str]) -> Counter:
    """집계하고, 같은 트랜잭션에서 적재에 성공한 지표(loaded)를 조회 준비 완료로 만든다.

    집계가 실패하면 준비 상태도 바뀌지 않는다. 바뀐 날짜가 없어도(새 기록 없음) 준비 상태는 갱신한다.
    """
    with conn.transaction():
        counts = aggregate_member(conn, member_id, dates)
        mark_ready(conn, member_id, loaded)
    return counts
