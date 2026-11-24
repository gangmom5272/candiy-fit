from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, update

from app.health import errors, repository, service
from app.health.models import SleepSession, SyncState
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_sleep_detail import sleep_row

pytestmark = pytest.mark.db


def row(owner, session_id="main", **changes):
    return SleepSession(
        data_id=session_id, computed_at=NOW, **sleep_row(end_user_id=owner, **changes)
    )


def ready(owner):
    return SyncState(
        end_user_id=owner,
        metric="sleeps",
        initial_sync_done=True,
        first_data_at=NOW - timedelta(days=365),
        last_success_at=NOW,
        last_data_at=NOW,
        last_status="success",
    )


async def call(factory, owner, start=TODAY - timedelta(days=1), end=TODAY):
    return await service.get_sleep_sessions(owner, start, end, session_factory=factory)


async def test_owner_wake_date_order_and_bounded_two_selects(health_db, monkeypatch):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    midnight = datetime(2026, 10, 21, 15, tzinfo=UTC)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(
            SyncState(end_user_id=owner, metric="heart_rates", initial_sync_done=False)
        )
        db.add(row(owner))
        # 동일 시작·종료 시각은 ID로 정렬하며, 비주 수면 유형은 추정하지 않는다.
        for identifier, end in [
            ("z", midnight - timedelta(hours=1)),
            ("b", midnight - timedelta(hours=1)),
            ("a", midnight - timedelta(hours=2)),
        ]:
            db.add(
                row(
                    owner,
                    identifier,
                    is_main=False,
                    start_at=midnight - timedelta(hours=3),
                    end_at=end,
                    session_minutes=int(
                        (end - (midnight - timedelta(hours=3))).total_seconds() / 60
                    ),
                    sleep_minutes=None,
                    deep_minutes=None,
                    light_minutes=None,
                    rem_minutes=None,
                    awake_minutes=None,
                    unspecified_minutes=None,
                )
            )
        # UTC로 전날이어도 KST 자정 종료는 오늘 기록이다.
        db.add(
            row(
                owner,
                "today",
                date=TODAY,
                start_at=midnight - timedelta(hours=1),
                end_at=midnight,
                session_minutes=60,
                sleep_minutes=50,
                deep_minutes=10,
                rem_minutes=10,
                light_minutes=30,
                awake_minutes=10,
            )
        )
        db.add(row(other, "foreign", deep_minutes=-1))
        db.add(row(owner, "outside", date=TODAY - timedelta(days=2), deep_minutes=-1))
        db.add(row(owner, "future", date=TODAY + timedelta(days=1), deep_minutes=-1))
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = await call(factory, owner)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [s.session_id for s in result.sessions] == ["main", "a", "b", "z", "today"]
    assert result.sessions[0].start_at.date() < result.period.start
    assert result.sessions[1].sleep_minutes is None
    assert result.sessions[-1].end_at == midnight
    assert all(s.computed_at == NOW for s in result.sessions)
    assert len(statements) == 2
    sql, parameters = statements[1]
    assert "LIMIT" in sql and 1001 in parameters
    assert "sleep_timeline" not in sql and "raw_health_record" not in sql
    assert [s.session_id for s in (await call(factory, owner, TODAY)).sessions] == [
        "today"
    ]


@pytest.mark.parametrize("count", [1000, 1001])
async def test_actual_sql_limit_is_distinct_from_lookup_failure(
    health_db, monkeypatch, count
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add_all(
            [row(owner, f"session-{i:04}", is_main=i == 0) for i in range(count)]
        )
        if count == 1001:
            await db.flush()
            await db.execute(
                update(SleepSession)
                .where(SleepSession.end_user_id == owner)
                .values(deep_minutes=-1, is_main=False)
            )
    if count == 1001:
        with pytest.raises(errors.SleepSessionLimitError) as caught:
            await call(factory, owner)
        assert caught.value.code == "SLEEP_SESSION_LIMIT_EXCEEDED"
    else:
        assert len((await call(factory, owner)).sessions) == 1000


@pytest.mark.parametrize(
    "changes",
    [dict(deep_minutes=-1), dict(spo2_avg=Decimal("99.1")), dict(sleep_minutes=481)],
)
async def test_bad_nonmain_fails_only_new_lookup_and_null_still_succeeds(
    health_db, monkeypatch, changes
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(
            SyncState(
                end_user_id=owner,
                metric="oxygen_saturations",
                initial_sync_done=True,
                last_success_at=NOW,
                last_status="success",
            )
        )
        db.add(row(owner))
        db.add(row(owner, "other", is_main=False, **changes))
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner)
    assert caught.value.metrics == ("sleeps",)
    for metric, expected in [("sleeps", 400), ("oxygen_saturations", Decimal("95.7"))]:
        original = await service.get_metric_result(
            owner, metric, TODAY - timedelta(days=365), TODAY, session_factory=factory
        )
        assert original.summary.avg == expected and original.summary.days_with_data == 1
    async with factory.begin() as db:
        await db.execute(
            update(SleepSession)
            .where(SleepSession.end_user_id == owner, SleepSession.data_id == "other")
            .values(**dict.fromkeys(changes))
        )
    result = await call(factory, owner)
    assert len(result.sessions) == 2
    assert result.sessions[1].model_dump().items() >= dict.fromkeys(changes).items()


@pytest.mark.parametrize("sleep_minutes", [None, 0, 480])
async def test_stored_sleep_duration_boundaries_preserved(
    health_db, monkeypatch, sleep_minutes
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, sleep_minutes=sleep_minutes))
    result = await call(factory, owner)
    assert result.sessions[0].sleep_minutes == sleep_minutes
    assert result.sessions[0].session_minutes == 480


@pytest.mark.parametrize("kind", ["none", "multiple", "date", "span"])
async def test_invalid_stored_session_contract(health_db, monkeypatch, kind):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    changes = {
        "none": dict(is_main=False),
        "multiple": {},
        "date": dict(date=TODAY),
        "span": dict(start_at=NOW, end_at=NOW),
    }[kind]
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, **changes))
        if kind == "multiple":
            db.add(row(owner, "second"))
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


async def test_readonly_snapshot_preserves_main_selection_across_reaggregate(
    health_db, monkeypatch
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner))
        db.add(row(owner, "other", is_main=False))
    original = repository.list_sync_states

    async def concurrent_write(db, member):
        states = await original(db, member)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(SleepSession)
                .where(SleepSession.end_user_id == owner)
                .values(is_main=~SleepSession.is_main)
            )
            await writer.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(last_status="fail")
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", concurrent_write)
    result = await call(factory, owner)
    assert [s.session_id for s in result.sessions if s.is_main] == ["main"]
    assert result.data_status.last_sync_status == "success"
    monkeypatch.setattr(repository, "list_sync_states", original)
    following = await call(factory, owner)
    assert [s.session_id for s in following.sessions if s.is_main] == ["other"]
    assert following.data_status.last_sync_status == "fail"
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"
