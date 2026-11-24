"""원본 → 집계 계산 규칙 (data/docs/calculation-rules.md 7장).

DB와 상관없는 순수 함수만 둔다. 실데이터로 바뀔 수 있는 규칙은 함수 하나씩으로 떼어 두었다.
- daily_heart_avg: 하루 심박 평균 (시간대별 평균의 평균, 7.6)
- merge_sleeps, pick_main_sleep: 나뉜 수면 묶기와 주 수면 고르기 (7.5)
- 이상치 기준 (8장): HEART_BPM_RANGE 등 아래 상수와 valid_*, exercise_duration_ok
- SPO2_LOW_THRESHOLD: 산소포화도 낮음 기준 (90% 미만, S15P31A403-130)
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, TypeVar

from .metrics import PRIMARY_APP
from .records import KST

# 수면 단계 번호 → 집계 칸 (calculation-rules.md 7.5)
STAGE_NAMES = {0: "unknown", 1: "awake", 2: "unspecified", 3: "awake", 4: "light", 5: "deep", 6: "rem", 7: "awake"}
STAGE_COLUMNS = ("awake", "light", "deep", "rem", "unspecified", "unknown")
# sleep_duration_total이 없을 때 잔 시간으로 더하는 단계
ASLEEP_STAGES = frozenset({2, 4, 5, 6})

# 산소포화도 낮음 기준 (%). 이 값보다 작은 측정을 spo2_low_count로 센다 (S15P31A403-130, calculation-rules.md 7.5).
# 수면 연구의 T90(수면 중 90% 미만 시간 비율)과 같은 기준선. 진단이 아니라 "낮게 측정된 횟수"다
SPO2_LOW_THRESHOLD: float | None = 90.0

# 앞 수면이 끝나고 이 시간 안에 다시 잠들면 같은 밤으로 묶는다 (7.5)
SLEEP_MERGE_GAP = timedelta(minutes=60)
# 묶은 수면 사이 간격을 타임라인·단계별 시간에 넣을 때 쓰는 단계 (1 = 깨어 있음)
GAP_STAGE = 1

# ----- 이상치 기준 (calculation-rules.md 8장). 원본은 그대로 두고 집계할 때만 뺀다 -----

# 샘플·측정값 단위: 범위 밖이면 그 값만 뺀다
HEART_BPM_RANGE = (30.0, 220.0)
SPO2_RANGE = (70.0, 100.0)
SPEED_MPS_MAX = 60 / 3.6  # 시속 60km

# 기록 단위: 범위 밖이면 그 기록을 뺀다
STEPS_DAY_MAX = 100_000
SLEEP_SESSION_MAX = timedelta(hours=14)
EXERCISE_MIN = timedelta(minutes=1)
EXERCISE_MAX_DEFAULT = timedelta(hours=6)
# 하루 종일 할 수 있는 운동만 상한을 늘린다 (Health Connect 운동 코드, exercise_code)
EXERCISE_MAX_BY_CODE = {
    37: timedelta(hours=12),  # 등산
    8: timedelta(hours=10),  # 자전거
    58: timedelta(hours=10),  # 요트
    56: timedelta(hours=8),  # 달리기
    79: timedelta(hours=8),  # 걷기
    61: timedelta(hours=8),  # 스키
    62: timedelta(hours=8),  # 스노보드
    32: timedelta(hours=8),  # 골프
    46: timedelta(hours=8),  # 패들링
}
BODY_RANGES = {
    "weights": (30.0, 300.0),
    "body_fats": (3.0, 60.0),
    "basal_metabolic_rates": (500.0, 3500.0),
    "heights": (1.0, 2.5),
}

# 신체 지표 → (data 안 키, 단위) (health-tables.md 3.8)
BODY_FIELDS = {
    "weights": ("weight_kg", "kg"),
    "body_fats": ("percentage", "%"),
    "basal_metabolic_rates": ("bmr_kcal_per_day", "kcal/day"),
    "heights": ("height_m", "m"),
}

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


@dataclass(frozen=True)
class Sample:
    time: datetime
    value: float


# ----- 시간 -----


def parse_time(value: object) -> datetime | None:
    """data 안 시각 (UTC "Z")."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def kst_date(dt: datetime) -> date:
    return dt.astimezone(KST).date()


def day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=KST)


def minutes_between(start: datetime, end: datetime) -> int:
    return round((end - start).total_seconds() / 60)


def parse_duration_minutes(value: object) -> int | None:
    """ISO 8601 기간 (PT5H37M28S) → 분. 값이 없으면 문자열 "null"이 온다."""
    if not isinstance(value, str):
        return None
    m = _DURATION.match(value)
    if not m or value in ("P", "PT"):
        return None
    days, hours, minutes, seconds = (float(g) if g else 0.0 for g in m.groups())
    return round((days * 86400 + hours * 3600 + minutes * 60 + seconds) / 60)


# ----- 샘플 -----


def samples(payload: dict, value_key: str) -> list[Sample]:
    result = []
    for s in payload.get("samples") or []:
        if not isinstance(s, dict):
            continue
        t = parse_time(s.get("time"))
        v = s.get(value_key)
        if t is not None and isinstance(v, int | float):
            result.append(Sample(t, float(v)))
    return result


def within(items: list[Sample], start: datetime, end: datetime) -> list[Sample]:
    return [s for s in items if start <= s.time <= end]


def asleep_samples(items: list[Sample], stages: list["Stage"]) -> list[Sample]:
    """잠든 단계(ASLEEP_STAGES) 안의 샘플 (7.5, S15P31A403-210). 깸·잠들기 전 심박이 수면 중 심박을 올리지 않게 한다."""
    spans = [(st.start, st.end) for st in stages if st.stage in ASLEEP_STAGES]
    return [s for s in items if any(a <= s.time < b for a, b in spans)]


# ----- 이상치 -----


def in_range(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def valid_heart(items: list[Sample]) -> list[Sample]:
    """30~220bpm 밖 심박 샘플을 뺀다. 하루·수면·운동 심박 모두 이 샘플로 계산한다."""
    return [s for s in items if in_range(s.value, HEART_BPM_RANGE)]


def valid_speed(items: list[Sample]) -> list[Sample]:
    """0~시속 60km 밖 속도 샘플(GPS 튐 등)을 뺀다. 운동 기록은 그대로 둔다."""
    return [s for s in items if 0 <= s.value <= SPEED_MPS_MAX]


def valid_spo2(value: float) -> bool:
    return in_range(value, SPO2_RANGE)


def valid_body(metric: str, value: float) -> bool:
    bounds = BODY_RANGES.get(metric)
    return bounds is None or in_range(value, bounds)


def non_negative(value: float | None) -> float | None:
    """칼로리·거리 음수는 기기 오류로 보고 뺀다 (None)."""
    return value if value is None or value >= 0 else None


def exercise_duration_ok(code: object, start: datetime, end: datetime) -> bool:
    """운동 시간이 1분 이상, 운동 종류별 상한(기본 6시간) 이하인지."""
    limit = EXERCISE_MAX_BY_CODE.get(code, EXERCISE_MAX_DEFAULT) if isinstance(code, int) else EXERCISE_MAX_DEFAULT
    return EXERCISE_MIN <= end - start <= limit


def heart_stats(items: list[Sample]) -> dict:
    """심박 평균·최저·최고·개수. 평균은 모든 샘플의 단순 평균이다.

    운동 중·수면 중처럼 한 상황 안의 샘플에 쓴다. 하루 평균은 daily_heart_avg를 쓴다.
    """
    if not items:
        return {"avg": None, "min": None, "max": None, "count": 0}
    values = [s.value for s in items]
    return {
        "avg": round(sum(values) / len(values), 1),
        "min": round(min(values)),
        "max": round(max(values)),
        "count": len(values),
    }


def _by_hour(items: list[Sample]) -> dict[int, list[Sample]]:
    by_hour: dict[int, list[Sample]] = {}
    for s in items:
        by_hour.setdefault(s.time.astimezone(KST).hour, []).append(s)
    return by_hour


def hourly_heart(items: list[Sample]) -> dict | None:
    """0~23시 시간별 {avg, min, max, count}. 샘플이 있는 시간만 넣는다."""
    by_hour = _by_hour(items)
    if not by_hour:
        return None
    return {str(h): heart_stats(v) for h, v in sorted(by_hour.items())}


def daily_heart_avg(items: list[Sample]) -> float | None:
    """하루 심박 평균 = 샘플이 있는 시간대마다 평균을 내고, 그 평균들을 다시 평균한다 (7.6).

    운동 중 연속 측정은 1초 간격이라 샘플이 평소 측정보다 훨씬 많다 (실데이터에서 약 99%).
    모든 샘플을 그대로 평균하면 하루 평균이 연속 측정 구간의 평균이 되므로, 1시간마다 같은 무게를 준다.
    """
    by_hour = _by_hour(items)
    if not by_hour:
        return None
    hour_avgs = [sum(s.value for s in v) / len(v) for v in by_hour.values()]
    return round(sum(hour_avgs) / len(hour_avgs), 1)


# ----- 활동 -----


@dataclass(frozen=True)
class StepRecord:
    start: datetime
    end: datetime | None
    count: float
    # 하루 누적값이 갱신된 버전들 (갱신 시각, 그때 누적값). step_snapshot
    versions: tuple[tuple[datetime, float], ...] = ()


def hourly_from_versions(day: date, versions: Sequence[tuple[datetime, float]]) -> list[int]:
    """누적값 버전 → 24칸. 버전 사이 늘어난 걸음을 갱신 시각의 시간대(KST)에 넣는다 (7.3).

    그날 첫 버전은 0에서 늘어난 것으로 본다. 다음 날 자정 뒤 갱신은 23시에 넣는다.
    누적값이 줄어든 버전은 실데이터에서 없었지만, 생기면 그 차이는 버린다.
    """
    hours = [0] * 24
    prev = 0.0
    for t, count in sorted(versions):
        diff = count - prev
        if diff > 0:
            t_day = kst_date(t)
            hour = 23 if t_day > day else 0 if t_day < day else t.astimezone(KST).hour
            hours[hour] += round(diff)
        prev = max(prev, count)
    return hours


def hourly_steps(records: list[StepRecord], day: date) -> list[int] | None:
    """그날 걸음 기록 → 0~23시 24칸.

    - 1시간 이하 기록: 시작 시각의 시간대에 넣는다
    - 하루 단위처럼 1시간보다 긴 기록: 누적값 버전 사이 차이로 나눈다. 버전이 없으면 나눌 수 없어 NULL
    - 24칸 합이 하루 걸음과 다르면(버전이 빠진 경우 등) 믿을 수 없어 NULL
    """
    if not records:
        return None
    hours = [0] * 24
    for r in records:
        if r.end is not None and r.end - r.start > timedelta(hours=1):
            if not r.versions:
                return None
            split = hourly_from_versions(day, r.versions)
            if sum(split) != round(r.count):
                return None
            hours = [a + b for a, b in zip(hours, split, strict=True)]
        else:
            hours[r.start.astimezone(KST).hour] += round(r.count)
    return hours


def number(payload: dict, key: str) -> float | None:
    v = payload.get(key)
    return float(v) if isinstance(v, int | float) else None


def total(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


class _FromApp(Protocol):
    app: str


A = TypeVar("A", bound=_FromApp)


def one_source(records: Sequence[A]) -> list[A]:
    """출처가 여럿이면 하나만 남긴다 (7.1). 출처끼리 더하면 같은 걸음·거리를 두 번 센다.

    삼성 헬스가 있으면 삼성 헬스, 없으면 기록이 가장 많은 출처 (같으면 앱 이름 순).
    """
    counts: dict[str, int] = {}
    for r in records:
        counts[r.app] = counts.get(r.app, 0) + 1
    if len(counts) <= 1:
        return list(records)
    chosen = PRIMARY_APP if PRIMARY_APP in counts else min(counts, key=lambda app: (-counts[app], app))
    return [r for r in records if r.app == chosen]


# ----- 수면 -----


@dataclass(frozen=True)
class Stage:
    start: datetime
    end: datetime
    stage: int


def stages(payload: dict) -> list[Stage]:
    result = []
    for s in payload.get("stages") or []:
        if not isinstance(s, dict):
            continue
        start, end, stage = parse_time(s.get("startTime")), parse_time(s.get("endTime")), s.get("stage")
        if start and end and isinstance(stage, int) and end > start:
            result.append(Stage(start, end, stage))
    return sorted(result, key=lambda s: s.start)


def stage_minutes(items: list[Stage]) -> dict[str, int | None]:
    """단계별 분. 단계 목록이 없으면 모두 NULL, 있으면 없는 단계는 0."""
    if not items:
        return dict.fromkeys(STAGE_COLUMNS)
    seconds = dict.fromkeys(STAGE_COLUMNS, 0.0)
    for s in items:
        seconds[STAGE_NAMES.get(s.stage, "unknown")] += (s.end - s.start).total_seconds()
    return {k: round(v / 60) for k, v in seconds.items()}


def sleep_minutes(payload: dict, items: list[Stage]) -> int | None:
    """sleep_duration_total. 문자열 "null"이면 단계 2·4·5·6 길이의 합. 단계도 없으면 NULL."""
    from_total = parse_duration_minutes(payload.get("sleep_duration_total"))
    if from_total is not None:
        return from_total
    asleep = [s for s in items if s.stage in ASLEEP_STAGES]
    if not asleep:
        return None
    return round(sum((s.end - s.start).total_seconds() for s in asleep) / 60)


def sleep_timeline(items: list[Stage]) -> list[dict] | None:
    if not items:
        return None
    return [
        {
            "start": s.start.astimezone(KST).isoformat(),
            "end": s.end.astimezone(KST).isoformat(),
            "stage": STAGE_NAMES.get(s.stage, "unknown"),
        }
        for s in items
    ]


def spo2_low_times(samples: list[Sample]) -> list[datetime] | None:
    """수면 중 90% 미만 측정 시각, 이른 순 (7.5, S15P31A403-302).

    spo2_stats의 low_count와 같은 샘플·같은 기준으로 고른다. 그래서 길이가 항상 low_count와 같다.
    같은 시각 측정이 둘이면 둘 다 둔다 (개수에도 두 번 들어간다). 측정이 없으면 low_count처럼 NULL이다.
    """
    if not samples or SPO2_LOW_THRESHOLD is None:
        return None
    return sorted(s.time for s in samples if s.value < SPO2_LOW_THRESHOLD)


def spo2_stats(values: list[float]) -> dict:
    """수면 중 산소포화도. 측정이 없으면 개수까지 모두 NULL."""
    if not values:
        return {"count": None, "avg": None, "min": None, "max": None, "low_count": None}
    low = None if SPO2_LOW_THRESHOLD is None else sum(1 for v in values if v < SPO2_LOW_THRESHOLD)
    # 이진 부동소수점 누적으로 평균이 반올림된 최저·최고 밖으로 나가지 않게 한다.
    decimals = [Decimal(str(value)) for value in values]
    precision = Decimal("0.1")
    return {
        "count": len(values),
        "avg": float((sum(decimals) / len(decimals)).quantize(precision, rounding=ROUND_HALF_UP)),
        "min": float(min(decimals).quantize(precision, rounding=ROUND_HALF_UP)),
        "max": float(max(decimals).quantize(precision, rounding=ROUND_HALF_UP)),
        "low_count": low,
    }


class _Timed(Protocol):
    start_at: datetime
    end_at: datetime


T = TypeVar("T", bound=_Timed)


def merge_sleeps(sessions: Sequence[T], gap: timedelta = SLEEP_MERGE_GAP) -> list[list[T]]:
    """시작 순으로 놓고, 앞 수면이 끝난 뒤 gap 안에 시작한 수면을 같은 밤으로 묶는다 (7.5).

    새벽에 잠깐 깼다가 다시 자면 수면이 둘로 나뉘어 온다. 묶는 기준은 날짜가 아니라 시간 간격이다.
    자정 전에 끝난 조각과 자정 뒤에 시작한 조각도 간격이 짧으면 같은 밤이다.
    """
    groups: list[list[T]] = []
    group_end: datetime | None = None
    for s in sorted(sessions, key=lambda s: s.start_at):
        if group_end is not None and s.start_at - group_end <= gap:
            groups[-1].append(s)
            group_end = max(group_end, s.end_at)
        else:
            groups.append([s])
            group_end = s.end_at
    return groups


def gap_stages(sessions: Sequence[_Timed]) -> list[Stage]:
    """묶은 수면 조각 사이의 빈 시간. 깨어 있던 시간으로 본다."""
    result = []
    ordered = sorted(sessions, key=lambda s: s.start_at)
    end = ordered[0].end_at
    for s in ordered[1:]:
        if s.start_at > end:
            result.append(Stage(end, s.start_at, GAP_STAGE))
        end = max(end, s.end_at)
    return result


def pick_main_sleep(sessions: list[dict]) -> str | None:
    """같은 날짜 수면(묶은 덩어리) 중 주 수면의 data_id. 세션이 가장 긴 것, 같으면 먼저 시작한 것."""
    if not sessions:
        return None
    return max(sessions, key=lambda s: (s["session_minutes"], -s["start_at"].timestamp()))["data_id"]
