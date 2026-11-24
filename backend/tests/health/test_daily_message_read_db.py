"""198번 전용 읽기 블록. 제어 연결은 대상의 단일 연결 풀과 분리한다."""

import asyncio
from datetime import date
from time import monotonic

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    AsyncTransaction,
    create_async_engine,
)

from app.health import daily_message_read as read_module
from app.health.daily_message_read import ReadCleanupError, read_daily_message_block
from app.health.errors import HealthDataForbiddenError, HealthDataLookupError
from app.health.models import LifestyleScore, SleepSession
from tests.health.test_service_db import seed

pytestmark = pytest.mark.db


@pytest.fixture
async def read_engines(health_db):
    factory, owners = health_db
    control = factory.kw["bind"]
    target = create_async_engine(
        control.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.05,
        pool_pre_ping=True,
    )
    try:
        yield target, control, owners
    finally:
        await target.dispose()


async def test_bound_session_keeps_lease_until_connection_close(read_engines):
    engine, _, _ = read_engines
    returned = []
    event.listen(engine.sync_engine, "checkin", lambda *args: returned.append(True))
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="rollback_only")
    try:
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert (
            await session.scalar(text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        await session.close()
        assert returned == []
        assert raw.is_valid and not driver.is_closed()
        assert transaction.is_active
        await transaction.rollback()
        assert returned == []
        await connection.close()
        assert returned == [True]
        assert not raw.is_valid
        async with engine.connect() as next_connection:
            assert (
                await next_connection.get_raw_connection()
            ).driver_connection is driver
            assert (
                await next_connection.scalar(text("SHOW transaction_isolation"))
                == "read committed"
            )
            assert (
                await next_connection.scalar(text("SHOW transaction_read_only"))
                == "off"
            )
    finally:
        await session.close()
        await connection.close()


async def test_readonly_rejects_write_and_pool_recovers(read_engines):
    engine, _, _ = read_engines
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text("DELETE FROM sync_state WHERE false"))
        assert caught.value.orig.sqlstate == "25006"
        await transaction.rollback()
    finally:
        await connection.close()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_session_close_failure_does_not_return_external_connection(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="rollback_only")
    close = session.close

    async def failed_close():
        raise OSError("synthetic close fault")

    try:
        await session.scalar(text("SELECT 1"))
        raw = await connection.get_raw_connection()
        monkeypatch.setattr(session, "close", failed_close)
        with pytest.raises(OSError):
            await session.close()
        assert raw.is_valid and not connection.closed
        assert engine.pool.checkedout() == 1 and transaction.is_active
    finally:
        await close()
        await transaction.rollback()
        await connection.close()


async def test_waiting_checkout_timeout_and_cancel_do_not_lose_lease(read_engines):
    engine, _, _ = read_engines
    holder = await engine.connect()
    raw = await holder.get_raw_connection()
    try:
        with pytest.raises(PoolTimeoutError):
            await engine.connect()
        started = asyncio.Event()

        async def acquire():
            started.set()
            return await engine.connect()

        waiter = asyncio.create_task(acquire())
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert raw.is_valid and engine.pool.checkedout() == 1
    finally:
        await holder.close()
    assert engine.pool.checkedout() == 0


async def test_failure_after_checkout_does_not_leak_unassigned_connection(read_engines):
    engine, _, _ = read_engines
    first = True

    def fail_once(dbapi_connection, record, proxy):
        nonlocal first
        if first:
            first = False
            raise OSError("synthetic acquisition fault")

    event.listen(engine.sync_engine, "checkout", fail_once)

    async def read(session):
        return await session.scalar(text("SELECT 1"))

    with pytest.raises(HealthDataLookupError):
        await read_daily_message_block(engine, read, request_id="synthetic-acquire")
    assert engine.pool.checkedout() == 0
    result = await read_daily_message_block(
        engine, read, request_id="synthetic-acquire-next"
    )
    assert result.value == 1


async def test_rollback_fault_discards_before_return(read_engines, monkeypatch, caplog):
    engine, _, _ = read_engines

    async def failed_rollback(transaction):
        raise OSError("synthetic-secret-rollback")

    async def read(session):
        return 450

    with (
        monkeypatch.context() as patch,
        caplog.at_level("INFO", logger="app.health.diagnostics"),
    ):
        patch.setattr(AsyncTransaction, "rollback", failed_rollback)
        with pytest.raises(HealthDataLookupError):
            await read_daily_message_block(
                engine, read, metric="sleeps", request_id="synthetic-rollback"
            )
    assert engine.pool.checkedout() == 0
    record = next(
        r for r in caplog.records if r.message == "daily_message_read_finished"
    )
    assert record.driver_closed and record.pool_blocked and record.tasks_done
    assert "synthetic-secret-rollback" not in caplog.text
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_read_block_returns_after_checkin_with_readonly_transaction(read_engines):
    engine, _, _ = read_engines
    checked_in = []
    event.listen(engine.sync_engine, "checkin", lambda *args: checked_in.append(True))

    async def read(session):
        assert (
            await session.scalar(text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        assert checked_in == []
        return await session.scalar(text("SELECT 17"))

    result = await read_daily_message_block(engine, read, request_id="synthetic-198")
    assert result.value == 17 and result.score is None
    assert checked_in == [True] and engine.pool.checkedout() == 0
    assert result.cleanup_seconds >= 0


async def test_score_uses_separate_budget_in_same_transaction(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    sessions = []
    timers = []
    timeout = asyncio.timeout

    def track_timer(seconds):
        timer = timeout(seconds)
        timers.append(timer)
        return timer

    monkeypatch.setattr(asyncio, "timeout", track_timer)

    async def read(session):
        sessions.append(session)
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        timers[0].reschedule(asyncio.get_running_loop().time() + 0.01)
        return pid

    async def score(session, pid):
        assert session is sessions[0]
        assert await session.scalar(text("SELECT pg_backend_pid()")) == pid
        assert timers[1] is not timers[0]
        assert timers[1].when() - asyncio.get_running_loop().time() > 0.8
        return 90

    result = await read_daily_message_block(
        engine,
        read,
        score_read=score,
        metric="sleeps",
        request_id="synthetic-198",
    )
    assert result.score == 90


async def test_sleep_and_score_keep_snapshot_while_control_pool_updates(read_engines):
    engine, control, (owner, _) = read_engines
    assert engine.pool is not control.pool
    from app.core.db import create_sessionmaker

    await seed(create_sessionmaker(control), owner)
    selected_date = date(2026, 10, 21)
    async with create_sessionmaker(control).begin() as session:
        session.add(
            LifestyleScore(
                end_user_id=owner,
                date=selected_date,
                sleep_minutes=412,
                sleep_score=90,
                hr_baseline_nights=0,
            )
        )
    sleep_query = select(SleepSession.sleep_minutes).where(
        SleepSession.end_user_id == owner, SleepSession.data_id == "main-21"
    )
    score_query = select(
        LifestyleScore.sleep_minutes, LifestyleScore.sleep_score
    ).where(LifestyleScore.end_user_id == owner, LifestyleScore.date == selected_date)

    async def read(session):
        minutes = await session.scalar(sleep_query)
        async with control.begin() as writer:
            await writer.execute(
                update(SleepSession)
                .where(
                    SleepSession.end_user_id == owner, SleepSession.data_id == "main-21"
                )
                .values(sleep_minutes=420)
            )
            await writer.execute(
                update(LifestyleScore)
                .where(LifestyleScore.end_user_id == owner)
                .values(sleep_minutes=420, sleep_score=80)
            )
        return minutes

    async def score(session, value):
        return tuple((await session.execute(score_query)).one())

    result = await read_daily_message_block(
        engine, read, score_read=score, metric="sleeps", request_id="synthetic-snapshot"
    )
    assert (result.value, result.score) == (412, (412, 90))

    async def next_read(session):
        return await session.scalar(sleep_query)

    next_result = await read_daily_message_block(
        engine,
        next_read,
        score_read=score,
        metric="sleeps",
        request_id="synthetic-next",
    )
    assert (next_result.value, next_result.score) == (420, (420, 80))


async def test_score_query_failure_keeps_materialized_sleep_after_normal_cleanup(
    read_engines,
):
    engine, control, _ = read_engines

    async def read(session):
        return 450

    async def score(session, value):
        await session.execute(text("SET LOCAL statement_timeout = '10ms'"))
        await session.execute(text("SELECT pg_sleep(1)"))

    result = await read_daily_message_block(
        engine,
        read,
        score_read=score,
        metric="sleeps",
        request_id="synthetic-score-failure",
    )
    assert result.value == 450 and result.score is None
    assert isinstance(result.score_error, HealthDataLookupError)
    assert engine.pool.checkedout() == 0
    async with control.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


@pytest.mark.parametrize("original", [None, HealthDataForbiddenError(("sleeps",))])
async def test_close_fault_discards_same_driver_and_preserves_whole_error(
    read_engines, monkeypatch, original
):
    engine, _, _ = read_engines
    drivers = []
    close = AsyncSession.close

    async def read(session):
        connection = await session.connection()
        drivers.append((await connection.get_raw_connection()).driver_connection)
        if original:
            raise original
        return 450

    async def failed_close(session):
        await close(session)
        if isinstance(session.bind, AsyncConnection):
            raise OSError("synthetic close fault")

    monkeypatch.setattr(AsyncSession, "close", failed_close)
    error_type = type(original) if original else HealthDataLookupError
    with pytest.raises(error_type) as caught:
        await read_daily_message_block(
            engine, read, metric="sleeps", request_id="synthetic-discard"
        )
    if original:
        assert caught.value is original
    assert drivers[0].is_closed()
    assert engine.pool.checkedout() == 0
    async with engine.connect() as connection:
        assert (await connection.get_raw_connection()).driver_connection is not drivers[
            0
        ]
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_repeated_cancel_during_cleanup_keeps_original_cancel_and_finishes(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    entered = asyncio.Event()
    close = AsyncSession.close
    drivers = []

    async def read(session):
        connection = await session.connection()
        drivers.append((await connection.get_raw_connection()).driver_connection)
        raise asyncio.CancelledError("original-cancel")

    async def delayed_close(session):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(AsyncSession, "close", delayed_close)
    task = asyncio.create_task(
        read_daily_message_block(
            engine,
            read,
            metric="sleeps",
            request_id="synthetic-cancel",
            cleanup_timeout=0.03,
        )
    )
    await entered.wait()
    task.cancel("repeat-one")
    await asyncio.sleep(0)
    task.cancel("repeat-two")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("original-cancel",)
    assert drivers[0].is_closed() and engine.pool.checkedout() == 0
    assert not [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("daily-message-cleanup") and not t.done()
    ]
    monkeypatch.setattr(AsyncSession, "close", close)


async def test_unexpected_close_fault_is_whole_failure_after_disposal(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    close = AsyncSession.close

    async def failed_close(session):
        await close(session)
        if isinstance(session.bind, AsyncConnection):
            raise ValueError("synthetic programming fault")

    async def read(session):
        return 450

    monkeypatch.setattr(AsyncSession, "close", failed_close)
    with pytest.raises(ReadCleanupError):
        await read_daily_message_block(
            engine, read, metric="sleeps", request_id="synthetic-unexpected"
        )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("fault", ["disconnect", "lookup_timeout", "cancel"])
async def test_real_query_fault_cleans_pool_and_recovers(read_engines, fault):
    engine, control, _ = read_engines
    drivers = []
    started = asyncio.Event()

    def executing(conn, cursor, statement, *args):
        if "pg_sleep" in statement:
            started.set()

    event.listen(engine.sync_engine, "before_cursor_execute", executing)

    async def read(session):
        connection = await session.connection()
        drivers.append((await connection.get_raw_connection()).driver_connection)
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        if fault == "disconnect":
            async with control.connect() as killer:
                assert await killer.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                )
            return await session.scalar(text("SELECT 1"))
        await session.execute(text("SELECT pg_sleep(3)"))

    task = asyncio.create_task(
        read_daily_message_block(
            engine, read, metric="sleeps", request_id="synthetic-query-fault"
        )
    )
    if fault == "cancel":
        await started.wait()
        task.cancel("query-cancel")
    with pytest.raises(
        asyncio.CancelledError if fault == "cancel" else HealthDataLookupError
    ):
        await task
    assert engine.pool.checkedout() == 0
    assert drivers[0].is_closed()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
        assert (await connection.get_raw_connection()).driver_connection is not drivers[
            0
        ]


async def test_checkout_failure_does_not_discard_other_requests_lease(read_engines):
    engine, _, _ = read_engines
    holder = await engine.connect()
    driver = (await holder.get_raw_connection()).driver_connection

    async def read(session):
        pytest.fail("failed checkout must not execute a query")

    try:
        with pytest.raises(HealthDataLookupError) as caught:
            await read_daily_message_block(
                engine, read, metric="steps", request_id="synthetic-pool-full"
            )
        assert caught.value.metrics == ("steps",)
        assert not driver.is_closed() and await holder.scalar(text("SELECT 1")) == 1
    finally:
        await holder.close()
    assert engine.pool.checkedout() == 0


async def test_error_after_checkin_never_terminates_returned_driver(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    drivers = []
    close = AsyncConnection.close

    async def read(session):
        connection = await session.connection()
        drivers.append((await connection.get_raw_connection()).driver_connection)
        return 450

    async def failed_after_return(connection):
        await close(connection)
        raise OSError("synthetic fault after checkin")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncConnection, "close", failed_after_return)
        with pytest.raises(ReadCleanupError):
            await read_daily_message_block(
                engine, read, metric="sleeps", request_id="synthetic-late-fault"
            )
    assert not drivers[0].is_closed()
    async with engine.connect() as connection:
        assert (await connection.get_raw_connection()).driver_connection is drivers[0]
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_returned_then_closed_driver_is_not_our_disposal_success(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    close = AsyncConnection.close

    async def read(session):
        return 450

    async def failed_after_return(connection):
        driver = (await connection.get_raw_connection()).driver_connection
        await close(connection)
        # 반환 후 외부에서 드라이버가 끊긴 상태를 재현한다.
        driver.terminate()
        raise OSError("synthetic fault after return and disconnect")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncConnection, "close", failed_after_return)
        with pytest.raises(ReadCleanupError):
            await read_daily_message_block(
                engine, read, metric="sleeps", request_id="synthetic-returned-closed"
            )
    assert engine.pool.checkedout() == 0
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_score_failure_is_observable_without_exception_text(read_engines, caplog):
    engine, _, _ = read_engines

    async def read(session):
        return 450

    async def score(session, value):
        raise OSError("synthetic-secret-score-error")

    with caplog.at_level("INFO", logger="app.health.diagnostics"):
        result = await read_daily_message_block(
            engine,
            read,
            score_read=score,
            metric="sleeps",
            request_id="synthetic-score-observation",
        )
    assert result.value == 450 and result.score_error is not None
    record = next(
        r for r in caplog.records if r.message == "daily_message_read_finished"
    )
    assert getattr(record, "score_error_kind", None) == "HealthDataLookupError"
    assert getattr(record, "score_error_reason", None) == "LOOKUP_FAILED"
    assert "synthetic-secret-score-error" not in caplog.text


async def test_required_read_checks_elapsed_time_with_same_high_resolution_clock(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    clock = read_module.monotonic
    offset = 0.0

    async def read(session):
        nonlocal offset
        offset = 1.01
        return 450

    monkeypatch.setattr(read_module, "monotonic", lambda: clock() + offset)
    with pytest.raises(HealthDataLookupError):
        await read_daily_message_block(
            engine, read, metric="sleeps", request_id="synthetic-clock"
        )
    assert engine.pool.checkedout() == 0


async def test_partial_disposal_never_becomes_metric_failure(read_engines, monkeypatch):
    engine, _, _ = read_engines
    close = AsyncSession.close
    invalidate = AsyncConnection.invalidate
    connections = []

    async def read(session):
        connections.append(await session.connection())
        raw = await connections[-1].get_raw_connection()
        patch.setattr(type(raw), "invalidate", failed_invalidate)
        return 450

    async def failed_close(session):
        await close(session)
        if isinstance(session.bind, AsyncConnection):
            raise OSError("synthetic cleanup fault")

    def failed_invalidate(raw, *args, **kwargs):
        raise OSError("synthetic invalidation fault")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "close", failed_close)
            with pytest.raises(ReadCleanupError):
                await read_daily_message_block(
                    engine, read, metric="sleeps", request_id="synthetic-incomplete"
                )
        assert engine.pool.checkedout() == 0
    finally:
        # 장애 주입 해제 후 테스트가 소유한 연결만 회수한다.
        for connection in connections:
            if not connection.closed:
                await invalidate(connection)
                await connection.close()


async def test_disposal_timeout_finishes_tasks_before_whole_failure(
    read_engines, monkeypatch
):
    engine, _, _ = read_engines
    close = AsyncSession.close
    offset = 0.0

    async def read(session):
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        invalidate = type(raw).invalidate

        def expensive_invalidate(owned, *args, **kwargs):
            nonlocal offset
            invalidate(owned, *args, **kwargs)
            offset += 0.05

        patch.setattr(type(raw), "invalidate", expensive_invalidate)
        return 450

    async def failed_close(session):
        await close(session)
        if isinstance(session.bind, AsyncConnection):
            raise OSError("synthetic cleanup fault")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "close", failed_close)
        patch.setattr(read_module, "monotonic", lambda: monotonic() + offset)
        with pytest.raises(ReadCleanupError):
            await read_daily_message_block(
                engine,
                read,
                metric="sleeps",
                request_id="synthetic-disposal-timeout",
                disposal_timeout=0.03,
            )
    assert engine.pool.checkedout() == 0
    assert not [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("daily-message-cleanup") and not t.done()
    ]


async def test_cleanup_time_starts_before_session_close_and_does_not_reset(
    read_engines, monkeypatch, caplog
):
    engine, _, _ = read_engines
    close = AsyncSession.close

    async def delayed_close(session):
        if isinstance(session.bind, AsyncConnection):
            await asyncio.sleep(0.04)
        await close(session)

    async def read(session):
        return 450

    monkeypatch.setattr(AsyncSession, "close", delayed_close)
    with caplog.at_level("INFO", logger="app.health.diagnostics"):
        result = await read_daily_message_block(
            engine,
            read,
            metric="sleeps",
            request_id="synthetic-measurement",
            cleanup_timeout=0.2,
        )
    assert result.cleanup_seconds >= 0.04
    record = next(
        r for r in caplog.records if r.message == "daily_message_read_finished"
    )
    assert record.cleanup_seconds == result.cleanup_seconds
    assert not hasattr(record, "request_id")  # Non-UUID synthetic ID is omitted.


@pytest.mark.parametrize("fault", [OSError, ValueError])
async def test_score_and_cleanup_faults_follow_final_priority(
    read_engines, monkeypatch, fault
):
    engine, _, _ = read_engines
    close = AsyncSession.close

    async def read(session):
        return 450

    async def score(session, value):
        raise OSError("synthetic score fault")

    async def failed_close(session):
        await close(session)
        if isinstance(session.bind, AsyncConnection):
            raise fault("synthetic cleanup fault")

    monkeypatch.setattr(AsyncSession, "close", failed_close)
    with pytest.raises(HealthDataLookupError if fault is OSError else ReadCleanupError):
        await read_daily_message_block(
            engine,
            read,
            score_read=score,
            metric="sleeps",
            request_id="synthetic-score-cleanup",
        )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "scenario", ["normal", "close_fault", "cancel_cleanup_timeout"]
)
async def test_repeated_cleanup_measurements(
    read_engines, monkeypatch, caplog, record_property, scenario
):
    engine, control, _ = read_engines
    close = AsyncSession.close

    async def read(session):
        if scenario == "cancel_cleanup_timeout":
            raise asyncio.CancelledError("synthetic-measured-cancel")
        return 450

    async def closing(session):
        if isinstance(session.bind, AsyncConnection):
            if scenario == "cancel_cleanup_timeout":
                await asyncio.Event().wait()
            if scenario == "close_fault":
                await close(session)
                raise OSError("synthetic-measured-close")
        await close(session)

    async with control.connect() as connection:
        record_property(
            "postgres_version", await connection.scalar(text("SHOW server_version"))
        )
    monkeypatch.setattr(AsyncSession, "close", closing)
    caplog.clear()
    with caplog.at_level("INFO", logger="app.health.diagnostics"):
        for attempt in range(10):
            operation = read_daily_message_block(
                engine, read, metric="sleeps", request_id=f"synthetic-measure-{attempt}"
            )
            if scenario == "normal":
                assert (await operation).value == 450
            else:
                with pytest.raises(
                    asyncio.CancelledError
                    if scenario == "cancel_cleanup_timeout"
                    else HealthDataLookupError
                ):
                    await operation
            assert engine.pool.checkedout() == 0
            assert not [
                t
                for t in asyncio.all_tasks()
                if t.get_name().startswith("daily-message-cleanup") and not t.done()
            ]
    records = [r for r in caplog.records if r.message == "daily_message_read_finished"]
    assert len(records) == 10
    record_property("scenario", scenario)
    record_property("repetitions", len(records))
    for field in ("cleanup_seconds", "disposal_seconds", "block_seconds"):
        values = sorted(
            getattr(r, field) for r in records if getattr(r, field) is not None
        )
        if values:
            record_property(field + "_min", values[0])
            record_property(field + "_median", values[len(values) // 2])
            record_property(field + "_max", values[-1])
    for record in records:
        if scenario != "normal":
            assert record.driver_closed and record.pool_blocked and record.tasks_done
            assert record.disposal_seconds < 1
