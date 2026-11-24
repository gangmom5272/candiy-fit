import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.health import service
from app.health.errors import HealthDataForbiddenError, HealthDataLookupError
from app.health.http import health_api_error
from app.users.schemas import AppConnection
from tests.health.test_calculations import NOW
from tests.health.test_data_status_response import METRICS
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


@pytest.fixture(autouse=True)
def arrival(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(service.repository, "find_first_arrival", lookup)
    return lookup


@pytest.fixture(autouse=True)
def connection(monkeypatch):
    lookup = AsyncMock(return_value=AppConnection(status="UNKNOWN", connected_at=None))
    monkeypatch.setattr(service.user_service, "get_app_connection", lookup)
    return lookup


@pytest.mark.parametrize("status", ["UNKNOWN", "NOT_CONNECTED", "CONNECTED"])
async def test_connection_is_independent_of_arrival_and_metric_readiness(
    reads, connection, status
):
    connection.return_value = AppConnection(
        status=status, connected_at=NOW if status == "CONNECTED" else None
    )
    result = await call(reads[0])
    assert result.app_connection == connection.return_value
    assert result.first_arrived_at is None
    connection.assert_awaited_once()
    assert connection.await_args.args[1] == USER


async def test_connection_storage_failure_is_not_unknown(reads, connection):
    from app.core.errors import TemporaryStorageError

    connection.side_effect = TemporaryStorageError("private")
    with pytest.raises(HealthDataLookupError):
        await call(reads[0])


@pytest.mark.parametrize("value", [None, NOW])
async def test_first_arrival_is_independent_of_ready(reads, arrival, value):
    factory, states, _, _ = reads
    states.return_value = []
    arrival.return_value = value
    result = await call(factory)
    assert result.first_arrived_at == value
    assert all(item.ready is None for item in result.metrics)


async def test_arrival_failure_is_not_unconfirmed(reads, arrival):
    arrival.side_effect = OSError("synthetic")
    with pytest.raises(HealthDataLookupError):
        await call(reads[0])
    assert reads[3] == ["transaction_closed", "session_closed"]


async def call(factory):
    return await service.get_data_status(USER, session_factory=factory)


async def test_five_states_keep_order_nulls_and_success_history(reads):
    factory, states, rows, lifecycle = reads
    states.return_value = [
        state(metric="exercise_sessions", last_data_at=None),
        state(metric="sleeps", last_status="fail"),
        state(metric="steps"),
        state(metric="weights"),
        state(
            metric="oxygen_saturations",
            initial_sync_done=False,
            last_status="success",
            last_data_at=None,
            last_success_at=None,
        ),
    ]
    result = await call(factory)
    assert result.user_id == USER and result.checked_at == NOW
    assert tuple(item.metric for item in result.metrics) == METRICS
    assert [item.ready for item in result.metrics] == [True, None, False, True, True]
    assert result.metrics[1].model_dump(exclude={"metric"}) == dict(
        ready=None, last_sync_status=None, last_data_at=None, last_success_at=None
    )
    assert result.metrics[2].last_sync_status == "success"
    assert result.metrics[2].last_success_at is None
    assert result.metrics[3].last_sync_status == "fail"
    assert result.metrics[3].last_data_at == result.metrics[3].last_success_at == NOW
    assert result.metrics[4].last_data_at is None
    assert result.metrics[4].last_success_at == NOW
    states.assert_awaited_once()
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_no_states_is_unknown_readiness_not_not_linked(reads):
    factory, states, _, _ = reads
    states.return_value = []
    result = await call(factory)
    assert len(result.metrics) == 5
    assert all(item.ready is None for item in result.metrics)


async def test_foreign_owner_is_rejected_even_on_an_unlisted_metric(reads):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric="weights", end_user_id=uuid4())]
    with pytest.raises(HealthDataForbiddenError):
        await call(factory)
    assert lifecycle[-1] == "session_closed"


@pytest.mark.parametrize(
    "changes",
    [
        {"last_status": "pending"},
        {"last_success_at": None},
        {"last_data_at": NOW.replace(tzinfo=None)},
    ],
)
async def test_one_invalid_state_fails_entire_result(reads, changes):
    factory, states, _, _ = reads
    states.return_value = [state(), state(metric="sleeps", **changes)]
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory)
    assert caught.value.metrics == METRICS
    public = health_api_error(caught.value)
    assert public.status_code == 503
    assert public.context == {"reason": "LOOKUP_FAILED", "metrics": list(METRICS)}


@pytest.mark.parametrize(
    "failure",
    [OSError("synthetic"), OperationalError(None, None, Exception("synthetic"))],
)
async def test_lookup_failure_does_not_become_unknown_states(reads, failure):
    factory, states, _, lifecycle = reads
    states.side_effect = failure
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("sqlstate", ["08006", "57014", "53300", "57P01", "42P01"])
async def test_db_error_classification_preserves_unexpected_errors(reads, sqlstate):
    factory, states, _, _ = reads
    original = Exception("synthetic")
    original.sqlstate = sqlstate
    error = DBAPIError(None, None, original)
    states.side_effect = error
    expected = DBAPIError if sqlstate == "42P01" else HealthDataLookupError
    with pytest.raises(expected):
        await call(factory)


@pytest.mark.parametrize(
    "failure", [RuntimeError("synthetic"), ProgrammingError(None, None, Exception())]
)
async def test_programming_errors_are_not_disguised_as_lookup_failures(reads, failure):
    factory, states, _, _ = reads
    states.side_effect = failure
    with pytest.raises(type(failure)):
        await call(factory)


async def test_timeout_and_cancellation_close_read_transaction(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    states.side_effect = wait
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]
    entered.clear()
    lifecycle.clear()
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 10)
    task = asyncio.create_task(call(factory))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_checked_at_is_observed_after_read_and_deadline_is_checked(
    reads, monkeypatch
):
    factory, states, _, _ = reads

    async def read(*args):
        monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(seconds=1))
        return []

    states.side_effect = read
    assert (await call(factory)).checked_at == NOW + timedelta(seconds=1)
    times = iter([0, 1.01])
    monkeypatch.setattr(service, "monotonic", lambda: next(times))
    with pytest.raises(HealthDataLookupError):
        await call(factory)
