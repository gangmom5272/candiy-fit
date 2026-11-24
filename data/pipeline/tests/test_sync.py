"""회원 단위 즉시 동기화 (S15P31A403-58). 합성 레코드만 쓴다."""

import json
import threading
import uuid
from datetime import date

import psycopg
import pytest

from candiy_pipeline import __main__ as run
from candiy_pipeline import aggregator
from candiy_pipeline.aggregator import AGGREGATE_TABLES
from candiy_pipeline.candiy_client import CandiyAuthError, CandiyError
from candiy_pipeline.config import load_settings
from candiy_pipeline.metrics import METRICS
from candiy_pipeline.sync import SYNC_MEMBER_DAYS, _PrefetchedClient, member_lock, sync_member
from candiy_pipeline.targets import SYNTHETIC_SOCIAL_PREFIX, Target
from candiy_pipeline.windows import query_range

TODAY = date(2026, 10, 22)


class FakeClient:
    """실제 API처럼 시작·종료가 모두 기간 안인 기록만 돌려준다. 호출을 기록한다."""

    def __init__(self, records=None, errors=None):
        self.records = records or {}
        self.errors = errors or {}
        self.calls = []
        self._lock = threading.Lock()

    def fetch(self, metric, candiy_id, start, end):
        with self._lock:
            self.calls.append((metric, start, end))
        if metric in self.errors:
            raise self.errors[metric]
        return [
            r
            for r in self.records.get(metric, [])
            if start.isoformat() <= r["start"][:10] and (r.get("end") or r["start"])[:10] <= end.isoformat()
        ]


def steps(day, count):
    t = f"{day}T21:00:00+09:00"
    return {
        "app": "com.sec.android.app.shealth",
        "data_id": f"steps-{day}",
        "start": f"{day}T00:00:00+09:00",
        "end": f"{day}T23:59:59+09:00",
        "data": json.dumps({"count": count}),
        "last_modified_time": t,
        "created_at": t,
        "updated_at": t,
    }


def test_recent_days_range():
    assert query_range(TODAY, initial=False, full=False, recent_days=SYNC_MEMBER_DAYS) == (date(2026, 10, 21), TODAY)
    assert query_range(TODAY, initial=True, full=False, recent_days=SYNC_MEMBER_DAYS)[0] == date(2026, 9, 18)


def test_prefetched_client_serves_results_and_errors():
    fake = FakeClient({"steps": [steps("2026-10-22", 100)]}, errors={"sleeps": CandiyError("http_500")})
    plan = [("steps", date(2026, 10, 21), TODAY), ("sleeps", date(2026, 10, 21), TODAY)]
    client = _PrefetchedClient(fake, "u", plan)
    assert len(fake.calls) == 2  # 미리 한 번씩만 부른다
    assert len(client.fetch("steps", "u", date(2026, 10, 21), TODAY)) == 1
    with pytest.raises(CandiyError):
        client.fetch("sleeps", "u", date(2026, 10, 21), TODAY)
    assert len(fake.calls) == 2  # 다시 부르지 않는다


# ----- DB -----


@pytest.fixture
def conn():
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def target(conn):
    t = Target(member_id=uuid.uuid4(), candiy_id=f"test-{uuid.uuid4().hex[:8]}")
    yield t
    for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
        conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (t.member_id,))
    conn.execute("DELETE FROM users WHERE id = %s", (t.member_id,))


def mark_initial_done(conn, target):
    for metric in METRICS:
        conn.execute(
            "INSERT INTO sync_state (end_user_id, metric, initial_sync_done) VALUES (%s, %s, true)",
            (target.member_id, metric),
        )


@pytest.mark.db
def test_sync_member_loads_aggregates_and_scores(conn, target):
    mark_initial_done(conn, target)
    fake = FakeClient({"steps": [steps("2026-10-21", 8000), steps("2026-10-22", 3000)]})
    r = sync_member(conn, fake, target, today=TODAY)

    assert (r.status, r.fetched, r.failed_metrics) == ("synced", 2, [])
    # 오늘·어제만 조회한다
    assert {(s, e) for _, s, e in fake.calls} == {(date(2026, 10, 21), TODAY)}
    assert len(fake.calls) == len(METRICS)
    rows = conn.execute(
        "SELECT date, steps_score FROM lifestyle_score WHERE end_user_id = %s ORDER BY date", (target.member_id,)
    ).fetchall()
    assert rows == [(date(2026, 10, 21), 60), (date(2026, 10, 22), 30)]


@pytest.mark.db
def test_sync_member_skips_recent_unless_forced(conn, target):
    mark_initial_done(conn, target)
    fake = FakeClient()
    assert sync_member(conn, fake, target, today=TODAY).status == "synced"
    calls = len(fake.calls)
    assert sync_member(conn, fake, target, today=TODAY).status == "skipped_recent"
    assert len(fake.calls) == calls  # 2분 안에는 CANDiY를 부르지 않는다
    assert sync_member(conn, fake, target, today=TODAY, force=True).status == "synced"


@pytest.mark.db
def test_sync_member_busy_when_other_run_holds_member(conn, target):
    with psycopg.connect(load_settings(require_candiy=False).db_dsn, autocommit=True) as other:
        with member_lock(other, target.member_id) as got:
            assert got
            fake = FakeClient()
            assert sync_member(conn, fake, target, today=TODAY).status == "busy"
            assert fake.calls == []
    assert sync_member(conn, FakeClient(), target, today=TODAY).status == "synced"  # 잠금이 풀리면 다시 된다


@pytest.mark.db
def test_sync_member_records_metric_failure(conn, target):
    mark_initial_done(conn, target)
    fake = FakeClient({"steps": [steps("2026-10-22", 3000)]}, errors={"sleeps": CandiyError("http_503")})
    r = sync_member(conn, fake, target, today=TODAY)
    assert r.status == "synced" and r.failed_metrics == ["sleeps:http_503"]
    status = conn.execute(
        "SELECT last_status FROM sync_state WHERE end_user_id = %s AND metric = 'sleeps'", (target.member_id,)
    ).fetchone()[0]
    assert status == "fail"  # 한 지표 실패가 다른 지표·집계를 막지 않는다


@pytest.mark.db
def test_sync_member_auth_failure(conn, target):
    r = sync_member(conn, FakeClient(errors=dict.fromkeys(METRICS, CandiyAuthError("x"))), target, today=TODAY)
    assert r.status == "auth_failed"


@pytest.mark.db
def test_sync_member_skips_synthetic_users(conn, target):
    conn.execute(
        "INSERT INTO users (id, social_type, social_user_id, display_name, birth_year, gender, height_cm, weight_kg) "
        "VALUES (%s, 'GOOGLE', %s, '테스트', 2000, 'MALE', 170, 60)",
        (target.member_id, SYNTHETIC_SOCIAL_PREFIX + "test"),
    )
    fake = FakeClient()
    assert sync_member(conn, fake, target, today=TODAY).status == "synthetic"
    assert fake.calls == []


# ----- 조회 준비 신호는 집계가 성공해야 바뀐다 (S15P31A403-78) -----


def ready_state(conn, member_id):
    return conn.execute(
        "SELECT bool_and(initial_sync_done), bool_and(last_status = 'success'), min(last_error), "
        "max(last_data_at) IS NOT NULL FROM sync_state WHERE end_user_id = %s",
        (member_id,),
    ).fetchone()


def activity_steps(conn, member_id):
    return conn.execute(
        "SELECT date, steps FROM activity_daily WHERE end_user_id = %s ORDER BY date", (member_id,)
    ).fetchall()


def broken_aggregate(*args, **kwargs):
    raise RuntimeError("합성 집계 실패")


@pytest.mark.db
def test_sync_member_aggregate_failure_is_not_ready_then_recovers(conn, target, monkeypatch):
    fake = FakeClient({"steps": [steps("2026-10-22", 3000)]})
    monkeypatch.setattr(aggregator, "aggregate_member", broken_aggregate)
    r = sync_member(conn, fake, target, today=TODAY)
    assert (r.status, r.failed_metrics) == ("aggregate_failed", ["aggregate:RuntimeError"])
    # 원본은 들어왔지만 집계가 없으므로 준비 완료가 아니고, 기준 시각도 앞서 나가지 않는다
    assert ready_state(conn, target.member_id) == (False, False, "aggregate:RuntimeError", False)
    assert activity_steps(conn, target.member_id) == []

    monkeypatch.undo()
    assert sync_member(conn, fake, target, today=TODAY, force=True).status == "synced"
    assert ready_state(conn, target.member_id) == (True, True, None, True)
    assert activity_steps(conn, target.member_id) == [(date(2026, 10, 22), 3000)]


@pytest.mark.db
def test_run_continues_after_member_aggregate_failure(conn, target, monkeypatch):
    other = Target(member_id=uuid.uuid4(), candiy_id=f"test-{uuid.uuid4().hex[:8]}")
    fake = FakeClient({"steps": [steps("2026-10-22", 3000)]})
    fake.close = lambda: None
    real_aggregate = aggregator.aggregate_member

    def fail_first(conn_, member_id, dates):
        if member_id == target.member_id:
            raise RuntimeError("합성 집계 실패")
        return real_aggregate(conn_, member_id, dates)

    monkeypatch.setattr(run, "load_settings", lambda: load_settings(require_candiy=False))
    monkeypatch.setattr(run, "CandiyClient", lambda *args: fake)
    monkeypatch.setattr(run, "load_targets", lambda *args, **kwargs: [target, other])
    monkeypatch.setattr(aggregator, "aggregate_member", fail_first)
    try:
        assert run.main(["--today", TODAY.isoformat()]) == 2  # 집계 실패가 있으면 종료 코드로 알린다
        assert ready_state(conn, target.member_id)[0] is False
        # 앞 회원 집계가 실패해도 뒤 회원은 처리된다
        assert ready_state(conn, other.member_id) == (True, True, None, True)
        assert activity_steps(conn, other.member_id) == [(date(2026, 10, 22), 3000)]
    finally:
        for table in ("raw_health_record", "step_snapshot", "sync_state", "lifestyle_score", *AGGREGATE_TABLES):
            conn.execute(f"DELETE FROM {table} WHERE end_user_id = %s", (other.member_id,))


def _run_with(monkeypatch, fake, targets):
    fake.close = lambda: None
    monkeypatch.setattr(run, "load_settings", lambda: load_settings(require_candiy=False))
    monkeypatch.setattr(run, "CandiyClient", lambda *args: fake)
    monkeypatch.setattr(run, "load_targets", lambda *args, **kwargs: targets)
    return run.run(["--today", TODAY.isoformat()])


@pytest.mark.db
def test_run_counts_all_404_member_as_unlinked_not_failure(conn, target, monkeypatch):
    # 앱을 연동하지 않은 회원: CANDiY가 모든 지표에 404 (S15P31A403-232)
    fake = FakeClient(errors={m: CandiyError("http_404") for m in METRICS})
    result = _run_with(monkeypatch, fake, [target])
    assert result.code == 0  # 알림이 울리지 않는다
    assert result.totals["unlinked_members"] == 1
    assert result.totals["fail"] == 0 and not result.errors
    # 조회 쪽 미연동 판정(409)을 위해 sync_state에는 404가 그대로 남는다
    states = conn.execute(
        "SELECT DISTINCT last_status, last_error FROM sync_state WHERE end_user_id = %s", (target.member_id,)
    ).fetchall()
    assert states == [("fail", "http_404")]


@pytest.mark.db
def test_run_partial_404_is_still_failure(conn, target, monkeypatch):
    fake = FakeClient({"steps": [steps("2026-10-22", 3000)]}, errors={m: CandiyError("http_404") for m in METRICS[1:]})
    result = _run_with(monkeypatch, fake, [target])
    assert result.code == 2
    assert result.totals["unlinked_members"] == 0
    assert result.totals["fail"] == len(METRICS) - 1 and result.errors["http_404"] == len(METRICS) - 1


@pytest.mark.db
def test_run_all_failed_but_not_all_404_is_failure(conn, target, monkeypatch):
    errors = {m: CandiyError("http_404") for m in METRICS}
    errors["steps"] = CandiyError("http_503")
    result = _run_with(monkeypatch, FakeClient(errors=errors), [target])
    assert result.code == 2
    assert result.totals["unlinked_members"] == 0
    assert result.errors["http_503"] == 1
