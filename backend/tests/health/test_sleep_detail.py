"""237: 저장된 주 수면 상세와 기존 조회 계약의 합성 회귀 검사."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from candiy_pipeline.rules import Sample, heart_stats
from pydantic import ValidationError

from app.coaching.schemas import evidence_data, partition_evidence
from app.health import repository
from app.health.calculations import sleep_record
from app.health.rest_schemas import metric_response
from app.health.schemas import Night
from coach_ai.ports import MetricResult
from tests.coaching.fakes import synthetic_evidence, synthetic_evidence_info
from tests.health.test_calculations import TODAY, calculate
from tests.health.test_schemas import USER, query
from tests.health.test_sleep import night

DETAIL = {
    "session_minutes": 480,
    "unspecified_minutes": 0,
    "unknown_minutes": None,
    "avg_heart_rate": Decimal("58.3"),
    "min_heart_rate": 45,
    "max_heart_rate": 81,
    "spo2_avg": Decimal("95.7"),
    "spo2_min": Decimal("90.5"),
    "spo2_max": Decimal("99.0"),
    "spo2_count": 25,
    "spo2_low_count": 0,
}


def detailed_night(**changes):
    return Night.model_validate(night(21).model_dump() | DETAIL | changes)


def sleep_row(**changes):
    values = night(21).model_dump() | DETAIL
    for public, column in (
        ("avg_heart_rate", "hr_avg"),
        ("min_heart_rate", "hr_min"),
        ("max_heart_rate", "hr_max"),
    ):
        values[column] = values.pop(public)
    return dict(end_user_id=USER, is_main=True, **values) | changes


async def read_row(metric, **changes):
    result = Mock()
    row = sleep_row(**changes)
    for column, public in (
        ("hr_avg", "avg_heart_rate"),
        ("hr_min", "min_heart_rate"),
        ("hr_max", "max_heart_rate"),
    ):
        row[public] = row.pop(column)
    result.mappings.return_value = [row]
    session = Mock(execute=AsyncMock(return_value=result))
    records = await repository.list_daily_records(
        session, query(metric=metric, start=TODAY - timedelta(days=1))
    )
    if metric == "oxygen_saturations":
        selected = str(session.execute.call_args.args[0])
        assert "hr_avg" not in selected and "session_minutes" not in selected
    return records


def test_detail_preserves_stored_values_and_existing_sleep_statistics():
    result = calculate([sleep_record(detailed_night())], metric="sleeps")
    assert result.summary.avg == 400
    assert result.summary.days_with_data == 1
    assert result.summary.daily_min.value == result.summary.daily_max.value == 400
    assert result.detail.nights[0].model_dump().items() >= DETAIL.items()
    payload = metric_response(result.to_public()).model_dump(mode="json")
    saved = payload["detail"]["nights"][0]
    assert saved["sessionMinutes"] == 480
    assert saved["avgHeartRate"] == 58.3
    assert saved["spo2Avg"] == 95.7
    assert saved["spo2LowCount"] == saved["unspecifiedMinutes"] == 0
    assert saved["unknownMinutes"] is None
    assert "avg_heart_rate" not in saved


def test_missing_optional_measurements_preserve_null_sleep_and_session():
    values = dict.fromkeys(DETAIL)
    values["session_minutes"] = 480
    result = calculate(
        [sleep_record(detailed_night(**values, sleep_minutes=None))], metric="sleeps"
    )
    assert result.summary.avg is None
    assert result.detail.nights[0].session_minutes == 480
    assert result.detail.nights[0].model_dump().items() >= values.items()


@pytest.mark.parametrize("field", DETAIL)
def test_new_results_require_every_detail_key_even_when_nullable(field):
    values = night(21).model_dump() | DETAIL
    del values[field]
    with pytest.raises(ValidationError) as raised:
        Night.model_validate(values)
    assert any(
        e["loc"] == (field,) and e["type"] == "missing" for e in raised.value.errors()
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"session_minutes": -1},
        {"session_minutes": True},
        {"unspecified_minutes": -1},
        {"unknown_minutes": 1.5},
        {"spo2_count": -1},
        {"spo2_count": 0},
        {"spo2_count": True},
        {"spo2_low_count": -1},
        {"spo2_low_count": 26},
        {"avg_heart_rate": Decimal("NaN")},
        {"avg_heart_rate": True},
        {"spo2_avg": "95.7"},
        {"min_heart_rate": -1},
        {"min_heart_rate": 70},
        {"max_heart_rate": 50},
        {"spo2_avg": Decimal("Infinity")},
        {"spo2_min": 98},
        {"spo2_max": 90},
    ],
)
def test_invalid_detail_is_rejected_instead_of_becoming_null(changes):
    with pytest.raises(ValidationError):
        detailed_night(**changes)


@pytest.mark.parametrize("sample", [60.4, 60.5, 60.6, 61.5])
def test_heart_detail_accepts_pipeline_rounding_without_changing_stored_values(sample):
    stats = heart_stats([Sample(night(21).start_at, sample)])
    result = detailed_night(
        avg_heart_rate=Decimal(str(stats["avg"])),
        min_heart_rate=stats["min"],
        max_heart_rate=stats["max"],
    )
    assert result.avg_heart_rate == Decimal(str(stats["avg"]))
    assert result.min_heart_rate == stats["min"]
    assert result.max_heart_rate == stats["max"]


@pytest.mark.parametrize(
    "changes",
    [
        {"avg_heart_rate": Decimal("59.4"), "min_heart_rate": 60, "max_heart_rate": 60},
        {"avg_heart_rate": Decimal("60.6"), "min_heart_rate": 60, "max_heart_rate": 60},
        {"avg_heart_rate": None, "min_heart_rate": 61, "max_heart_rate": 60},
    ],
)
def test_heart_detail_rejects_contradictions_beyond_rounding(changes):
    with pytest.raises(ValidationError):
        detailed_night(**changes)


async def test_repository_maps_stored_heart_names_and_all_detail_fields():
    records = await read_row("sleeps")
    assert records[0].night.model_dump().items() >= DETAIL.items()


async def test_oxygen_does_not_validate_unrelated_sleep_heart_detail():
    records = await read_row("oxygen_saturations", hr_avg=Decimal("NaN"))
    assert records[0].value == Decimal("95.7")
    assert records[0].measured_min == Decimal("90.5")
    assert records[0].night is None


def test_existing_sleep_query_accepts_366_days_and_rejects_367():
    assert query(metric="sleeps", start=TODAY - timedelta(days=365)).end == TODAY
    with pytest.raises(ValidationError):
        query(metric="sleeps", start=TODAY - timedelta(days=366))


def test_new_evidence_retains_detail_and_old_evidence_is_not_backfilled():
    snapshot = (
        calculate([sleep_record(detailed_night())], metric="sleeps")
        .to_public()
        .model_dump(mode="json")
    )
    port = MetricResult(str(USER), snapshot)
    assert port.snapshot == snapshot
    item = synthetic_evidence().event_item()
    item.update(metric="sleeps", period=snapshot["period"], snapshot=port.snapshot)
    partition = partition_evidence([item])
    assert partition.skipped_metrics == ()
    assert evidence_data(partition.valid)[0].snapshot == snapshot
    for key in DETAIL:
        del snapshot["detail"]["nights"][0][key]
    legacy = replace(synthetic_evidence_info(), metric="sleeps", snapshot=snapshot)
    assert evidence_data([legacy])[0].snapshot == snapshot
    assert (
        "session_minutes"
        not in evidence_data([legacy])[0].snapshot["detail"]["nights"][0]
    )
