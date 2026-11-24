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
    return schemas.BodyDailyRecord.model_validate(
        dict(
            date=day,
            value=Decimal("70.25"),
            measured_at=NOW.replace(day=day.day),
            computed_at=NOW,
        )
        | changes
    )


async def call(factory, metric="weights", start=TODAY, end=TODAY):
    return await service.get_body_daily(
        USER, metric, start, end, session_factory=factory
    )


@pytest.fixture
def body_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric="weights")]
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(service.repository, "list_body_daily", rows, raising=False)
    return factory, states, rows, lifecycle


@pytest.mark.parametrize(
    "metric,unit,value",
    [
        ("weights", "kg", "70.25"),
        ("body_fats", "%", "22.35"),
        ("basal_metabolic_rates", "kcal/day", "1500.45"),
        ("heights", "m", "1.75"),
    ],
)
async def test_metric_values_missing_days_and_selected_source_status(
    body_reads, metric, unit, value
):
    factory, states, rows, lifecycle = body_reads
    states.return_value = [
        state(metric=metric, last_status="fail", last_data_at=NOW - timedelta(days=5)),
        state(metric="steps", initial_sync_done=False, last_data_at=NOW),
    ]
    rows.return_value = [record(value=Decimal(value))]
    result = await call(factory, metric, TODAY - timedelta(days=1))
    assert result.metric == metric and result.unit == unit and result.user_id == USER
    assert [day.date for day in result.days] == [TODAY - timedelta(days=1), TODAY]
    assert result.days[0].model_dump() == dict(
        date=TODAY - timedelta(days=1),
        value=None,
        measured_at=None,
        computed_at=None,
        is_in_progress=False,
        reason="NO_RECORD",
        wear_hours=None,
        is_well_worn=None,
    )
    assert result.days[1].value == Decimal(value) and result.days[1].reason is None
    assert result.days[1].is_in_progress
    payload = result.model_dump(mode="json")
    assert payload["days"][1]["value"] == float(value)
    assert payload["days"][1]["measured_at"] == "2026-10-22T05:00:00Z"
    assert set(payload["data_status"]) == {
        "last_sync_status",
        "last_data_at",
        "last_success_at",
        "checked_at",
    }
    assert result.data_status.last_data_at == NOW - timedelta(days=5)
    assert (
        result.data_status.last_sync_status == "fail"
        and result.data_status.checked_at == NOW
    )
    assert lifecycle == ["transaction_closed", "session_closed"]
    rows.assert_awaited_once()


@pytest.mark.parametrize(
    "states,error",
    [
        ([], HealthDataLookupError),
        ([state(metric="heights")], HealthDataLookupError),
        ([state(metric="weights", initial_sync_done=False)], HealthDataNotReadyError),
        ([state(metric="weights", last_success_at=None)], HealthDataLookupError),
        ([state(metric="weights", end_user_id=uuid4())], HealthDataForbiddenError),
        ([state(metric="weights", last_error="http_404")], HealthDataNotLinkedError),
    ],
)
async def test_status_errors_do_not_read_body(body_reads, states, error):
    factory, source, rows, lifecycle = body_reads
    source.return_value = states
    with pytest.raises(error) as raised:
        await call(factory)
    assert raised.value.metrics == ()
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("length", [1, 366, 367])
async def test_inclusive_period_limit_and_normal_empty_history(body_reads, length):
    factory, states, _, _ = body_reads
    start = TODAY - timedelta(days=length - 1)
    if length == 367:
        with pytest.raises(ValidationError):
            await call(factory, start=start)
        states.assert_not_awaited()
    else:
        result = await call(factory, start=start)
        assert len(result.days) == length
        assert all(day.reason == "NO_RECORD" for day in result.days)


@pytest.mark.parametrize(
    "metric,start,end",
    [
        ("steps", TODAY, TODAY),
        ("weight", TODAY, TODAY),
        ("weights", TODAY, TODAY - timedelta(days=1)),
        ("weights", TODAY, TODAY + timedelta(days=1)),
    ],
)
async def test_invalid_input_fails_before_database(body_reads, metric, start, end):
    factory, states, _, _ = body_reads
    with pytest.raises(ValidationError):
        await call(factory, metric, start, end)
    states.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", None),
        ("value", 0),
        ("value", -1),
        ("value", True),
        ("value", "70.25"),
        ("value", Decimal("NaN")),
        ("value", Decimal("Infinity")),
        ("measured_at", None),
        ("computed_at", None),
        ("measured_at", NOW.replace(tzinfo=None)),
        ("computed_at", NOW.replace(tzinfo=None)),
        ("measured_at", NOW - timedelta(days=1)),
    ],
)
def test_stored_invalid_values_never_become_missing(field, value):
    with pytest.raises(ValidationError):
        record(**{field: value})


@pytest.mark.parametrize("field", ["value", "measured_at", "computed_at"])
def test_nullable_response_keys_are_still_required(field):
    values = dict(
        date=TODAY,
        value=None,
        measured_at=None,
        computed_at=None,
        is_in_progress=True,
        reason="NO_RECORD",
    )
    del values[field]
    with pytest.raises(ValidationError):
        schemas.BodyDay.model_validate(values)


async def test_kst_measurement_date_and_today_at_utc_boundary(body_reads, monkeypatch):
    factory, _, rows, _ = body_reads
    instant = datetime(2026, 10, 21, 15, tzinfo=UTC)
    monkeypatch.setattr(service, "_now", lambda: instant)
    rows.return_value = [record(measured_at=instant)]
    result = await call(factory)
    assert result.days[0].is_in_progress and result.days[0].measured_at == instant


@pytest.mark.parametrize("kind", ["duplicate", "outside"])
async def test_invalid_returned_dates_are_lookup_failure(body_reads, kind):
    factory, _, rows, _ = body_reads
    rows.return_value = (
        [record(), record()]
        if kind == "duplicate"
        else [record(TODAY - timedelta(days=1))]
    )
    with pytest.raises(HealthDataLookupError):
        await call(factory)


@pytest.mark.parametrize(
    "error",
    [
        OSError("synthetic"),
        TimeoutError(),
        RuntimeError("synthetic"),
        asyncio.CancelledError(),
    ],
)
async def test_error_and_cancellation_cleanup(body_reads, error):
    factory, _, rows, lifecycle = body_reads
    rows.side_effect = error
    expected = (
        type(error)
        if isinstance(error, (RuntimeError, asyncio.CancelledError))
        else HealthDataLookupError
    )
    with pytest.raises(expected):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_actual_timeout_closes_session(body_reads, monkeypatch):
    factory, _, rows, lifecycle = body_reads
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    rows.side_effect = slow
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]
