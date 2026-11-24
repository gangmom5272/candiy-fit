"""코칭 골든셋 고정 데이터 내보내기 (S15P31A403-163)."""

import uuid
from datetime import date

import psycopg
import pytest

from candiy_pipeline.aggregator import (
    AGGREGATE_TABLES,
    aggregate_and_mark_ready,
    aggregate_member,
    dates_to_aggregate,
)
from candiy_pipeline.config import load_settings
from candiy_pipeline.golden import SYNC_METRICS, check_synthetic_member, fixture, main, sha256
from candiy_pipeline.loader import sync_metric
from candiy_pipeline.metrics import METRICS
from candiy_pipeline.synthetic import (
    EDGE_PERSONAS,
    PERSONA_BY_ID,
    PERSONAS,
    SYNTHETIC_DAYS,
    SyntheticClient,
    _at,
    generate,
)
from candiy_pipeline.targets import Target, target_for_test_user

TODAY = date(2026, 10, 22)
NOW = _at(TODAY, 21)


def test_sha256_ignores_its_own_field_and_key_order():
    a = {"persona": "synth-01", "steps": [{"date": "2026-10-22", "steps": 0}]}
    assert sha256(a) == sha256({**dict(reversed(a.items())), "data_sha256": "x"})
    assert sha256(a) != sha256({**a, "steps": []})


@pytest.mark.db
def test_fixture_keeps_db_values_as_is():
    edge = PERSONA_BY_ID["synth-edge"]
    target = Target(member_id=uuid.uuid4(), candiy_id=edge.user_id)
    client = SyntheticClient({edge.user_id: generate(edge, TODAY, NOW, days=14)})
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            for metric in METRICS:
                assert sync_metric(conn, client, target, metric, TODAY, full=True).status == "success"
            aggregate_member(conn, target.member_id, dates_to_aggregate(conn, target.member_id, all_dates=True))
            data = fixture(conn, edge.user_id, NOW, target.member_id)
        finally:
            for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
                conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (target.member_id,))

    assert data["user_id"] == str(target.member_id) and data["now"] == "2026-10-22T21:00:00+09:00"
    assert data["data_sha256"] == sha256(data)
    steps = {r["date"]: r["steps"] for r in data["steps"]}
    assert "2026-10-13" not in steps  # 행 없음은 행 없이
    assert steps["2026-10-19"] == 0 and steps["2026-10-20"] is None  # 0보와 값만 빈 날은 그대로
    unknown = [r for r in data["exercise"] if r["date"] == "2026-10-17"]
    assert [(r["exercise_type"], r["type_name"], r["category"]) for r in unknown] == [(9999, None, None)]
    walk = [r for r in data["exercise"] if r["date"] == "2026-10-21"]
    assert walk[0]["type_name"] and walk[0]["counts_as_exercise"] is False
    assert set(data["sync"]) == set(SYNC_METRICS)
    assert all(r["start_at"].endswith("+09:00") for r in data["sleep"])
    # 기존 페르소나 파일은 칸을 늘리지 않는다 (커밋된 고정 데이터·해시가 그대로)
    assert all("avg_heart_rate" not in r and "spo2_low_count" not in r for r in data["sleep"])
    assert all("sample_count" not in r for r in data["heart"])


@pytest.mark.parametrize("app_env", ["", "prod"])
def test_export_is_blocked_outside_local_and_dev(monkeypatch, tmp_path, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(SystemExit):
        main(["--today", "2026-10-22", "--out", str(tmp_path)])
    assert not any(tmp_path.iterdir())


@pytest.mark.db
def test_export_refuses_non_synthetic_member():
    # 합성 회원 ID 자리의 회원이 합성 회원(synthetic-<페르소나>)이 아니면 내보내지 않는다
    member_id = target_for_test_user("synth-01").member_id
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = %s", (member_id,)).fetchone() is None:
            pytest.skip("합성 회원 synth-01이 없는 DB")
        check_synthetic_member(conn, "synth-01")
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE users SET social_user_id = 'google-123' WHERE id = %s", (member_id,))
            with pytest.raises(ValueError, match="합성 회원이 아닙니다"):
                check_synthetic_member(conn, "synth-01")


def _load(conn, persona, member_id, days, human):
    client = SyntheticClient({persona.user_id: generate(persona, TODAY, NOW, days=days, human=human)})
    target = Target(member_id=member_id, candiy_id=persona.user_id)
    for metric in METRICS:
        assert sync_metric(conn, client, target, metric, TODAY, full=True, initial_days=days).status == "success"
    # 기록 시작일(first_data_at)은 집계와 함께 준비 완료를 표시할 때 채워진다 (synthetic.seed_member와 같다)
    aggregate_and_mark_ready(conn, member_id, dates_to_aggregate(conn, member_id, all_dates=True), list(METRICS))


def _cleanup(conn, member_id):
    for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
        conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (member_id,))


@pytest.mark.db
def test_demo_persona_fixture_has_90_days_and_sleep_coaching_fields():
    # 시연 페르소나(synth-11)는 90일 데이터와 수면 코칭 계산에 쓰는 칸을 더 넣는다 (S15P31A403-305)
    demo = PERSONA_BY_ID["synth-11"]
    member_id = uuid.uuid4()
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            _load(conn, demo, member_id, SYNTHETIC_DAYS, human=True)
            data = fixture(conn, demo.user_id, NOW, member_id)
        finally:
            _cleanup(conn, member_id)

    assert data["data_sha256"] == sha256(data)
    assert date.fromisoformat(data["linked_from"]) <= date(2026, 7, 25)  # 기준일 포함 90일
    mains = [r for r in data["sleep"] if r["is_main"]]
    assert len(mains) >= 85
    extra = {"avg_heart_rate", "min_heart_rate", "max_heart_rate", "spo2_count", "spo2_low_count"}
    assert all(extra <= set(r) for r in data["sleep"])
    assert sum(1 for r in mains if r["spo2_low_count"]) >= 6  # 최근 2주 90% 미만 반복 (이야기)
    assert all("sample_count" in r for r in data["heart"])
    # 생활 습관 점수 (S15P31A403-357): 칸은 DB lifestyle_score 와 같고 계산 시각은 기준 시각으로 고정한다
    assert len(data["scores"]) >= 85
    score_columns = {"date", "total_score", "sleep_score", "sleep_grade", "hr_baseline_nights", "check_wear"}
    assert score_columns <= set(data["scores"][0])
    assert {r["computed_at"] for r in data["scores"]} == {data["now"]}


@pytest.mark.db
def test_demo_persona_fixture_refuses_short_data():
    # --classic(35일)으로 넣은 시연 페르소나는 11장 기준 기간이 모자라 내보내지 않는다
    demo = PERSONA_BY_ID["synth-11"]
    member_id = uuid.uuid4()
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            _load(conn, demo, member_id, 35, human=False)
            with pytest.raises(ValueError, match="90일보다 짧습니다"):
                fixture(conn, demo.user_id, NOW, member_id)
        finally:
            _cleanup(conn, member_id)


def test_demo_persona_can_be_chosen_but_is_not_default(monkeypatch, tmp_path, capsys):
    # 시연 페르소나는 --people로 고를 수 있다 (인자 검사를 통과해 환경 검사에서 멈춘다)
    monkeypatch.setenv("APP_ENV", "prod")  # DB에 붙기 전에 멈추게 한다
    with pytest.raises(SystemExit):
        main(["--today", "2026-10-22", "--people", "synth-11", "--out", str(tmp_path)])
    assert "APP_ENV" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--today", "2026-10-22", "--people", "synth-99", "--out", str(tmp_path)])
    assert "invalid choice" in capsys.readouterr().err
    # 기본 실행(--people 없음)은 예전과 같은 11명만
    assert "synth-11" not in [p.user_id for p in PERSONAS + EDGE_PERSONAS]
