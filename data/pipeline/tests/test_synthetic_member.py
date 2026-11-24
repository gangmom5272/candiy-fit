"""기존 로그인 회원의 개발용 샘플 생성: 환경 차단, 격리, 실패 시 롤백."""

import uuid
from contextlib import nullcontext
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg
import pytest

from candiy_pipeline import synthetic
from candiy_pipeline.config import load_settings
from candiy_pipeline.metrics import METRICS

MEMBER = uuid.uuid4()
TODAY = date(2026, 10, 20)
NOW = synthetic._at(TODAY, 21)
PERSONA = synthetic.PERSONA_BY_ID["synth-02"]
ARGS = ["--member", str(MEMBER), "--people", "synth-02"]


@pytest.fixture
def cli(monkeypatch):
    settings = SimpleNamespace(app_env="local", include_members=False, db_dsn="unused")
    monkeypatch.setattr(synthetic, "load_settings", lambda **kwargs: settings)
    connect = MagicMock()
    monkeypatch.setattr(synthetic.psycopg, "connect", connect)
    return settings, connect


def test_lists_registered_personas_without_settings_or_database(cli, monkeypatch, capsys):
    monkeypatch.setattr(synthetic, "load_settings", lambda **kw: pytest.fail("listing must not load settings"))
    extra = SimpleNamespace(user_id="synth-test", name="추가 페르소나")
    monkeypatch.setitem(synthetic.PERSONA_BY_ID, extra.user_id, extra)
    assert synthetic.main(["--list-people"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"{p.user_id}\t{p.name}" for p in synthetic.PERSONA_BY_ID.values()]
    cli[1].assert_not_called()


@pytest.mark.parametrize("persona_id", list(synthetic.PERSONA_BY_ID))
def test_member_can_select_each_registered_persona(cli, monkeypatch, persona_id):
    seed = MagicMock()
    monkeypatch.setattr(synthetic, "seed_member", seed)
    assert synthetic.main(["--member", str(MEMBER), "--people", persona_id]) == 0
    assert seed.call_args.args[1:3] == (MEMBER, synthetic.PERSONA_BY_ID[persona_id])


@pytest.mark.parametrize("environment", ["", "prod", "production", "test", "DEV"])
def test_environment_denied_before_connect(cli, environment):
    settings, connect = cli
    settings.app_env = environment
    with pytest.raises(SystemExit) as error:
        synthetic.main(ARGS)
    assert error.value.code == 2
    connect.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["--member", "bad-id"],
        ["--member", str(MEMBER)],
        ARGS + ["synth-03"],
        ARGS + ["--reset"],
    ],
)
def test_invalid_member_options_do_not_connect(cli, args):
    with pytest.raises(SystemExit):
        synthetic.main(args)
    cli[1].assert_not_called()


def test_real_sync_must_be_disabled(cli):
    cli[0].include_members = True
    with pytest.raises(SystemExit):
        synthetic.main(ARGS)
    cli[1].assert_not_called()


@pytest.mark.parametrize("environment", ["local", "dev"])
def test_member_cli_dispatches_without_profile_upsert(cli, monkeypatch, environment):
    cli[0].app_env = environment
    seed = MagicMock()
    monkeypatch.setattr(synthetic, "seed_member", seed)
    monkeypatch.setattr(synthetic, "_upsert_user", lambda *a: pytest.fail("profile changed"))
    assert synthetic.main(ARGS) == 0
    assert seed.call_args.args[1:3] == (MEMBER, PERSONA)


def test_app_env_reads_file_and_environment_override(tmp_path, monkeypatch):
    env_file = tmp_path / "settings.env"
    env_file.write_text("APP_ENV=prod\n", encoding="utf-8")
    monkeypatch.delenv("APP_ENV", raising=False)
    assert load_settings(env_file, require_candiy=False).app_env == "prod"
    monkeypatch.setenv("APP_ENV", "local")
    assert load_settings(env_file, require_candiy=False).app_env == "local"


@pytest.fixture
def member_db(monkeypatch):
    conn = MagicMock()
    # 회원 있음, 다른 출처 없음, 기존 합성 기록 없음(처음 넣기 → 90일)
    conn.execute.return_value.fetchone.side_effect = [(MEMBER,), (False,), (None,)]
    monkeypatch.setattr(synthetic, "member_lock", lambda *a: nullcontext(True))
    return conn


@pytest.mark.parametrize("rows", [[None], [(MEMBER,), (True,)]])
def test_missing_or_mixed_member_has_no_writes(member_db, monkeypatch, rows):
    member_db.execute.return_value.fetchone.side_effect = rows
    monkeypatch.setattr(synthetic, "generate", lambda *a: pytest.fail("must stop before generating"))
    with pytest.raises(ValueError):
        synthetic.seed_member(member_db, MEMBER, PERSONA, TODAY, NOW)
    assert all(call.args[0].startswith("SELECT") for call in member_db.execute.call_args_list)


def test_busy_member_has_no_writes(member_db, monkeypatch):
    monkeypatch.setattr(synthetic, "member_lock", lambda *a: nullcontext(False))
    with pytest.raises(ValueError, match="진행 중"):
        synthetic.seed_member(member_db, MEMBER, PERSONA, TODAY, NOW)
    member_db.execute.assert_not_called()


def test_loads_authenticated_id_and_marks_ready(member_db, monkeypatch):
    calls = []

    def sync(conn, client, target, metric, today, **kwargs):
        assert target.member_id == MEMBER
        assert target.candiy_id == PERSONA.user_id
        for record in client.fetch(metric, target.candiy_id, TODAY, TODAY):
            assert record["app"] == "dev.candiy-fit.synthetic.synth-02"
        calls.append(metric)
        return SimpleNamespace(status="success")

    monkeypatch.setattr(synthetic, "sync_metric", sync)
    monkeypatch.setattr(synthetic, "dates_to_aggregate", lambda *a, **kw: {TODAY})
    aggregate = MagicMock(return_value={"activity_daily": 1})
    monkeypatch.setattr(synthetic, "aggregate_and_mark_ready", aggregate)
    synthetic.seed_member(member_db, MEMBER, PERSONA, TODAY, NOW)
    assert calls == list(METRICS)
    aggregate.assert_called_once_with(member_db, MEMBER, {TODAY}, list(METRICS))


@pytest.mark.parametrize("stage", ["load", "aggregate"])
def test_failure_exits_outer_transaction_with_exception(member_db, monkeypatch, stage):
    monkeypatch.setattr(
        synthetic,
        "sync_metric",
        lambda *a, **kw: SimpleNamespace(status="fail" if stage == "load" else "success", error="test_failure"),
    )
    monkeypatch.setattr(synthetic, "dates_to_aggregate", lambda *a, **kw: {TODAY})
    monkeypatch.setattr(synthetic, "aggregate_and_mark_ready", MagicMock(side_effect=RuntimeError("aggregate failed")))
    with pytest.raises((ValueError, RuntimeError)):
        synthetic.seed_member(member_db, MEMBER, PERSONA, TODAY, NOW)
    assert member_db.transaction.return_value.__exit__.call_args.args[0] in (ValueError, RuntimeError)


@pytest.mark.db
def test_member_seed_preserves_account_isolates_and_repeats(monkeypatch):
    """Alembic을 적용한 테스트 전용 DB에서 저장·집계·중복 방지·롤백을 확인한다."""
    member, other = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            for target in (member, other):
                conn.execute(
                    "INSERT INTO users (id, social_type, social_user_id, display_name, birth_year, gender, "
                    "height_cm, weight_kg) VALUES (%s, 'GOOGLE', %s, '개발테스트', 2000, 'MALE', 170, 60)",
                    (target, f"member-seed-test-{target}"),
                )
            before = conn.execute("SELECT * FROM users WHERE id = %s", (member,)).fetchone()
            synthetic.seed_member(conn, member, PERSONA, TODAY, NOW)
            count = conn.execute("SELECT count(*) FROM raw_health_record WHERE end_user_id = %s", (member,)).fetchone()[
                0
            ]
            assert count > 0
            synthetic.seed_member(conn, member, PERSONA, TODAY, NOW)
            assert (
                conn.execute("SELECT count(*) FROM raw_health_record WHERE end_user_id = %s", (member,)).fetchone()[0]
                == count
            )
            assert conn.execute("SELECT * FROM users WHERE id = %s", (member,)).fetchone() == before
            assert (
                conn.execute("SELECT count(*) FROM raw_health_record WHERE end_user_id = %s", (other,)).fetchone()[0]
                == 0
            )
            assert conn.execute(
                "SELECT count(*) FROM sync_state WHERE end_user_id = %s AND initial_sync_done", (member,)
            ).fetchone()[0] == len(METRICS)
            assert (
                conn.execute("SELECT count(*) FROM lifestyle_score WHERE end_user_id = %s", (member,)).fetchone()[0] > 0
            )
            with pytest.raises(ValueError, match="다른 합성 유형"):
                synthetic.seed_member(conn, member, synthetic.PERSONA_BY_ID["synth-03"], TODAY, NOW)
            states = conn.execute(
                "SELECT * FROM sync_state WHERE end_user_id = %s ORDER BY metric", (member,)
            ).fetchall()
            with monkeypatch.context() as patch:
                patch.setattr(
                    synthetic, "aggregate_and_mark_ready", MagicMock(side_effect=RuntimeError("test rollback"))
                )
                with pytest.raises(RuntimeError, match="test rollback"):
                    synthetic.seed_member(conn, member, PERSONA, TODAY + timedelta(days=1), NOW + timedelta(days=1))
            assert (
                conn.execute("SELECT * FROM sync_state WHERE end_user_id = %s ORDER BY metric", (member,)).fetchall()
                == states
            )
            assert (
                conn.execute("SELECT count(*) FROM raw_health_record WHERE end_user_id = %s", (member,)).fetchone()[0]
                == count
            )
            # 테스트에서 만든 행의 출처만 바꿔 기존 외부 데이터가 있는 경우도 확인한다.
            conn.execute("UPDATE raw_health_record SET app = 'test.external' WHERE end_user_id = %s", (member,))
            with pytest.raises(ValueError, match="기존 건강 데이터"):
                synthetic.seed_member(conn, member, PERSONA, TODAY, NOW)
        finally:
            synthetic._delete_health(conn, [member, other])
            conn.execute("DELETE FROM users WHERE id = ANY(%s)", ([member, other],))


@pytest.mark.db
@pytest.mark.parametrize("days_later", [1, 8])
def test_changed_date_refreshes_persona_values_and_rolls_back(monkeypatch, days_later):
    """기준일 변경으로 최근 7일 심박 상승 구간에서 빠진 값도 신규 생성 결과와 같아야 한다."""
    member, fresh = uuid.uuid4(), uuid.uuid4()
    persona = synthetic.PERSONA_BY_ID["synth-03"]
    later = TODAY + timedelta(days=days_later)
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as conn:
        try:
            for target in (member, fresh):
                conn.execute(
                    "INSERT INTO users (id, social_type, social_user_id, display_name, birth_year, gender, "
                    "height_cm, weight_kg) VALUES (%s, 'GOOGLE', %s, '회귀테스트', 2000, 'MALE', 170, 60)",
                    (target, f"member-refresh-test-{target}"),
                )
            synthetic.seed_member(conn, member, persona, TODAY, NOW)
            before = conn.execute(
                "SELECT data_id, payload, last_modified_at, ingested_at FROM raw_health_record "
                "WHERE end_user_id = %s ORDER BY metric, data_id",
                (member,),
            ).fetchall()
            # 같은 시각의 값을 갱신한 뒤 집계가 실패해도 원본과 저장 시각까지 이전 상태로 돌아와야 한다.
            with monkeypatch.context() as patch:
                patch.setattr(synthetic, "aggregate_and_mark_ready", MagicMock(side_effect=RuntimeError("rollback")))
                with pytest.raises(RuntimeError, match="rollback"):
                    synthetic.seed_member(conn, member, persona, later, synthetic._at(later, 21), full=True)
            assert (
                conn.execute(
                    "SELECT data_id, payload, last_modified_at, ingested_at FROM raw_health_record "
                    "WHERE end_user_id = %s ORDER BY metric, data_id",
                    (member,),
                ).fetchall()
                == before
            )

            synthetic.seed_member(conn, member, persona, later, synthetic._at(later, 21), full=True)
            synthetic.seed_member(conn, fresh, persona, later, synthetic._at(later, 21), full=True)
            # 과거 기간은 보존하되 신규 생성 범위의 모든 원본 값은 일치한다.
            mismatch = conn.execute(
                "SELECT count(*) FROM raw_health_record f LEFT JOIN raw_health_record r "
                "ON r.end_user_id = %s AND r.metric = f.metric AND r.data_id = f.data_id "
                "WHERE f.end_user_id = %s AND (r.id IS NULL OR r.payload IS DISTINCT FROM f.payload)",
                (member, fresh),
            ).fetchone()[0]
            assert mismatch == 0
            sleep = conn.execute(
                "SELECT r.hr_avg, f.hr_avg FROM sleep_session r JOIN sleep_session f "
                "ON r.data_id = f.data_id WHERE r.end_user_id = %s AND f.end_user_id = %s AND r.date = %s",
                (member, fresh, TODAY - timedelta(days=6)),
            ).fetchall()
            assert sleep and all(old == new for old, new in sleep)
            scores = [
                conn.execute(
                    "SELECT total_score, heart_score, hr_baseline, hr_baseline_nights FROM lifestyle_score "
                    "WHERE end_user_id = %s AND date = %s",
                    (target, later),
                ).fetchone()
                for target in (member, fresh)
            ]
            assert scores[0] is not None and scores[0] == scores[1]
        finally:
            synthetic._delete_health(conn, [member, fresh])
            conn.execute("DELETE FROM users WHERE id = ANY(%s)", ([member, fresh],))


# ----- 15분 갱신: PIPELINE_SYNTHETIC_MEMBERS의 로그인 회원 (S15P31A403-217) -----

OTHER = uuid.uuid4()


def test_refresh_members_seeds_each_and_continues_after_failure(cli, monkeypatch):
    cli[0].synthetic_members = (f"{MEMBER}:synth-02", f" {OTHER} : synth-03 ")
    calls = []

    def seed(conn, member_id, persona, today, now, *, full):
        assert full is False  # 15분 갱신은 오늘·어제만
        calls.append((member_id, persona.user_id))
        if member_id == MEMBER:
            raise ValueError("해당 DB에 회원이 없습니다")

    monkeypatch.setattr(synthetic, "seed_member", seed)
    assert synthetic.main(["--refresh-members"]) == 1  # 한 명 실패 → 실패로 끝나 예약 파이프라인에 보인다
    assert calls == [(MEMBER, "synth-02"), (OTHER, "synth-03")]


def test_refresh_members_empty_list_does_not_connect(cli):
    cli[0].synthetic_members = ()
    assert synthetic.main(["--refresh-members"]) == 0
    cli[1].assert_not_called()


@pytest.mark.parametrize("entry", ["not-a-uuid:synth-02", f"{MEMBER}:synth-99", f"{MEMBER}"])
def test_refresh_members_bad_entry_does_not_connect(cli, entry):
    cli[0].synthetic_members = (entry,)
    with pytest.raises(SystemExit):
        synthetic.main(["--refresh-members"])
    cli[1].assert_not_called()


@pytest.mark.parametrize("extra", [["--reset"], ["--people", "synth-02"], ["--member", str(MEMBER)]])
def test_refresh_members_rejects_other_options(cli, extra):
    cli[0].synthetic_members = (f"{MEMBER}:synth-02",)
    with pytest.raises(SystemExit):
        synthetic.main(["--refresh-members", *extra])
    cli[1].assert_not_called()


def test_refresh_members_requires_real_sync_off(cli):
    cli[0].synthetic_members = (f"{MEMBER}:synth-02",)
    cli[0].include_members = True
    with pytest.raises(SystemExit):
        synthetic.main(["--refresh-members"])
    cli[1].assert_not_called()


def test_synthetic_members_setting_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_SYNTHETIC_MEMBERS", f" {MEMBER}:synth-02 , ,{OTHER}:synth-03")
    settings = load_settings(tmp_path / "none.env", require_candiy=False)
    assert settings.synthetic_members == (f"{MEMBER}:synth-02", f"{OTHER}:synth-03")


@pytest.mark.parametrize(
    ("first_record_days_ago", "full"),
    [(None, True), (34, True), (89, False)],  # 처음·예전 35일 계정은 90일 전체, 이미 90일이면 오늘·어제만
)
def test_member_seed_loads_90_days_once_then_recent_only(member_db, monkeypatch, first_record_days_ago, full):
    first = (
        None if first_record_days_ago is None else synthetic.day_start(TODAY - timedelta(days=first_record_days_ago))
    )
    member_db.execute.return_value.fetchone.side_effect = [(MEMBER,), (False,), (first,)]
    seen = []

    def sync(conn, client, target, metric, today, **kwargs):
        seen.append((kwargs["full"], kwargs["recent_days"], kwargs["initial_days"]))
        return SimpleNamespace(status="success")

    monkeypatch.setattr(synthetic, "sync_metric", sync)
    monkeypatch.setattr(synthetic, "dates_to_aggregate", lambda *a, **kw: {TODAY})
    monkeypatch.setattr(synthetic, "aggregate_and_mark_ready", MagicMock(return_value={}))
    synthetic.seed_member(member_db, MEMBER, PERSONA, TODAY, NOW)
    assert set(seen) == {(full, synthetic.REFRESH_DAYS, synthetic.SYNTHETIC_DAYS)}
