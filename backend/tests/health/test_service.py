import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import service
from app.health.calculations import DailyRecord
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
)
from app.health.repository import SyncRecord
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER


def state(**changes):
    values = dict(
        end_user_id=USER,
        metric="steps",
        initial_sync_done=True,
        last_data_at=NOW,
        first_data_at=NOW - timedelta(days=30),
        last_success_at=NOW,
        last_status="success",
        last_error=None,
    )
    return SyncRecord(**(values | changes))


@pytest.fixture
def reads(monkeypatch):
    lifecycle = []

    class Session:
        execute = AsyncMock()

        @asynccontextmanager
        async def begin(self):
            try:
                yield
            finally:
                lifecycle.append("transaction_closed")

    @asynccontextmanager
    async def factory():
        try:
            yield Session()
        finally:
            lifecycle.append("session_closed")

    monkeypatch.setattr(service, "_now", lambda: NOW)
    states = AsyncMock(return_value=[state()])
    rows = AsyncMock(return_value=[DailyRecord(TODAY, 0)])
    monkeypatch.setattr(service.repository, "list_sync_states", states)
    monkeypatch.setattr(service.repository, "list_daily_records", rows)
    monkeypatch.setattr(
        service.repository, "has_health_history", AsyncMock(return_value=False)
    )
    return factory, states, rows, lifecycle


async def call(factory):
    return await service.get_metric_result(
        USER, "steps", TODAY, TODAY, session_factory=factory
    )


async def test_recent_failure_returns_previous_result_and_success_time(reads):
    factory, states, _, lifecycle = reads
    states.return_value = [state(last_status="fail")]
    result = await call(factory)
    assert result.in_progress.value == 0
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.last_success_at == NOW
    assert lifecycle == ["transaction_closed", "session_closed"]
    assert "user_id" not in result.to_public().model_dump()


@pytest.mark.parametrize("ready", [True, False])
async def test_unlinked_precedes_readiness_and_skips_aggregate_query(reads, ready):
    factory, states, rows, lifecycle = reads
    states.return_value = [
        state(last_error="http_404", last_status="fail", initial_sync_done=ready)
    ]
    with pytest.raises(HealthDataNotLinkedError) as caught:
        await call(factory)
    assert caught.value.metrics == ("steps",)
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "state_rows,expected",
    [
        ([], HealthDataNotReadyError),
        (
            [state(initial_sync_done=False, last_status="fail", last_success_at=None)],
            HealthDataNotReadyError,
        ),
        ([state(end_user_id=uuid4())], HealthDataForbiddenError),
    ],
)
async def test_missing_unready_and_foreign_state_are_distinct(
    reads, state_rows, expected
):
    factory, states, rows, lifecycle = reads
    states.return_value = state_rows
    with pytest.raises(expected):
        await call(factory)
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_other_metrics_only_contribute_member_dates(reads):
    factory, states, _, _ = reads
    states.return_value = [
        state(),
        state(
            metric="weights",
            initial_sync_done=False,
            first_data_at=NOW - timedelta(days=90),
            last_data_at=NOW + timedelta(minutes=1),
        ),
    ]
    result = await call(factory)
    assert result.summary.linked_from == (NOW - timedelta(days=90)).date()
    assert result.data_status.synced_until == NOW + timedelta(minutes=1)


@pytest.mark.parametrize(
    "failure", [OSError("synthetic private text"), InvalidHealthData("invalid")]
)
async def test_known_lookup_failures_do_not_become_empty_results(reads, failure):
    factory, states, _, lifecycle = reads
    states.side_effect = failure
    with pytest.raises(HealthDataLookupError) as error:
        await call(factory)
    assert error.value.reason == "LOOKUP_FAILED" and error.value.metrics == ("steps",)
    assert str(error.value) == "HEALTH_DATA_UNAVAILABLE"
    assert lifecycle[-1] == "session_closed"


async def test_unexpected_programming_error_is_not_disguised(reads):
    factory, states, _, _ = reads
    states.side_effect = RuntimeError("synthetic bug")
    with pytest.raises(RuntimeError):
        await call(factory)


async def test_invalid_input_never_opens_a_session(reads):
    factory, states, _, lifecycle = reads
    with pytest.raises(ValidationError):
        await service.get_metric_result(
            USER, "heart_rate", TODAY, TODAY, session_factory=factory
        )
    states.assert_not_awaited()
    assert lifecycle == []


async def test_timeout_and_cancellation_release_resources(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    states.side_effect = wait
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert lifecycle[-1] == "session_closed"
    entered.clear()
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 10)
    task = asyncio.create_task(call(factory))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lifecycle[-1] == "session_closed"


async def test_synchronous_calculation_over_deadline_is_rejected(reads, monkeypatch):
    factory, _, _, _ = reads
    times = iter([0, 1.01])
    monkeypatch.setattr(service, "monotonic", lambda: next(times))
    with pytest.raises(HealthDataLookupError):
        await call(factory)


async def test_today_uses_kst_when_utc_date_is_still_yesterday(reads, monkeypatch):
    factory, _, _, _ = reads
    monkeypatch.setattr(
        service, "_now", lambda: datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
    )
    result = await call(factory)
    assert result.in_progress.date == TODAY


async def test_exercise_reads_raw_metric_state_and_preserves_recent_failure(reads):
    factory, states, rows, lifecycle = reads
    from tests.health.test_exercise import activity_record

    rows.return_value = [activity_record(TODAY)]
    states.return_value = [
        state(metric="steps", last_data_at=NOW + timedelta(minutes=1)),
        state(metric="exercise_sessions", last_status="fail"),
    ]
    result = await service.get_metric_result(
        USER, "exercises", TODAY, TODAY, session_factory=factory
    )
    assert result.metric == "exercises"
    assert result.in_progress.value == 0 and not result.in_progress.missing
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.last_data_at == result.data_status.last_success_at == NOW
    assert result.data_status.synced_until == NOW + timedelta(minutes=1)
    assert rows.await_args.args[1].metric == "exercises"
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "states_input,expected",
    [
        ([state()], HealthDataLookupError),
        ([state(metric="exercises")], HealthDataLookupError),
        (
            [state(metric="exercise_sessions", initial_sync_done=False)],
            HealthDataNotReadyError,
        ),
        (
            [state(metric="exercise_sessions", last_success_at=None)],
            HealthDataLookupError,
        ),
        (
            [state(metric="exercise_sessions", end_user_id=uuid4())],
            HealthDataForbiddenError,
        ),
    ],
)
async def test_exercise_invalid_state_fails_with_public_metric(
    reads, states_input, expected
):
    factory, states, rows, _ = reads
    states.return_value = states_input
    with pytest.raises(expected) as error:
        await service.get_metric_result(
            USER, "exercises", TODAY, TODAY, session_factory=factory
        )
    assert error.value.metrics == ("exercises",)
    rows.assert_not_awaited()


async def test_exercise_lookup_error_is_not_reported_as_zero(reads):
    factory, states, rows, _ = reads
    states.return_value = [state(metric="exercise_sessions")]
    rows.side_effect = InvalidHealthData("synthetic inconsistency")
    with pytest.raises(HealthDataLookupError) as error:
        await service.get_metric_result(
            USER, "exercises", TODAY, TODAY, session_factory=factory
        )
    assert error.value.metrics == ("exercises",)
