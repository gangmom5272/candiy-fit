import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
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

CASES = [
    ("oxygen_saturations", "percentage", "%", 100, 65.125),
    ("weights", "weight_kg", "kg", 1000, 20.125),
    ("body_fats", "percentage", "%", 100, 1.125),
    ("basal_metabolic_rates", "bmr_kcal_per_day", "kcal/day", 10000, 400.125),
    ("heights", "height_m", "m", 3, 0.5125),
]
METRICS = [case[0] for case in CASES]


def values(**changes):
    return (
        dict(
            record_id="synthetic-measurement",
            date=TODAY,
            measured_at=NOW,
            value=Decimal("0.125"),
            source_app="synthetic.app",
            last_modified_at=NOW,
            ingested_at=NOW,
        )
        | changes
    )


async def call(factory, metric="weights", start=TODAY, end=TODAY):
    return await service.get_measurement_records(
        USER, metric, start, end, session_factory=factory
    )


@pytest.fixture
def measurement_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric=metric) for metric in METRICS]
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(repository, "list_measurement_records", rows, raising=False)
    return factory, states, rows, lifecycle


@pytest.mark.parametrize("metric,key,unit,maximum,value", CASES)
async def test_units_fields_and_unfiltered_values(
    measurement_reads, metric, key, unit, maximum, value
):
    factory, states, rows, lifecycle = measurement_reads
    states.return_value = [
        state(
            metric=metric, last_status="fail", last_success_at=NOW - timedelta(days=1)
        ),
        state(metric="heart_rates", initial_sync_done=False),
    ]
    rows.return_value = [schemas.MeasurementRecord(**values(value=Decimal(str(value))))]
    result = await call(factory, metric)
    assert result.metric == metric and result.unit == unit
    assert result.records[0].value == Decimal(str(value))
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.checked_at == NOW
    assert result.records[0].ingested_at > result.data_status.last_success_at
    payload = result.model_dump(mode="json")
    assert set(payload) == {
        "user_id",
        "metric",
        "period",
        "unit",
        "records",
        "data_status",
    }
    assert set(payload["records"][0]) == set(values())
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("length", [1, 31, 32, 366])
async def test_period_limit_and_empty_records(measurement_reads, length):
    factory, states, _, _ = measurement_reads
    if length > 31:
        with pytest.raises(ValidationError):
            await call(factory, start=TODAY - timedelta(days=length - 1))
        states.assert_not_awaited()
    else:
        result = await call(factory, start=TODAY - timedelta(days=length - 1))
        assert result.records == []


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "vo2_maxes"},
        {"metric": "heart_rates"},
        {"metric": ""},
        {"user_id": "invalid"},
        {"start": TODAY + timedelta(days=1)},
        {"end": TODAY + timedelta(days=1)},
        {"end": TODAY - timedelta(days=1)},
        {"start": date.min, "end": date.min},
    ],
)
async def test_invalid_input_before_database(measurement_reads, changes):
    factory, states, _, lifecycle = measurement_reads
    arguments = dict(user_id=USER, metric="weights", start=TODAY, end=TODAY) | changes
    with pytest.raises(ValidationError):
        await service.get_measurement_records(**arguments, session_factory=factory)
    states.assert_not_awaited()
    assert lifecycle == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", -1),
        ("value", "0.125"),
        ("value", True),
        ("value", None),
        ("value", Decimal("NaN")),
        ("value", Decimal("Infinity")),
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
def test_invalid_record_fields(field, value):
    with pytest.raises(ValidationError):
        schemas.MeasurementRecord(**values(**{field: value}))


def test_required_keys_and_kst_midnight():
    for field in values():
        data = values()
        del data[field]
        with pytest.raises(ValidationError):
            schemas.MeasurementRecord(**data)
    instant = datetime(2026, 10, 21, 15, tzinfo=UTC)
    result = schemas.MeasurementRecord(**values(measured_at=instant))
    assert result.date == TODAY
    assert result.model_dump(mode="json")["measured_at"] == "2026-10-21T15:00:00Z"


@pytest.mark.parametrize("metric,key,unit,maximum,value", CASES)
async def test_result_rejects_wrong_unit_and_metric_bounds(
    measurement_reads, metric, key, unit, maximum, value
):
    factory, _, rows, _ = measurement_reads
    for number in (0, maximum, Decimal("0.000001")):
        rows.return_value = [schemas.MeasurementRecord(**values(value=number))]
        result = await call(factory, metric)
        assert result.records[0].value == number
        with pytest.raises(ValidationError):
            schemas.MeasurementRecordsResult.model_validate(
                result.model_dump() | {"unit": "m" if unit != "m" else "kg"}
            )
    rows.return_value = [schemas.MeasurementRecord(**values(value=maximum + 0.1))]
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, metric)


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize(
    "kind", ["missing", "other", "unready", "no_success", "unlinked", "foreign"]
)
async def test_readiness_and_error_metrics(measurement_reads, metric, kind):
    factory, states, rows, lifecycle = measurement_reads
    states.return_value = {
        "missing": [],
        "other": [state(metric="vo2_maxes")],
        "unready": [state(metric=metric, initial_sync_done=False)],
        "no_success": [state(metric=metric, last_success_at=None)],
        "unlinked": [state(metric=metric, last_error="http_404")],
        "foreign": [state(metric=metric, end_user_id=uuid4())],
    }[kind]
    expected = {
        "unready": errors.HealthDataNotReadyError,
        "unlinked": errors.HealthDataNotLinkedError,
        "foreign": errors.HealthDataForbiddenError,
    }.get(kind, errors.HealthDataLookupError)
    with pytest.raises(expected) as caught:
        await call(factory, metric)
    assert caught.value.metrics == ((metric,) if metric == "oxygen_saturations" else ())
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("metric", ["oxygen_saturations", "weights"])
@pytest.mark.parametrize("kind", ["limit", "invalid", "io", "bug", "cancel"])
async def test_failure_classification_and_cleanup(measurement_reads, metric, kind):
    factory, _, rows, lifecycle = measurement_reads
    failure = {
        "limit": errors.MeasurementRecordLimitError(),
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
    with pytest.raises(expected) as caught:
        await call(factory, metric)
    if expected is errors.HealthDataLookupError:
        assert caught.value.metrics == (
            (metric,) if metric == "oxygen_saturations" else ()
        )
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_timeout_cancels_and_closes(measurement_reads, monkeypatch):
    factory, _, rows, lifecycle = measurement_reads
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    rows.side_effect = slow
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, "oxygen_saturations")
    assert caught.value.metrics == ("oxygen_saturations",)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("metric", ["oxygen_saturations", "weights"])
async def test_repository_owner_error_metrics(metric):
    result = Mock()
    result.mappings.return_value = [{"end_user_id": uuid4()}]
    db = Mock(execute=AsyncMock(return_value=result))
    query = schemas.MeasurementRecordsQuery.model_validate(
        dict(user_id=USER, metric=metric, start=TODAY, end=TODAY),
        context={"today": TODAY},
    )
    with pytest.raises(errors.HealthDataForbiddenError) as caught:
        await repository.list_measurement_records(db, query)
    assert caught.value.metrics == ((metric,) if metric == "oxygen_saturations" else ())


@pytest.mark.parametrize(
    "instant",
    [
        datetime.min.replace(tzinfo=timezone(timedelta(hours=9))),
        datetime.max.replace(tzinfo=timezone(timedelta(hours=-9))),
    ],
)
async def test_stored_time_overflow_is_lookup_failure(reads, monkeypatch, instant):
    factory, states, _, _ = reads
    states.return_value = [state(metric="weights")]
    # asyncpg normally returns UTC. Simulate a malformed timestamp at the repository boundary.
    row = values(last_modified_at=instant) | {"end_user_id": USER, "end_at": None}
    row.pop("date")
    result = Mock()
    result.mappings.return_value = [row]
    db = Mock(execute=AsyncMock(return_value=result))
    original = repository.list_measurement_records

    async def read(_session, query):
        return await original(db, query)

    monkeypatch.setattr(repository, "list_measurement_records", read)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory)
