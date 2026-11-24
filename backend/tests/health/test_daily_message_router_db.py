import asyncio
import io
import json
from datetime import datetime
from statistics import median
from time import perf_counter

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.health import repository, router
from app.health.diagnostics import health_logging
from app.health.models import (
    ActivityDaily,
    ExerciseSession,
    LifestyleScore,
    SleepSession,
    SyncState,
)
from tests.health.test_daily_message_repository import NOW, TODAY
from tests.health.test_daily_message_service_db import seed_message
from tests.health.test_router_db import metric_api_db as metric_api_db

pytestmark = pytest.mark.db
URL = "/api/v1/health/daily-message"


async def test_real_request_id_reaches_safe_stdout(client, message_db):
    factory, (owner, other), headers = message_db
    await seed_message(factory, owner)
    stream = io.StringIO()
    with health_logging(stream):
        response = await client.get(URL, headers=headers[0])
    assert response.status_code == 200
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    reads = [
        event for event in events if event["event"] == "daily_message_read_finished"
    ]
    assert len(reads) == 4
    assert all(
        event["request_id"] == response.headers["x-request-id"] for event in reads
    )
    assert all("block_seconds" in event for event in reads)
    for secret in (str(owner), str(other), headers[0]["Authorization"]):
        assert secret not in stream.getvalue()
    assert all("value" not in event and "user_id" not in event for event in events)


@pytest.fixture
def message_db(metric_api_db, app, monkeypatch):
    factory, owners, headers = metric_api_db

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock)
    monkeypatch.setattr(app.state, "engine", factory.kw["bind"], raising=False)
    return factory, owners, headers


@pytest.mark.parametrize(
    "case,status",
    [
        ("ready", "READY"),
        ("partial", "PARTIAL"),
        ("empty", "EMPTY"),
        ("waiting", "WAITING"),
    ],
)
async def test_real_auth_repository_selection_and_four_states(
    client, message_db, case, status
):
    factory, (owner, other), headers = message_db
    await seed_message(factory, owner)
    await seed_message(factory, other)
    async with factory.begin() as session:
        if case == "partial":
            await session.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner, SyncState.metric == "sleeps")
                .values(initial_sync_done=False, last_status="fail")
            )
        if case in ("empty", "waiting"):
            for model in (ActivityDaily, ExerciseSession, SleepSession):
                await session.execute(delete(model).where(model.end_user_id == owner))
        if case == "empty":
            await session.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(first_data_at=None)
            )
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == status and data["date"] == TODAY.isoformat()
    assert str(owner) not in response.text and str(other) not in response.text
    assert response.headers["cache-control"] == "no-store"
    if case == "ready":
        assert data["code"] == "BALANCED_RECORDS"
    if case == "partial":
        assert data["code"] == "ACTIVE_DAY" and data["dataAsOf"]["sleeps"] is None
        assert [n["code"] for n in data["notices"]] == ["DATA_NOT_READY", "SYNC_FAILED"]
    assert (await client.get(URL, headers=headers[1])).json()["data"][
        "status"
    ] == "READY"


@pytest.mark.parametrize(
    "case", ["none", "null", "null_fields", "mismatch", "invalid", "query"]
)
async def test_score_only_problem_does_not_fail_sleep(
    client, message_db, monkeypatch, caplog, case
):
    caplog.set_level("INFO", logger="app.health.diagnostics")
    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    async with factory.begin() as session:
        if case == "none":
            await session.execute(
                delete(LifestyleScore).where(LifestyleScore.end_user_id == owner)
            )
        elif case != "query":
            values = {
                "null": {"sleep_score": None},
                "null_fields": {"sleep_score": None, "sleep_minutes": None},
                "mismatch": {"sleep_minutes": 420},
                "invalid": {"sleep_score": 101},
            }[case]
            await session.execute(
                update(LifestyleScore)
                .where(LifestyleScore.end_user_id == owner)
                .values(**values)
            )
    if case == "query":

        async def fail(*args):
            raise OSError("synthetic-private")

        monkeypatch.setattr(repository, "find_sleep_score", fail)
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "READY" and data["code"] == "ACTIVE_DAY"
    assert data["dataAsOf"]["sleeps"] is not None and data["scoreComputedAt"] is None
    assert data["notices"] == []
    if case == "null_fields":
        assert not [
            record
            for record in caplog.records
            if record.msg == "daily_message_score_unavailable"
        ]


@pytest.mark.parametrize("forbidden", [False, True])
async def test_score_error_then_real_cleanup_fault_respects_precedence(
    client, message_db, monkeypatch, forbidden
):
    from app.health.errors import HealthDataForbiddenError

    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    original_close = AsyncSession.close
    original_score = repository.find_sleep_score
    score_failed = False

    async def fail_score(*args):
        nonlocal score_failed
        score_failed = True
        if forbidden:
            raise HealthDataForbiddenError()
        raise OSError("synthetic-private-score")

    async def close(session):
        if score_failed and isinstance(session.bind, AsyncConnection):
            raise OSError("synthetic-private-close")
        await original_close(session)

    monkeypatch.setattr(repository, "find_sleep_score", fail_score)
    monkeypatch.setattr(AsyncSession, "close", close)
    stream = io.StringIO()
    with health_logging(stream):
        response = await client.get(URL, headers=headers[0])
    assert response.status_code == (403 if forbidden else 200)
    if not forbidden:
        data = response.json()["data"]
        assert data["status"] == "PARTIAL" and data["code"] == "ACTIVE_DAY"
        assert data["dataAsOf"]["sleeps"] is None and data["scoreComputedAt"] is None
        assert [(notice["metric"], notice["code"]) for notice in data["notices"]] == [
            ("sleeps", "LOOKUP_FAILED")
        ]
    output = response.text + str(response.headers) + stream.getvalue()
    for secret in ("synthetic-private", str(owner), headers[0]["Authorization"]):
        assert secret not in output
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(events) == 4
    assert all(
        event["request_id"] == response.headers["x-request-id"] for event in events
    )
    closed = [
        event
        for event in events
        if event["event"] == "daily_message_read_finished"
        and event["metric"] == "sleeps"
    ][-1]
    assert closed["original_kind"] == (
        "HealthDataForbiddenError" if forbidden else None
    )
    assert closed["score_error_kind"] == (
        None if forbidden else "HealthDataLookupError"
    )
    assert closed["cleanup_kind"] == "OSError"
    assert closed["driver_closed"] and closed["pool_blocked"] and closed["tasks_done"]
    assert factory.kw["bind"].pool.checkedout() == 0
    score_failed = False
    monkeypatch.setattr(repository, "find_sleep_score", original_score)
    assert (await client.get(URL, headers=headers[0])).json()["data"][
        "code"
    ] == "BALANCED_RECORDS"


async def test_concurrent_members_keep_outcomes_and_notices_separate(
    client, message_db
):
    factory, (owner, other), headers = message_db
    await seed_message(factory, owner)
    await seed_message(factory, other)
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner, SyncState.metric == "sleeps")
            .values(initial_sync_done=False, last_status="fail")
        )
        await session.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == other)
            .values(steps=0, exercise_minutes=0, exercise_count=0)
        )
        await session.execute(
            delete(ExerciseSession).where(ExerciseSession.end_user_id == other)
        )
        await session.execute(
            update(LifestyleScore)
            .where(LifestyleScore.end_user_id == other)
            .values(sleep_score=None)
        )
    first, second = await asyncio.gather(
        *[client.get(URL, headers=auth) for auth in headers]
    )
    assert first.status_code == second.status_code == 200
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert first.json()["data"]["status"] == "PARTIAL"
    assert first.json()["data"]["code"] == "ACTIVE_DAY"
    assert len(first.json()["data"]["notices"]) == 2
    assert second.json()["data"]["status"] == "READY"
    assert second.json()["data"]["code"] == "SLEEP_RECORDED"
    assert second.json()["data"]["notices"] == []


async def test_unusable_sleep_value_is_excluded_instead_of_detail_fallback(
    client, message_db
):
    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    async with factory.begin() as session:
        await session.execute(
            update(SleepSession)
            .where(SleepSession.end_user_id == owner)
            .values(sleep_minutes=481)
        )
    data = (await client.get(URL, headers=headers[0])).json()["data"]
    assert data["status"] == "PARTIAL" and data["code"] == "ACTIVE_DAY"
    assert data["dataAsOf"]["sleeps"] is None


async def test_main_sleep_null_and_uncounted_exercise_provide_detail(
    client, message_db
):
    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    async with factory.begin() as session:
        await session.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner)
            .values(steps=0, exercise_minutes=0, exercise_count=0)
        )
        await session.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner)
            .values(counts_as_exercise=False)
        )
        await session.execute(
            update(SleepSession)
            .where(SleepSession.end_user_id == owner)
            .values(sleep_minutes=None)
        )
    data = (await client.get(URL, headers=headers[0])).json()["data"]
    assert data["code"] == "DETAIL_RECORDS" and data["status"] == "READY"
    assert data["area"] is None and data["scoreComputedAt"] is None


async def test_no_candidate_failure_precedes_unready_and_all404_is_whole(
    client, message_db
):
    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(initial_sync_done=False)
        )
    unready = await client.get(URL, headers=headers[0])
    assert unready.status_code == 503
    assert unready.json()["error"]["context"] == {
        "reason": "DATA_NOT_READY",
        "metrics": ["steps", "exercises", "sleeps"],
    }
    async with factory.begin() as session:
        await session.execute(
            delete(SyncState).where(
                SyncState.end_user_id == owner, SyncState.metric == "steps"
            )
        )
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": ["steps", "exercises", "sleeps"],
    }
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(last_error="http_404")
        )
    assert (await client.get(URL, headers=headers[0])).status_code == 409


@pytest.mark.parametrize("scope", ["exercises", "score"])
async def test_real_sql_cancel_is_scoped_and_pool_can_be_reused(
    client, message_db, app, monkeypatch, scope
):
    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    target = create_async_engine(
        factory.kw["bind"].url, pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    monkeypatch.setattr(app.state, "engine", target)
    ready = asyncio.Event()
    pid = None
    original = (
        repository.find_sleep_score
        if scope == "score"
        else repository.list_daily_records
    )

    async def delayed(session, *args):
        nonlocal pid
        if scope == "score" or args[0].metric == scope:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            ready.set()
            await session.execute(text("SELECT pg_sleep(5)"))
        return await original(session, *args)

    name = "find_sleep_score" if scope == "score" else "list_daily_records"
    monkeypatch.setattr(repository, name, delayed)
    task = asyncio.create_task(client.get(URL, headers=headers[0]))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        async with factory.kw["bind"].connect() as control:
            assert await control.scalar(
                text("SELECT pg_cancel_backend(:pid)"), {"pid": pid}
            )
        response = await asyncio.wait_for(task, 3)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == ("READY" if scope == "score" else "PARTIAL")
        assert data["code"] == ("ACTIVE_DAY" if scope == "score" else "STEPS_MILESTONE")
        assert data["dataAsOf"]["sleeps"] is not None
        assert target.pool.checkedout() == 0
        monkeypatch.setattr(repository, name, original)
        assert (await client.get(URL, headers=headers[0])).json()["data"][
            "code"
        ] == "BALANCED_RECORDS"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await target.dispose()


@pytest.mark.parametrize(
    "scenario", ["normal", "partial", "common_fault", "score_fault", "cancel_cleanup"]
)
async def test_request_elapsed_samples(
    client, message_db, monkeypatch, scenario, record_property
):
    factory, (owner, _), headers = message_db
    await seed_message(factory, owner)
    if scenario == "partial":
        async with factory.begin() as session:
            await session.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner, SyncState.metric == "sleeps")
                .values(initial_sync_done=False)
            )
    if scenario in ("common_fault", "score_fault"):

        async def fail(*args):
            raise OSError("synthetic measurement fault")

        monkeypatch.setattr(
            repository,
            "list_sync_states" if scenario == "common_fault" else "find_sleep_score",
            fail,
        )
    entered = asyncio.Event()
    if scenario == "cancel_cleanup":
        original = AsyncSession.close

        async def slow_close(session):
            if isinstance(session.bind, AsyncConnection):
                entered.set()
                await asyncio.sleep(2)
            await original(session)

        monkeypatch.setattr(AsyncSession, "close", slow_close)
    samples = []
    for _ in range(10):
        entered.clear()
        started = perf_counter()
        if scenario == "cancel_cleanup":
            task = asyncio.create_task(client.get(URL, headers=headers[0]))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        else:
            response = await client.get(URL, headers=headers[0])
            assert response.status_code == (503 if scenario == "common_fault" else 200)
        samples.append(perf_counter() - started)
        assert factory.kw["bind"].pool.checkedout() == 0
        assert not [
            t
            for t in asyncio.all_tasks()
            if t.get_name().startswith("daily-message-cleanup-") and not t.done()
        ]
    record_property(
        "elapsed_seconds",
        json.dumps(
            {
                "scenario": scenario,
                "count": 10,
                "min": min(samples),
                "median": median(samples),
                "max": max(samples),
            }
        ),
    )
