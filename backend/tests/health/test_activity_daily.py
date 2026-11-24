import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import schemas, service
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


def record(day=TODAY, **changes):
    return schemas.ActivityDailyRecord(
        **(
            dict(
                date=day,
                exercise_minutes=0,
                exercise_count=0,
                exercise_calories_kcal=None,
                exercise_distance_m=None,
                computed_at=NOW,
            )
            | changes
        )
    )


async def call(factory, start=TODAY, end=TODAY):
    return await service.get_activity_daily(USER, start, end, session_factory=factory)


@pytest.fixture
def activity_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric="exercise_sessions")]
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(service.repository, "list_activity_daily", rows, raising=False)
    return factory, states, rows, lifecycle


async def test_all_dates_preserve_absent_row_zero_partial_values_and_today(
    activity_reads,
    monkeypatch,
):
    factory, states, rows, lifecycle = activity_reads
    history_read = AsyncMock()
    monkeypatch.setattr(service.repository, "has_health_history", history_read)
    start = TODAY - timedelta(days=3)
    states.return_value += [
        state(
            metric="calories_burned",
            initial_sync_done=False,
            last_success_at=None,
            last_status="fail",
        ),
        state(metric="heart_rates", initial_sync_done=False),
    ]
    rows.return_value = [
        record(
            TODAY,
            exercise_minutes=30,
            exercise_count=1,
            exercise_calories_kcal=Decimal("12.3"),
            exercise_distance_m=Decimal("0.0"),
        ),
        record(start),
        record(start + timedelta(days=2), exercise_distance_m=Decimal("123.4")),
    ]
    result = await call(factory, start)
    assert [day.date for day in result.days] == [
        start + timedelta(days=i) for i in range(4)
    ]
    assert [day.exercise_minutes for day in result.days] == [0, None, 0, 30]
    assert [day.reason for day in result.days] == [None, "NO_RECORD", None, None]
    assert result.days[1].computed_at is None
    assert result.days[2].exercise_count == 0 and result.days[
        2
    ].exercise_distance_m == Decimal("123.4")
    assert result.days[3].exercise_calories_kcal == Decimal("12.3")
    assert [day.is_in_progress for day in result.days] == [False, False, False, True]
    assert result.source_statuses["exercise_sessions"].ready is True
    assert result.source_statuses["calories_burned"].ready is False
    assert result.source_statuses["distances"].ready is None
    payload = result.model_dump(mode="json")
    assert payload["days"][3]["exercise_calories_kcal"] == 12.3
    assert payload["days"][3]["computed_at"] == "2026-10-22T05:00:00Z"
    assert result.checked_at == NOW
    assert lifecycle == ["transaction_closed", "session_closed"]
    rows.assert_awaited_once()
    history_read.assert_not_awaited()


async def test_recent_failure_returns_stored_values_and_source_times(activity_reads):
    factory, states, rows, _ = activity_reads
    states.return_value = [
        state(metric=name, last_status="fail")
        for name in ("exercise_sessions", "calories_burned", "distances")
    ]
    rows.return_value = [record(exercise_minutes=10, exercise_count=1)]
    result = await call(factory)
    assert result.days[0].exercise_minutes == 10
    assert all(
        s.ready and s.last_sync_status == "fail" and s.last_success_at == NOW
        for s in result.source_statuses.values()
    )


@pytest.mark.parametrize(
    "states,error",
    [
        ([state(metric="steps")], HealthDataLookupError),
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
        (
            [state(metric="exercise_sessions", last_error="http_404")],
            HealthDataNotLinkedError,
        ),
        (
            [
                state(metric="exercise_sessions"),
                state(metric="distances", last_success_at=None),
            ],
            HealthDataLookupError,
        ),
    ],
)
async def test_invalid_or_unready_source_does_not_read_activity(
    activity_reads, monkeypatch, states, error
):
    factory, source, rows, _ = activity_reads
    source.return_value = states
    history_read = AsyncMock()
    monkeypatch.setattr(service.repository, "has_health_history", history_read)
    with pytest.raises(error) as raised:
        await call(factory)
    assert raised.value.metrics == ("exercises",)
    rows.assert_not_awaited()
    history_read.assert_not_awaited()


@pytest.mark.parametrize("history", [False, True])
async def test_initial_collection_distinguishes_missing_history(
    activity_reads, monkeypatch, history
):
    factory, states, rows, lifecycle = activity_reads
    states.return_value = []
    history_read = AsyncMock(return_value=history)
    monkeypatch.setattr(service.repository, "has_health_history", history_read)
    with pytest.raises(
        HealthDataLookupError if history else HealthDataNotReadyError
    ) as error:
        await call(factory)
    assert error.value.metrics == ("exercises",)
    history_read.assert_awaited_once()
    assert history_read.await_args.args[1] == USER
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "failure,expected",
    [
        (OSError("synthetic"), HealthDataLookupError),
        (TimeoutError(), HealthDataLookupError),
        (RuntimeError("synthetic"), RuntimeError),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_history_failure_is_not_initial_collection(
    activity_reads, monkeypatch, failure, expected
):
    factory, states, rows, lifecycle = activity_reads
    states.return_value = []
    history_read = AsyncMock(side_effect=failure)
    monkeypatch.setattr(service.repository, "has_health_history", history_read)
    with pytest.raises(expected) as error:
        await call(factory)
    if expected is HealthDataLookupError:
        assert error.value.metrics == ("exercises",)
    history_read.assert_awaited_once()
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_history_lookup_uses_existing_timeout(activity_reads, monkeypatch):
    factory, states, rows, lifecycle = activity_reads
    states.return_value = []
    cancelled = []

    async def slow_history(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(service.repository, "has_health_history", slow_history)
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HealthDataLookupError) as error:
        await call(factory)
    assert error.value.metrics == ("exercises",)
    assert cancelled == [True]
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("length", [1, 366, 367])
async def test_inclusive_limit(activity_reads, length):
    factory, states, _, _ = activity_reads
    start = TODAY - timedelta(days=length - 1)
    if length > 366:
        with pytest.raises(ValidationError):
            await call(factory, start)
        states.assert_not_awaited()
    else:
        result = await call(factory, start)
        assert len(result.days) == length
        assert all(day.reason == "NO_RECORD" for day in result.days)


@pytest.mark.parametrize(
    "start,end",
    [(TODAY, TODAY - timedelta(days=1)), (TODAY, TODAY + timedelta(days=1))],
)
async def test_invalid_dates_fail_before_database(activity_reads, start, end):
    factory, states, _, _ = activity_reads
    with pytest.raises(ValidationError):
        await call(factory, start, end)
    states.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [OSError("synthetic"), TimeoutError(), RuntimeError("synthetic")]
)
async def test_lookup_failures_are_not_missing(activity_reads, error):
    factory, _, rows, lifecycle = activity_reads
    rows.side_effect = error
    with pytest.raises(
        RuntimeError if type(error) is RuntimeError else HealthDataLookupError
    ):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("exercise_minutes", -1),
        ("exercise_minutes", True),
        ("exercise_minutes", 1.5),
        ("exercise_count", -1),
        ("exercise_count", "1"),
        ("exercise_count", True),
        ("exercise_calories_kcal", Decimal("NaN")),
        ("exercise_calories_kcal", Decimal("-1")),
        ("exercise_distance_m", float("inf")),
        ("exercise_distance_m", "12.3"),
        ("exercise_distance_m", True),
        ("computed_at", NOW.replace(tzinfo=None)),
    ],
)
def test_invalid_stored_activity_is_not_coerced(field, value):
    with pytest.raises(ValidationError):
        record(**{field: value})


async def test_actual_timeout_cancels_lookup_and_closes_session(
    activity_reads, monkeypatch
):
    factory, _, rows, lifecycle = activity_reads
    cancelled = []

    async def slow_read(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    rows.side_effect = slow_read
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_caller_cancellation_is_not_translated(activity_reads):
    factory, _, rows, lifecycle = activity_reads
    rows.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_today_uses_kst_at_utc_date_boundary(activity_reads, monkeypatch):
    factory, _, _, _ = activity_reads
    instant = datetime(2026, 10, 21, 15, tzinfo=UTC)
    monkeypatch.setattr(service, "_now", lambda: instant)
    result = await call(factory)
    assert result.days[0].date == TODAY and result.days[0].is_in_progress
    assert result.checked_at == instant
