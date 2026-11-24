"""합성 데이터 생성기 (S15P31A403-46). 생성 결과가 실데이터 특성과 계산 규칙에 맞는지 본다."""

import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta

import psycopg
import pytest

from candiy_pipeline import rules
from candiy_pipeline.aggregator import AGGREGATE_TABLES, aggregate_member, dates_to_aggregate
from candiy_pipeline.config import load_settings
from candiy_pipeline.loader import sync_metric
from candiy_pipeline.metrics import METRICS
from candiy_pipeline.records import KST, parse_record
from candiy_pipeline.synthetic import (
    DEMO_PERSONAS,
    EDGE_PERSONAS,
    PERSONA_BY_ID,
    PERSONAS,
    PROFILES,
    SYNTHETIC_DAYS,
    SyntheticClient,
    _after_workout,
    _arc_day,
    _at,
    _delete_stale_members,
    _human_day,
    _plan_day,
    _upsert_user,
    generate,
)
from candiy_pipeline.targets import SYNTHETIC_SOCIAL_PREFIX, Target, load_targets

TODAY = date(2026, 10, 20)
NOW = _at(TODAY, 21)


@pytest.fixture(scope="module")
def generated():
    return {p.user_id: generate(p, TODAY, NOW) for p in PERSONAS}


def parsed(records):
    return [parse_record(r) for r in records]


def test_same_seed_same_records():
    p = PERSONA_BY_ID["synth-02"]
    assert generate(p, TODAY, NOW) == generate(p, TODAY, NOW)


def test_records_parse_and_nothing_after_now(generated):
    for by_metric in generated.values():
        assert set(by_metric) <= set(METRICS)
        for records in by_metric.values():
            for r in parsed(records):
                assert r.last_modified_at <= NOW


def test_step_versions_only_increase(generated):
    for by_metric in generated.values():
        versions = defaultdict(list)
        for r in parsed(by_metric["steps"]):
            versions[r.data_id].append((r.last_modified_at, r.payload["count"]))
        for items in versions.values():
            counts = [c for _, c in sorted(items)]
            assert counts == sorted(counts) and len(set(counts)) == len(counts)


def test_sleep_rules(generated):
    for by_metric in generated.values():
        nights = sorted(parsed(by_metric["sleeps"]), key=lambda r: r.start_at)
        for r in nights:
            asleep = rules.parse_duration_minutes(r.payload["sleep_duration_total"])
            # 실제 수면이 세션보다 길면 이상치다 (calculation-rules.md 8장). 나뉜 수면의 뒤 조각은 같을 수 있다
            assert asleep <= rules.minutes_between(r.start_at, r.end_at)
        for a, b in zip(nights, nights[1:], strict=False):
            # 나뉜 수면은 60분 안에 다시 잠들어 한 밤으로 묶인다. 서로 다른 밤은 그보다 멀다
            gap = b.start_at - a.end_at
            assert gap > timedelta(0)
            if gap <= rules.SLEEP_MERGE_GAP:
                assert gap >= timedelta(minutes=20)


def test_spo2_only_during_sleep(generated):
    for by_metric in generated.values():
        sleeps = parsed(by_metric["sleeps"])
        for r in parsed(by_metric["oxygen_saturations"]):
            assert any(s.start_at <= r.start_at <= s.end_at for s in sleeps)


def test_exercise_within_outlier_limits(generated):
    for by_metric in generated.values():
        for r in parsed(by_metric.get("exercise_sessions", [])):
            assert rules.exercise_duration_ok(r.payload["exercise_type"], r.start_at, r.end_at)


def walk_kmh(by_metric):
    """걷기(79) 세션마다 평균 속도 (km/h)."""
    speeds = [s for r in parsed(by_metric["speeds"]) for s in rules.samples(r.payload, "speed_mps")]
    result = []
    for ex in parsed(by_metric["exercise_sessions"]):
        if ex.payload["exercise_type"] == 79:
            inside = [s.value for s in rules.within(speeds, ex.start_at, ex.end_at)]
            result.append(sum(inside) / len(inside) * 3.6)
    return result


def test_walking_speed_personas(generated):
    # 운동 안 함(04)의 걷기는 시속 4km 미만, 모범형(01)의 걷기는 그 이상 (score-rules.md 2.3)
    assert walk_kmh(generated["synth-04"]) and max(walk_kmh(generated["synth-04"])) < 4.0
    assert walk_kmh(generated["synth-01"]) and min(walk_kmh(generated["synth-01"])) >= 4.0


def test_unsynced_persona_has_gap(generated):
    # 09: 최근 2일은 동기화되지 않았고, 워치를 안 찬 날은 수면이 없다
    by_metric = generated["synth-09"]
    days_with_steps = {r.start_at.date() for r in parsed(by_metric["steps"])}
    assert TODAY not in days_with_steps and TODAY - timedelta(days=1) not in days_with_steps
    assert len({r.end_at.date() for r in parsed(by_metric["sleeps"])}) < 30


def test_body_only_for_athlete(generated):
    assert "weights" in generated["synth-05"]
    assert all("weights" not in v for k, v in generated.items() if k != "synth-05")


@pytest.mark.db
def test_load_and_aggregate():
    """적재 → 집계까지 돌려 시간대별 걸음 합이 하루 걸음과 맞는지 본다."""
    p = PERSONA_BY_ID["synth-02"]
    target = Target(member_id=uuid.uuid4(), candiy_id=p.user_id)
    client = SyntheticClient({p.user_id: generate(p, TODAY, NOW, days=7)})
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            for metric in METRICS:
                assert sync_metric(conn, client, target, metric, TODAY, full=True).status == "success"
            aggregate_member(conn, target.member_id, dates_to_aggregate(conn, target.member_id, all_dates=True))
            days, matched = conn.execute(
                "SELECT count(*), count(*) FILTER (WHERE (SELECT sum(h) FROM unnest(hourly_steps) h) = steps) "
                "FROM activity_daily WHERE end_user_id = %s",
                (target.member_id,),
            ).fetchone()
            assert days == 7 and matched == 7
            scored, totals = conn.execute(
                "SELECT count(*), count(total_score) FROM lifestyle_score WHERE end_user_id = %s",
                (target.member_id,),
            ).fetchone()
            assert scored == 7 and totals == 7  # 걸음이 매일 있어 오늘의 점수가 모든 날 나온다
            sleeps = conn.execute(
                "SELECT count(*) FROM sleep_session WHERE end_user_id = %s AND is_main", (target.member_id,)
            ).fetchone()[0]
            assert sleeps >= 6  # 첫날 밤은 조회 기간 앞에서 시작해 빠질 수 있다
        finally:
            for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
                conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (target.member_id,))


def test_profiles_fit_users_constraints():
    # users 테이블 CHECK와 맞는지 (닉네임 1~12자, 성별, 태어난 연도, 키·몸무게 범위)
    assert set(PROFILES) == set(PERSONA_BY_ID)
    for p in PROFILES.values():
        assert 1 <= len(p.display_name) <= 12
        assert p.gender in ("MALE", "FEMALE")
        assert 1900 <= p.birth_year <= TODAY.year
        assert 0 < p.height_cm <= 300 and 0 < p.weight_kg <= 500


@pytest.mark.db
def test_synthetic_users_are_not_candiy_targets(monkeypatch):
    """합성 회원은 users에 들어가지만 평소 파이프라인의 조회 대상에서는 빠진다."""
    # 적재해 둔 합성 회원(같은 회원 ID·social_user_id)과 겹치지 않게 테스트 전용 ID로 synth-03 프로필을 쓴다
    p = replace(PERSONA_BY_ID["synth-03"], user_id=f"synth-test-{uuid.uuid4().hex[:8]}")
    monkeypatch.setitem(PROFILES, p.user_id, PROFILES["synth-03"])
    member = uuid.uuid4()
    real = uuid.uuid4()
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            _upsert_user(conn, p, member)
            _upsert_user(conn, p, member)  # 다시 넣어도 한 행
            conn.execute(
                "INSERT INTO users (id, social_type, social_user_id, display_name, birth_year, gender, height_cm, "
                "weight_kg) VALUES (%s, 'KAKAO', 'real-test-user', '테스트', 2000, 'MALE', 170, 60)",
                (real,),
            )
            row = conn.execute("SELECT social_user_id, display_name FROM users WHERE id = %s", (member,)).fetchone()
            assert row == (SYNTHETIC_SOCIAL_PREFIX + p.user_id, PROFILES[p.user_id].display_name)
            ids = {t.member_id for t in load_targets(conn, ())}
            assert real in ids and member not in ids
        finally:
            conn.execute("DELETE FROM users WHERE id = ANY(%s)", ([member, real],))


@pytest.mark.db
def test_stale_synthetic_member_is_replaced(monkeypatch):
    """회원 ID 규칙이 바뀌어도(uuid5 → UUIDv4) 같은 합성 회원 이름의 예전 행을 지우고 새 ID로 넣는다."""
    p = replace(PERSONA_BY_ID["synth-03"], user_id=f"synth-test-{uuid.uuid4().hex[:8]}")
    monkeypatch.setitem(PROFILES, p.user_id, PROFILES["synth-03"])
    old, new = uuid.uuid5(uuid.NAMESPACE_URL, p.user_id), uuid.uuid4()
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            _upsert_user(conn, p, old)
            conn.execute(
                "INSERT INTO sync_state (end_user_id, metric) VALUES (%s, 'steps')",
                (old,),
            )
            _delete_stale_members(conn, p, new)
            _upsert_user(conn, p, new)
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM users WHERE social_user_id = %s", (SYNTHETIC_SOCIAL_PREFIX + p.user_id,)
                )
            ]
            assert ids == [new]
            assert conn.execute("SELECT count(*) FROM sync_state WHERE end_user_id = %s", (old,)).fetchone()[0] == 0
            _delete_stale_members(conn, p, new)  # 지울 것이 없으면 그대로
            assert conn.execute("SELECT count(*) FROM users WHERE id = %s", (new,)).fetchone()[0] == 1
        finally:
            conn.execute("DELETE FROM sync_state WHERE end_user_id = ANY(%s)", ([old, new],))
            conn.execute("DELETE FROM users WHERE id = ANY(%s)", ([old, new],))


# ----- 사람다운 흐트러짐 (S15P31A403-235) -----


def _days(n):
    return [TODAY - timedelta(days=i) for i in range(n)]


def test_human_is_reproducible_and_differs_from_classic():
    p = PERSONA_BY_ID["synth-01"]
    human = generate(p, TODAY, NOW, SYNTHETIC_DAYS, human=True)
    assert human == generate(p, TODAY, NOW, SYNTHETIC_DAYS, human=True)
    assert human != generate(p, TODAY, NOW, SYNTHETIC_DAYS)


def test_human_has_late_nights_skipped_weeks_and_sick_days_in_90_days():
    days = _days(SYNTHETIC_DAYS)
    for p in PERSONAS:
        hs = [_human_day(p, d) for d in days]
        assert 1 <= sum(h.late_hours > 0 for h in hs) <= 25, p.user_id
        assert any(h.busy_week for h in hs), p.user_id
    # 모범형은 흐트러짐이 작다
    loose = {p.user_id: sum(_human_day(p, d).late_hours > 0 for d in days) for p in PERSONAS}
    assert loose["synth-01"] < max(loose.values())


def test_human_late_night_moves_bedtime_and_shortens_sleep():
    p = PERSONA_BY_ID["synth-02"]
    late = next(d for d in _days(SYNTHETIC_DAYS) if _human_day(p, d).late_hours and d.weekday() < 5)
    classic, human = _plan_day(p, late), _plan_day(p, late, human=True)
    if classic.worn and classic.sleeps:
        assert human.sleeps[0].start > classic.sleeps[0].start + timedelta(hours=1)


def test_busy_week_or_sick_day_skips_more_workouts():
    p = PERSONA_BY_ID["synth-01"]
    days = _days(SYNTHETIC_DAYS)
    classic = sum(len(_plan_day(p, d).workouts) for d in days)
    human = sum(len(_plan_day(p, d, human=True).workouts) for d in days)
    assert human < classic


def test_edge_persona_has_no_human_variation():
    edge = EDGE_PERSONAS[0]
    assert generate(edge, TODAY, NOW, 14, human=True) == generate(edge, TODAY, NOW, 14)


# ----- 시연 페르소나 synth-11 (S15P31A403-304) -----

DEMO = PERSONA_BY_ID["synth-11"]


def _low_spo2_nights(today, lo, hi):
    """기준일 lo~hi-1일 전에 깬 수면 중 90% 미만 측정이 있는 밤 수."""
    return sum(_arc_day(DEMO, today - timedelta(days=a), today).low_spo2 > 0 for a in range(lo, hi))


def test_demo_persona_only_on_request():
    # 기본 실행·매일 갱신·골든셋에 들어가지 않고, --people·--member로만 넣는다
    assert DEMO in DEMO_PERSONAS and DEMO not in PERSONAS + EDGE_PERSONAS
    assert DEMO.user_id in PROFILES
    assert all(p.arc is None for p in PERSONAS + EDGE_PERSONAS)


@pytest.mark.parametrize("offset", range(0, 45, 4))
def test_overwork_low_spo2_nights_increase(offset):
    # 기준일이 바뀌어도: 평소(본인 기준 28일 포함)엔 드물고, 최근 1주에 반복된다.
    # 최근 14일(signals)에 3밤 이상이라 반복 신호에 걸린다 (11.5)
    today = TODAY + timedelta(days=offset)
    normal, busy, worse = (_low_spo2_nights(today, *r) for r in ((14, 42), (7, 14), (0, 7)))
    assert normal <= 1 and worse >= 3 and busy < worse and busy + worse >= 3
    # 주말에 길게 잔 밤에도 있다
    weekends = [today - timedelta(days=a) for a in range(7) if (today - timedelta(days=a)).weekday() >= 5]
    assert all(_arc_day(DEMO, d, today).low_spo2 > 0 for d in weekends)


def test_overwork_sleep_shortens_and_worsens():
    def avg(values):
        return sum(values) / len(values)

    def weekday_nights(lo, hi):
        days = [TODAY - timedelta(days=a) for a in range(lo, hi) if (TODAY - timedelta(days=a)).weekday() < 5]
        return [_plan_day(DEMO, d, human=True, arc=_arc_day(DEMO, d, TODAY)) for d in days]

    def asleep(plan, stages=(4, 5, 6)):
        return sum((b - a).total_seconds() / 3600 for n in plan.sleeps for a, b, k in n.stages if k in stages)

    normal, worse = weekday_nights(14, 42), weekday_nights(0, 7)
    assert avg([asleep(p) for p in normal]) - avg([asleep(p) for p in worse]) > 1.5  # 7시간 안팎 → 5시간대
    assert avg([asleep(p, (5,)) for p in worse]) < avg([asleep(p, (5,)) for p in normal]) * 0.8  # 깊은 잠 감소
    assert avg([asleep(p, (1,)) for p in worse]) > avg([asleep(p, (1,)) for p in normal])  # 깬 시간 증가


def test_overwork_spo2_records_match_story():
    # 90% 미만 측정 수는 이야기대로, 정상 측정은 90% 밑으로 내려가지 않는다 (90% 미만 수가 정확히 맞도록)
    records = [parse_record(r) for r in generate(DEMO, TODAY, NOW, 42, human=True)["oxygen_saturations"]]
    lows = [r for r in records if r.payload["percentage"] < rules.SPO2_LOW_THRESHOLD]
    assert all(r.payload["percentage"] >= 90.5 for r in records if r not in lows)
    arcs = [_arc_day(DEMO, TODAY - timedelta(days=a), TODAY) for a in range(42)]
    assert sum(a.low_spo2 for a in arcs) >= len(lows) >= sum(a.low_spo2 > 0 for a in arcs) > 0
    # 대부분 새벽 3~5시 (KST)
    hours = [r.start_at.astimezone(KST).hour for r in lows]
    assert sum(3 <= h < 5 for h in hours) >= len(hours) * 0.8


def test_overwork_weight_rises_2kg():
    assert _arc_day(DEMO, TODAY - timedelta(days=60), TODAY).weight_delta == 0
    assert _arc_day(DEMO, TODAY, TODAY).weight_delta == pytest.approx(2.0)


def test_overwork_spo2_one_or_two_records_per_night():
    # 실데이터처럼 밤마다 산소포화도 기록 1~2개 (calculation-rules.md 7.5 "기록 빈도", S15P31A403-303)
    days = [TODAY - timedelta(days=a) for a in range(42)]
    nights = {d: _plan_day(DEMO, d, human=True, arc=_arc_day(DEMO, d, TODAY)).sleeps for d in days}
    per_night = {d: 0 for d in days}
    for r in parsed(generate(DEMO, TODAY, NOW, 42, human=True)["oxygen_saturations"]):
        d = next(d for d, s in nights.items() if s[0].start <= r.start_at <= s[-1].end)
        per_night[d] += 1
    assert set(per_night.values()) <= {1, 2}
    assert sum(v == 1 for v in per_night.values()) > len(days) / 2  # 대부분 1개


def test_overwork_spo2_records_stay_same_when_today_moves():
    # 기준일이 지나 이야기 단계가 바뀌어도 밤마다 기록(data_id)이 같아야 매일 갱신 때 덮어써진다.
    # 적재는 기록을 지우지 않아서, 기록 수가 줄면 예전 기록(예전 단계의 90% 미만)이 DB에 남는다
    later = TODAY + timedelta(days=20)
    earlier = {r["data_id"] for r in generate(DEMO, TODAY, NOW, 42, human=True)["oxygen_saturations"]}
    moved = {r["data_id"] for r in generate(DEMO, later, _at(later, 21), 62, human=True)["oxygen_saturations"]}
    assert earlier <= moved


# ----- 운동 다음 밤과 시연 페르소나 synth-12 (S15P31A403-385) -----

LATE_GYM = PERSONA_BY_ID["synth-12"]


def _story_plans(persona, today, days):
    """generate 처럼 날짜 순서대로 운동 다음 밤을 반영한 하루 계획."""
    plans = {}
    for a in range(days - 1, -1, -1):
        d = today - timedelta(days=a)
        arc = _after_workout(persona, _arc_day(persona, d, today), plans.get(d - timedelta(days=1)))
        plans[d] = _plan_day(persona, d, human=True, arc=arc)
    return plans


def _minutes(plan, stages):
    return sum((b - a).total_seconds() / 60 for n in plan.sleeps for a, b, k in n.stages if k in stages)


def _avg(values):
    return sum(values) / len(values)


@pytest.mark.parametrize("offset", range(0, 30, 6))
def test_overwork_no_workouts_in_busy_two_weeks(offset):
    # 야근 2주는 운동을 하지 않는다. 어느 날 시연해도 마지막 운동은 2주보다 앞이다
    today = TODAY + timedelta(days=offset)
    starts = [r["start"][:10] for r in generate(DEMO, today, _at(today, 21), 42, human=True)["exercise_sessions"]]
    assert starts and all((today - date.fromisoformat(s)).days >= 14 for s in starts)


def test_overwork_sleeps_deeper_after_workout():
    # 평소 구간: 운동한 다음 밤은 깊은 잠이 길고 덜 깬다. 잔 시간은 같다
    plans = _story_plans(DEMO, TODAY, 42)
    normal = [d for d in plans if (TODAY - d).days >= 14 and d - timedelta(days=1) in plans]
    after = [plans[d] for d in normal if plans[d - timedelta(days=1)].workouts and plans[d].sleeps]
    rest = [plans[d] for d in normal if not plans[d - timedelta(days=1)].workouts and plans[d].sleeps]
    assert _avg([_minutes(p, (5,)) for p in after]) > _avg([_minutes(p, (5,)) for p in rest]) * 1.2
    assert _avg([_minutes(p, (1,)) for p in after]) < _avg([_minutes(p, (1,)) for p in rest])


def test_late_gym_persona_only_on_request():
    assert LATE_GYM in DEMO_PERSONAS and LATE_GYM not in PERSONAS + EDGE_PERSONAS
    assert LATE_GYM.user_id in PROFILES and LATE_GYM.arc == "late_gym"


def test_late_gym_moves_weekday_workouts_to_night_two_weeks_ago():
    # 2주 전부터 평일 운동은 밤 9시 뒤에 끝나고, 그 전과 주말 운동은 일찍 끝난다
    for d, plan in _story_plans(LATE_GYM, TODAY, 42).items():
        for _, _, end in plan.workouts:
            late = (TODAY - d).days < 14 and d.weekday() < 5
            assert (end.hour >= 21) == late, (d, end)


def test_late_gym_shallow_sleep_only_after_late_workout():
    # 늦은 운동 다음 밤은 깊은 잠만 줄고 잔 시간은 비슷하다. 늦은 운동·이른 운동 밤이 4밤 이상씩 있다 (11.3)
    plans = _story_plans(LATE_GYM, TODAY, 28)
    late, early = [], []
    for d, plan in plans.items():
        prev = plans.get(d - timedelta(days=1))
        if prev and prev.workouts and plan.sleeps:
            (late if max(e for _, _, e in prev.workouts).hour >= 21 else early).append(plan)
    assert len(late) >= 4 and len(early) >= 4
    assert _avg([_minutes(p, (5,)) for p in late]) < _avg([_minutes(p, (5,)) for p in early]) * 0.7
    asleep = (4, 5, 6)
    assert abs(_avg([_minutes(p, asleep) for p in late]) - _avg([_minutes(p, asleep) for p in early])) < 30


# ----- 광고 영상용 synth-13 (S15P31A403-410) -----

AD = PERSONA_BY_ID["synth-13"]
AD_TODAY = date(2026, 11, 16)  # 녹화일. 정한 밤과 비교 평균이 이날 기준이다


def test_ad_persona_only_on_request():
    assert AD in DEMO_PERSONAS and AD not in PERSONAS + EDGE_PERSONAS
    assert AD.user_id in PROFILES and AD.arc == "ad"


@pytest.mark.parametrize(
    ("d", "start", "end", "asleep", "deep"),
    [(date(2026, 11, 6), "01:44", "07:22", 329, 33), (date(2026, 11, 11), "00:56", "06:52", 345, 20)],
)
def test_ad_pinned_nights_exact(d, start, end, asleep, deep):
    # 대사의 잠든 시각·잔 시간·깊은 잠이 분 단위 그대로다
    plan = _story_plans(AD, AD_TODAY, 90)[d]
    night = plan.sleeps[0]
    assert len(plan.sleeps) == 1
    assert night.start.astimezone(KST).strftime("%H:%M") == start
    assert night.end.astimezone(KST).strftime("%H:%M") == end
    assert _minutes(plan, (4, 5, 6)) == asleep and _minutes(plan, (5,)) == deep


@pytest.mark.parametrize(
    ("today", "first"),
    [
        (date(2026, 11, 15), date(2026, 10, 19)),
        (AD_TODAY, date(2026, 10, 20)),
        (date(2026, 11, 17), date(2026, 10, 21)),
    ],
)
def test_ad_exercise_nights_average_matches_lines(today, first):
    # 질문한 날 기준 최근 28일: 운동한 날 밤 444분(7시간 24분), 안 한 날 밤 371분(6시간 11분).
    # 데이터는 11-16 기준으로 멈춰 있으므로 11-17 에는 11-16 까지만 있다. 녹화는 11-15 로 당겨졌다
    plans = _story_plans(AD, AD_TODAY, 90)
    ex, rest = [], []
    for d in (first + timedelta(days=i) for i in range((min(today, AD_TODAY) - first).days + 1)):
        if plans[d].sleeps:
            (ex if plans[d - timedelta(days=1)].workouts else rest).append(_minutes(plans[d], (4, 5, 6)))
    assert round(_avg(ex)) == 444 and round(_avg(rest)) == 371


def test_ad_no_low_spo2_and_one_high_heart_night():
    # 상담 권유가 붙지 않게 90% 미만 산소포화도가 없고, 수면 중 심박은 11-11 밤 하루만 높다
    records = generate(AD, AD_TODAY, _at(AD_TODAY, 21), SYNTHETIC_DAYS, human=True)
    assert all(r.payload["percentage"] >= 90 for r in parsed(records["oxygen_saturations"]))
    plans = _story_plans(AD, AD_TODAY, 90)
    high = plans[date(2026, 11, 11)].sleeps[0]
    samples = [
        s["beatsPerMinute"]
        for r in parsed(records["heart_rates"])
        if high.start <= r.start_at and r.end_at <= high.end
        for s in r.payload["samples"]
    ]
    assert 68 <= _avg(samples) <= 72
    assert all(_arc_day(AD, AD_TODAY - timedelta(days=a), AD_TODAY).sleep_hr_shift == 0 for a in range(90))
