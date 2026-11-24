"""골든셋용 경계 상황 페르소나 (S15P31A403-163). 정한 날짜에 정한 상황이 생기고, 집계까지 그대로 남는지 본다."""

import uuid
from datetime import date, timedelta

import psycopg
import pytest

from candiy_pipeline.aggregator import AGGREGATE_TABLES, aggregate_member, dates_to_aggregate
from candiy_pipeline.config import load_settings
from candiy_pipeline.loader import sync_metric
from candiy_pipeline.metrics import METRICS
from candiy_pipeline.records import KST, parse_record
from candiy_pipeline.synthetic import (
    EDGE_PERSONAS,
    PERSONA_BY_ID,
    PERSONAS,
    SPECIAL_KINDS,
    UNKNOWN_CODE,
    SyntheticClient,
    _at,
    generate,
    main,
)
from candiy_pipeline.targets import Target

TODAY = date(2026, 10, 22)
NOW = _at(TODAY, 21)
EDGE = PERSONA_BY_ID["synth-edge"]


def day(kind: str) -> date:
    """그 상황의 가장 이른 날짜."""
    return min(TODAY - timedelta(days=ago) for ago, k in EDGE.special_days.items() if k == kind)


@pytest.fixture(scope="module")
def records():
    return {m: [parse_record(r) for r in rs] for m, rs in generate(EDGE, TODAY, NOW).items()}


def on(rs, d: date, *, by_end: bool = False):
    return [r for r in rs if (((r.end_at or r.start_at) if by_end else r.start_at).astimezone(KST).date() == d)]


def test_edge_persona_is_not_in_default_people():
    assert EDGE in EDGE_PERSONAS and EDGE not in PERSONAS
    assert set(EDGE.special_days.values()) == SPECIAL_KINDS
    assert all(not p.special_days for p in PERSONAS)


def test_default_run_skips_edge_persona(monkeypatch):
    # --people 없이 실행하면 기본 10명만 넣는다 (개발 서버 매일 갱신 대상)
    loaded = []
    monkeypatch.setattr("candiy_pipeline.synthetic.generate", lambda p, *a, **k: loaded.append(p.user_id) or {})
    monkeypatch.setattr("candiy_pipeline.synthetic.psycopg.connect", lambda *a, **k: (_ for _ in ()).throw(SystemExit))
    monkeypatch.setenv("APP_ENV", "local")
    with pytest.raises(SystemExit):
        main([])
    assert loaded == [p.user_id for p in PERSONAS]


def test_same_seed_same_records():
    assert generate(EDGE, TODAY, NOW) == generate(EDGE, TODAY, NOW)


def test_no_record_day_has_nothing(records):
    # 기록 없는 날이 이틀 이어져, 앞날은 다음 날 밤에 잠든 심박도 없이 완전히 비어 있다
    d = day("no_record")
    assert d + timedelta(days=1) == max(
        TODAY - timedelta(days=a) for a, k in EDGE.special_days.items() if k == "no_record"
    )
    assert all(not on(rs, d) and not on(rs, d, by_end=True) for rs in records.values())


def test_zero_and_null_steps(records):
    zero = on(records["steps"], day("zero_steps"))
    assert [r.payload["count"] for r in zero] == [0]
    assert not on(records["steps"], day("null_steps"))
    assert len(on(records["exercise_sessions"], day("null_steps"))) == 1  # 운동이 있어 활동 행은 생긴다


def test_nap_only_day(records):
    naps = on(records["sleeps"], day("nap_only"), by_end=True)
    assert len(naps) == 1
    assert 13 <= naps[0].start_at.astimezone(KST).hour <= 15


def test_forced_workouts(records):
    slow = on(records["exercise_sessions"], day("slow_walk_only"))
    unknown = on(records["exercise_sessions"], day("unknown_exercise"))
    assert [r.payload["exercise_type"] for r in slow] == [79]
    assert [r.payload["exercise_type"] for r in unknown] == [UNKNOWN_CODE]


def test_low_spo2_night(records):
    d = day("low_spo2")
    night = on(records["sleeps"], d, by_end=True)
    values = [
        r.payload["percentage"]
        for r in records["oxygen_saturations"]
        if any(n.start_at <= r.start_at <= n.end_at for n in night)
    ]
    assert sorted(v for v in values if v < 90) == [87.5, 89.9]
    assert len(values) == 6 and sum(values) / len(values) >= 90  # 평균은 정상, 측정 두 번만 낮다


@pytest.mark.db
def test_aggregates_keep_edge_cases():
    """적재 → 집계 뒤에도 경계 상황이 사유별로 남는다 (조회의 NO_RECORD·NULL_VALUE·ZERO_STEPS 구분)."""
    target = Target(member_id=uuid.uuid4(), candiy_id=EDGE.user_id)
    client = SyntheticClient({EDGE.user_id: generate(EDGE, TODAY, NOW, days=14)})
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            for metric in METRICS:
                assert sync_metric(conn, client, target, metric, TODAY, full=True).status == "success"
            aggregate_member(conn, target.member_id, dates_to_aggregate(conn, target.member_id, all_dates=True))

            def one(sql: str, d: date):
                return conn.execute(sql, (target.member_id, d)).fetchall()

            activity = (
                "SELECT steps, exercise_count, exercise_minutes FROM activity_daily "
                "WHERE end_user_id = %s AND date = %s"
            )
            assert one(activity, day("no_record")) == []
            assert one(activity, day("zero_steps")) == [(0, 0, 0)]
            assert one(activity, day("null_steps"))[0][0] is None
            assert one(activity, day("slow_walk_only"))[0][1:] == (0, 0)

            assert one(
                "SELECT counts_as_exercise FROM exercise_session WHERE end_user_id = %s AND date = %s",
                day("slow_walk_only"),
            ) == [(False,)]
            assert one(
                "SELECT code, counts_as_exercise FROM exercise_session WHERE end_user_id = %s AND date = %s",
                day("unknown_exercise"),
            ) == [(UNKNOWN_CODE, True)]

            sleep = "SELECT is_main, spo2_low_count FROM sleep_session WHERE end_user_id = %s AND date = %s"
            assert one(sleep, day("nap_only"))[0][0] is True  # 쪽잠만 잔 날은 그 쪽잠이 주 수면
            assert one(sleep, day("low_spo2")) == [(True, 2)]
        finally:
            for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
                conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (target.member_id,))
