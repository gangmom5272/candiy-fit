"""로컬 PostgreSQL에 실제로 적재해 보는 테스트. 합성 레코드만 쓴다.

실행: docker compose로 DB를 띄운 뒤 `pytest -m db`
"""

import json
import uuid
from datetime import date, datetime

import psycopg
import pytest

from candiy_pipeline.candiy_client import CandiyError
from candiy_pipeline.config import load_settings
from candiy_pipeline.loader import mark_failed, mark_ready, sync_metric
from candiy_pipeline.records import KST
from candiy_pipeline.targets import Target

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 18)


def rec(
    data_id, start, end=None, modified="2026-10-18T09:00:00+09:00", app="com.sec.android.app.shealth", created=None
):
    r = {
        "app": app,
        "data_id": data_id,
        "start": start,
        "data": json.dumps({"count": 1}),
        "last_modified_time": modified,
        "created_at": created or modified,
        "updated_at": modified,
    }
    if end:
        r["end"] = end
    return r


class FakeClient:
    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error
        self.calls = []

    def fetch(self, metric, candiy_id, start, end):
        self.calls.append((start, end))
        if self.error:
            raise CandiyError(self.error)
        # 실제 API처럼 시작·종료가 모두 기간 안인 기록만 돌려준다 (날짜만 비교, 시점형은 시작만)
        return [
            r
            for r in self.records
            if start.isoformat() <= r["start"][:10] and (r.get("end") or r["start"])[:10] <= end.isoformat()
        ]


@pytest.fixture
def conn():
    with psycopg.connect(load_settings().db_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def target(conn):
    t = Target(member_id=uuid.uuid4(), candiy_id="synthetic")
    yield t
    conn.execute("DELETE FROM raw_health_record WHERE end_user_id = %s", (t.member_id,))
    conn.execute("DELETE FROM sync_state WHERE end_user_id = %s", (t.member_id,))
    conn.execute("DELETE FROM step_snapshot WHERE end_user_id = %s", (t.member_id,))


def raw_count(conn, target):
    return conn.execute(
        "SELECT count(*) FROM raw_health_record WHERE end_user_id = %s", (target.member_id,)
    ).fetchone()[0]


def state(conn, target, metric="steps"):
    return conn.execute(
        "SELECT initial_sync_done, last_status, last_error, last_data_at, first_data_at, last_success_at "
        "FROM sync_state WHERE end_user_id = %s AND metric = %s",
        (target.member_id, metric),
    ).fetchone()


def test_first_sync_loads_and_is_idempotent(conn, target):
    client = FakeClient(
        [
            rec("a", "2026-10-17T00:00:00+09:00", "2026-10-17T23:59:59.999+09:00"),
            rec("b", "2026-10-18T00:00:00+09:00", "2026-10-18T23:59:59.999+09:00"),
            rec("q", "2026-10-18T01:00:00+09:00", app="io.candiy.healthconnect.quickstart"),
        ]
    )

    r1 = sync_metric(conn, client, target, "steps", TODAY)
    assert (r1.status, r1.inserted, r1.skipped["excluded_app"]) == ("success", 2, 1)
    assert len(client.calls) == 6  # 첫 연동 35일 → 7일 구간을 하루씩 겹쳐 6번
    mark_ready(conn, target.member_id, ["steps"])  # 실제로는 그 회원 집계가 성공한 뒤에 한다

    r2 = sync_metric(conn, client, target, "steps", TODAY)
    assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 2)
    assert raw_count(conn, target) == 2
    assert len(client.calls) == 7  # 두 번째는 최근 3일 → 1번


def test_member_keeps_own_collector_app_records(conn):
    # 실제 회원(candiy_id = member_id)은 우리 수집 앱(LiNK)이 쓴 기록을 적재한다. 패키지명이 예제 앱과 같다
    member_id = uuid.uuid4()
    member = Target(member_id=member_id, candiy_id=str(member_id))
    client = FakeClient([rec("q", "2026-10-18T01:00:00+09:00", app="io.candiy.healthconnect.quickstart")])
    try:
        r = sync_metric(conn, client, member, "steps", TODAY)
        assert (r.status, r.inserted, r.skipped["excluded_app"]) == ("success", 1, 0)
    finally:
        conn.execute("DELETE FROM raw_health_record WHERE end_user_id = %s", (member_id,))
        conn.execute("DELETE FROM sync_state WHERE end_user_id = %s", (member_id,))
        conn.execute("DELETE FROM step_snapshot WHERE end_user_id = %s", (member_id,))


def test_step_versions_are_kept(conn, target):
    # 하루 누적 걸음 기록 하나가 세 번 갱신돼 왔다. 원본에는 최신 1행, step_snapshot에는 버전 3행
    day = ("2026-10-17T00:00:00+09:00", "2026-10-17T23:59:59.999+09:00")
    versions = [
        ("2026-10-17T09:00:00+09:00", 1200),
        ("2026-10-17T12:00:00+09:00", 3000),
        ("2026-10-17T18:00:00+09:00", 5000),
    ]
    records = []
    for modified, count in versions:
        r = rec("day1", *day, modified=modified)
        r["data"] = json.dumps({"count": count})
        records.append(r)

    sync_metric(conn, FakeClient(records), target, "steps", TODAY)
    assert raw_count(conn, target) == 1
    snaps = conn.execute(
        "SELECT step_count FROM step_snapshot WHERE end_user_id = %s ORDER BY modified_at", (target.member_id,)
    ).fetchall()
    assert snaps == [(1200,), (3000,), (5000,)]

    # 다시 적재해도 버전은 늘지 않는다
    sync_metric(conn, FakeClient(records), target, "steps", TODAY, full=True)
    assert (
        conn.execute("SELECT count(*) FROM step_snapshot WHERE end_user_id = %s", (target.member_id,)).fetchone()[0]
        == 3
    )


def test_record_crossing_chunk_boundary_is_loaded_once(conn, target):
    # 10-11 밤 → 10-12 아침 수면. 겹치지 않는 구간(…~10-11, 10-12~…)이면 어느 구간에도 들어가지 않는다
    sleep = rec("night", "2026-10-11T23:30:00+09:00", "2026-10-12T07:00:00+09:00")
    # 겹친 날(10-08)의 기록은 두 구간에서 모두 온다
    overlap_day = rec("overlap", "2026-10-08T00:00:00+09:00", "2026-10-08T23:59:59.999+09:00")
    client = FakeClient([sleep, overlap_day])

    r = sync_metric(conn, client, target, "sleeps", TODAY)
    assert (r.status, r.fetched, r.inserted) == ("success", 3, 2)
    assert raw_count(conn, target) == 2


def test_newer_modified_overwrites_older_does_not(conn, target):
    sync_metric(
        conn,
        FakeClient([rec("a", "2026-10-18T00:00:00+09:00", modified="2026-10-18T09:00:00+09:00")]),
        target,
        "steps",
        TODAY,
    )
    older = sync_metric(
        conn,
        FakeClient([rec("a", "2026-10-18T00:00:00+09:00", modified="2026-10-18T08:00:00+09:00")]),
        target,
        "steps",
        TODAY,
    )
    newer = sync_metric(
        conn,
        FakeClient([rec("a", "2026-10-18T00:00:00+09:00", modified="2026-10-18T10:00:00+09:00")]),
        target,
        "steps",
        TODAY,
    )
    assert older.unchanged == 1 and newer.updated == 1


@pytest.mark.parametrize("refresh_source", [None, "different-source", "dev.candiy-fit.synthetic.synth-03"])
def test_equal_timestamp_refresh_only_updates_selected_source(conn, target, refresh_source):
    source = "dev.candiy-fit.synthetic.synth-03"
    original = rec("night-hr", "2026-10-18T06:00:00+09:00", app=source)
    original["data"] = json.dumps({"beatsPerMinute": [{"time": "2026-10-18T06:00:00+09:00", "value": 69}]})
    sync_metric(conn, FakeClient([original]), target, "heart_rates", TODAY)
    before = conn.execute(
        "SELECT last_modified_at, source_created_at FROM raw_health_record WHERE end_user_id = %s",
        (target.member_id,),
    ).fetchone()
    changed = {**original, "data": json.dumps({"beatsPerMinute": [{"time": "2026-10-18T06:00:00+09:00", "value": 62}]})}
    options = {} if refresh_source is None else {"refresh_source": refresh_source}
    result = sync_metric(conn, FakeClient([changed]), target, "heart_rates", TODAY, **options)
    assert result.updated == (1 if refresh_source == source else 0)
    row = conn.execute(
        "SELECT payload, last_modified_at, source_created_at FROM raw_health_record WHERE end_user_id = %s",
        (target.member_id,),
    ).fetchone()
    assert row[0] == json.loads(changed["data"] if refresh_source == source else original["data"])
    assert row[1:] == before  # 측정·최초 수집 시각은 바꾸지 않는다.
    assert sync_metric(conn, FakeClient([changed]), target, "heart_rates", TODAY, **options).updated == 0
    older = {**original, "last_modified_time": "2026-10-18T08:00:00+09:00"}
    assert sync_metric(conn, FakeClient([older]), target, "heart_rates", TODAY, **options).updated == 0


def test_last_data_at_is_capped_by_last_modified(conn, target):
    # 하루 단위 걸음 기록: end가 23:59지만 09:00에 수정된 기록이면 기준 시각은 09:00
    client = FakeClient(
        [
            rec(
                "a", "2026-10-17T00:00:00+09:00", "2026-10-17T23:59:59.999+09:00", modified="2026-10-17T23:59:59+09:00"
            ),
            rec(
                "b", "2026-10-18T00:00:00+09:00", "2026-10-18T23:59:59.999+09:00", modified="2026-10-18T09:00:00+09:00"
            ),
        ]
    )
    sync_metric(conn, client, target, "steps", TODAY)
    mark_ready(conn, target.member_id, ["steps"])
    done, status, error, last_data_at, first_data_at, _ = state(conn, target)
    assert (done, status, error) == (True, "success", None)
    assert last_data_at == datetime(2026, 10, 18, 9, tzinfo=KST)  # end 23:59가 아니라 수정 시각 09:00
    assert first_data_at == datetime(2026, 10, 17, tzinfo=KST)


def test_load_alone_does_not_make_ready(conn, target):
    # 적재만 끝나고 집계 전이면 조회 준비 완료가 아니다. 기준 시각도 아직 바꾸지 않는다 (S15P31A403-78)
    sync_metric(conn, FakeClient([rec("a", "2026-10-18T00:00:00+09:00")]), target, "steps", TODAY)
    done, status, error, last_data_at, first_data_at, last_success_at = state(conn, target)
    assert (done, status, error) == (False, "success", None)
    assert last_data_at is None and first_data_at is None and last_success_at is None


def test_ready_without_records(conn, target):
    # 기록이 하나도 없어도 조회는 성공했으므로 준비 완료다. 기준 시각만 없다
    sync_metric(conn, FakeClient([]), target, "sleeps", TODAY)
    mark_ready(conn, target.member_id, ["sleeps"])
    done, status, _, last_data_at, first_data_at, last_success_at = state(conn, target, "sleeps")
    assert (done, status) == (True, "success")
    assert last_data_at is None and first_data_at is None and last_success_at is not None


def test_mark_failed_keeps_ready_and_time(conn, target):
    # 집계 실패를 남겨도 이전 준비 상태·기준 시각은 그대로 둔다 (기존 집계는 계속 쓸 수 있다)
    sync_metric(conn, FakeClient([rec("a", "2026-10-18T00:00:00+09:00")]), target, "steps", TODAY)
    mark_ready(conn, target.member_id, ["steps"])
    before = state(conn, target)
    mark_failed(conn, target.member_id, ["steps"], "aggregate:RuntimeError")
    after = state(conn, target)
    assert (after[1], after[2]) == ("fail", "aggregate:RuntimeError")
    assert after[0] is True and after[3:] == before[3:]


def test_failure_keeps_previous_state(conn, target):
    sync_metric(conn, FakeClient([rec("a", "2026-10-18T00:00:00+09:00")]), target, "steps", TODAY)
    mark_ready(conn, target.member_id, ["steps"])
    before = state(conn, target)

    r = sync_metric(conn, FakeClient(error="http_503"), target, "steps", TODAY)
    after = state(conn, target)
    assert (r.status, r.error) == ("fail", "http_503")
    assert (after[1], after[2]) == ("fail", "http_503")
    assert after[3] == before[3] and after[5] == before[5]  # last_data_at, last_success_at 그대로
    assert raw_count(conn, target) == 1


def created_at(conn, target, data_id):
    return conn.execute(
        "SELECT source_created_at FROM raw_health_record WHERE end_user_id = %s AND data_id = %s",
        (target.member_id, data_id),
    ).fetchone()[0]


def test_resent_versions_keep_first_arrival_time(conn, target):
    # CANDiY는 같은 data_id를 다시 전송하면 새 행으로 저장해 여러 버전이 함께 온다
    start = "2026-10-18T00:00:00+09:00"
    client = FakeClient(
        [
            rec("a", start, modified="2026-10-18T09:00:00+09:00", created="2026-10-18T09:01:00+09:00"),
            rec("a", start, modified="2026-10-18T11:00:00+09:00", created="2026-10-18T11:01:00+09:00"),
            rec("a", start, modified="2026-10-18T10:00:00+09:00", created="2026-10-18T10:01:00+09:00"),
        ]
    )
    r = sync_metric(conn, client, target, "steps", TODAY)
    assert (r.fetched, r.inserted) == (3, 1)
    assert created_at(conn, target, "a") == datetime(2026, 10, 18, 9, 1, tzinfo=KST)

    # 다음 실행에서 더 새 버전이 와도 처음 도착 시각은 그대로다
    newer = FakeClient([rec("a", start, modified="2026-10-18T12:00:00+09:00", created="2026-10-18T12:01:00+09:00")])
    assert sync_metric(conn, newer, target, "steps", TODAY).updated == 1
    assert created_at(conn, target, "a") == datetime(2026, 10, 18, 9, 1, tzinfo=KST)


def test_same_data_id_for_two_members_is_kept_separately(conn, target):
    other = Target(member_id=uuid.uuid4(), candiy_id="synthetic-2")
    try:
        client = FakeClient([rec("shared", "2026-10-18T00:00:00+09:00")])
        sync_metric(conn, client, target, "steps", TODAY)
        sync_metric(conn, client, other, "steps", TODAY)
        assert raw_count(conn, target) == 1 and raw_count(conn, other) == 1
    finally:
        conn.execute("DELETE FROM raw_health_record WHERE end_user_id = %s", (other.member_id,))
        conn.execute("DELETE FROM sync_state WHERE end_user_id = %s", (other.member_id,))
