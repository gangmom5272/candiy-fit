import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import errors, repository, schemas, service
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


def values(**changes):
    return (
        dict(
            record_id="synthetic-vo2",
            date=TODAY,
            measured_at=NOW,
            value=Decimal("42.125"),
            measurement_method=0,
            source_app="synthetic.app",
            last_modified_at=NOW,
            ingested_at=NOW,
        )
        | changes
    )


async def call(factory, start=TODAY, end=TODAY):
    return await service.get_vo2_max_records(USER, start, end, session_factory=factory)


@pytest.fixture
def vo2_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric="vo2_maxes")]
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(service.repository, "list_vo2_max_records", rows, raising=False)
    return factory, states, rows, lifecycle


@pytest.mark.parametrize("length", [1, 31, 32])
async def test_period_validation_and_empty_records(vo2_reads, length):
    factory, states, _, _ = vo2_reads
    if length == 32:
        with pytest.raises(ValidationError):
            await call(factory, TODAY - timedelta(days=length - 1))
        states.assert_not_awaited()
    else:
        result = await call(factory, TODAY - timedelta(days=length - 1))
        assert result.records == [] and result.unit == "mL/kg/min"


@pytest.mark.parametrize(
    "start,end",
    [(TODAY, TODAY - timedelta(days=1)), (TODAY, TODAY + timedelta(days=1))],
)
async def test_invalid_dates_before_database(vo2_reads, start, end):
    factory, states, _, _ = vo2_reads
    with pytest.raises(ValidationError):
        await call(factory, start, end)
    states.assert_not_awaited()


async def test_invalid_user_before_database(vo2_reads):
    factory, states, _, _ = vo2_reads
    with pytest.raises(ValidationError):
        await service.get_vo2_max_records(
            "invalid", TODAY, TODAY, session_factory=factory
        )
    states.assert_not_awaited()


async def test_repository_rejects_foreign_owner():
    result = Mock()
    result.mappings.return_value = [{"end_user_id": uuid4()}]
    db = Mock(execute=AsyncMock(return_value=result))
    query = schemas.Vo2MaxQuery.model_validate(
        dict(user_id=USER, start=TODAY, end=TODAY), context={"today": TODAY}
    )
    with pytest.raises(errors.HealthDataForbiddenError):
        await repository.list_vo2_max_records(db, query)


@pytest.mark.parametrize("value", [0, 100, Decimal("42.125"), Decimal("0.000001")])
@pytest.mark.parametrize("method", range(6))
def test_value_and_method_boundaries_preserve_zero_and_precision(value, method):
    result = schemas.Vo2MaxRecord.model_validate(
        values(value=value, measurement_method=method)
    )
    assert result.value == value and result.measurement_method == method
    assert result.model_dump(mode="json")["value"] == float(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", -1),
        ("value", Decimal("100.01")),
        ("value", "42.5"),
        ("value", True),
        ("value", None),
        ("value", Decimal("NaN")),
        ("value", Decimal("Infinity")),
        ("measurement_method", None),
        ("measurement_method", -1),
        ("measurement_method", 6),
        ("measurement_method", "0"),
        ("measurement_method", True),
        ("measurement_method", 1.0),
        ("measurement_method", 1.5),
        ("record_id", ""),
        ("record_id", "x" * 65),
        ("source_app", ""),
        ("source_app", "x" * 129),
        ("measured_at", NOW.replace(tzinfo=None)),
        ("ingested_at", None),
        ("last_modified_at", NOW.replace(tzinfo=None)),
        ("date", TODAY - timedelta(days=1)),
    ],
)
def test_invalid_stored_fields_are_not_coerced(field, value):
    with pytest.raises(ValidationError):
        schemas.Vo2MaxRecord.model_validate(values(**{field: value}))


def test_required_keys_and_kst_date():
    for field in values():
        data = values()
        del data[field]
        with pytest.raises(ValidationError):
            schemas.Vo2MaxRecord.model_validate(data)
    midnight = datetime(2026, 10, 21, 15, tzinfo=UTC)
    result = schemas.Vo2MaxRecord.model_validate(values(measured_at=midnight))
    assert result.date == TODAY


async def test_current_records_can_be_newer_than_last_success(vo2_reads):
    factory, states, rows, lifecycle = vo2_reads
    previous = NOW - timedelta(days=2)
    states.return_value = [
        state(
            metric="vo2_maxes",
            last_status="fail",
            last_success_at=previous,
            last_data_at=previous,
        ),
        state(metric="heart_rates", initial_sync_done=False, last_data_at=NOW),
    ]
    rows.return_value = [schemas.Vo2MaxRecord.model_validate(values())]
    result = await call(factory)
    assert result.records[0].ingested_at > result.data_status.last_success_at
    assert result.data_status.last_data_at == previous
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.checked_at == NOW
    payload = result.model_dump(mode="json")
    assert set(payload) == {"user_id", "period", "unit", "records", "data_status"}
    assert set(payload["records"][0]) == set(values())
    assert set(payload["data_status"]) == {
        "last_sync_status",
        "last_data_at",
        "last_success_at",
        "checked_at",
    }
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "states,expected",
    [
        ([], errors.HealthDataLookupError),
        ([state(metric="sleeps")], errors.HealthDataLookupError),
        (
            [state(metric="vo2_maxes", initial_sync_done=False)],
            errors.HealthDataNotReadyError,
        ),
        (
            [state(metric="vo2_maxes", last_success_at=None)],
            errors.HealthDataLookupError,
        ),
        (
            [state(metric="vo2_maxes", last_error="http_404")],
            errors.HealthDataNotLinkedError,
        ),
        (
            [state(metric="vo2_maxes", end_user_id=uuid4())],
            errors.HealthDataForbiddenError,
        ),
    ],
)
async def test_only_vo2_readiness_gates_lookup(vo2_reads, states, expected):
    factory, source, rows, lifecycle = vo2_reads
    source.return_value = states
    with pytest.raises(expected) as caught:
        await call(factory)
    assert caught.value.metrics == ()
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("kind", ["limit", "invalid", "io", "bug", "cancel"])
async def test_failure_classification_and_cleanup(vo2_reads, kind):
    factory, _, rows, lifecycle = vo2_reads
    failure = {
        "limit": errors.Vo2MaxRecordLimitError(),
        "invalid": errors.InvalidHealthData("synthetic"),
        "io": OSError("synthetic"),
        "bug": RuntimeError("synthetic"),
        "cancel": asyncio.CancelledError(),
    }[kind]
    rows.side_effect = failure
    expected = (
        type(failure)
        if kind in ("limit", "bug", "cancel")
        else errors.HealthDataLookupError
    )
    with pytest.raises(expected):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_actual_timeout_cancels_and_closes(vo2_reads, monkeypatch):
    factory, _, rows, lifecycle = vo2_reads
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    rows.side_effect = slow
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]
