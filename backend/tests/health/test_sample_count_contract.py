import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.health.calculations import heart_record
from app.health.rest_schemas import DailyRangeResponse, HourlyHeartRateResponse
from app.health.schemas import DailyRange, HourlyHeartRate
from coach_ai.adapters.memory import HeartDay, InMemoryHealthData, SyncState
from tests.health.test_calculations import NOW, STATUS, TODAY, calculate
from tests.health.test_schemas import USER


@pytest.mark.parametrize(
    "model,values",
    [
        (DailyRange, dict(date=TODAY, min=None, max=None)),
        (DailyRangeResponse, dict(date=TODAY, min=None, max=None)),
        (HourlyHeartRate, dict(avg=70, min=60, max=80)),
        (HourlyHeartRateResponse, dict(avg=70, min=60, max=80)),
    ],
)
def test_new_sample_count_key_is_required_but_nullable(model, values):
    with pytest.raises(ValidationError) as error:
        model.model_validate(values)
    assert error.value.errors()[0]["type"] == "missing"
    assert model.model_validate(values | {"sample_count": None}).sample_count is None


@pytest.mark.parametrize("name", ["DailyRangeResponse", "HourlyHeartRateResponse"])
def test_openapi_requires_nullable_sample_count(app, name):
    schema = app.openapi()["components"]["schemas"][name]
    assert "sampleCount" in schema["required"]
    assert {"type": "null"} in schema["properties"]["sampleCount"]["anyOf"]


async def test_memory_counts_match_backend_for_absent_zero_and_partial_days():
    start = TODAY - timedelta(days=3)
    inputs = [
        (
            start,
            Decimal(70),
            120,
            {"8": dict(avg=70, min=60, max=80, count=120)},
            60,
            80,
        ),
        (start + timedelta(days=1), None, 0, None, None, None),
        (TODAY, None, 7, None, None, None),
    ]
    stored = [heart_record(*values) for values in inputs]
    backend = calculate(stored, metric="heart_rates", start=start).to_public()
    memory = InMemoryHealthData(
        heart={
            str(USER): [
                HeartDay(
                    day,
                    float(avg) if avg is not None else None,
                    low,
                    high,
                    sample_count=count,
                )
                for day, avg, count, _, low, high in inputs
            ]
        },
        linked_from={str(USER): start},
        sync={
            str(USER): {
                "heart_rate": SyncState(
                    STATUS.last_data_at, "success", STATUS.last_success_at
                )
            }
        },
        now=NOW,
    )
    result = await memory.get_metric_result(str(USER), "heart_rate", start, TODAY)
    assert (
        result.snapshot["detail"]["daily_ranges"]
        == backend.model_dump(mode="json")["detail"]["daily_ranges"]
    )
    assert [
        item["sample_count"] for item in result.snapshot["detail"]["daily_ranges"]
    ] == [120, 0, None, 7]


def test_shared_heart_fixture_matches_current_backend_output():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "ai/tests/data/common_query_results.json"
        ).read_text(encoding="utf-8")
    )["heart_weighted"]["snapshot"]
    inputs = [
        (19, "70", 14, 50, 110, 140),
        (20, "76", 15, 52, 140, 300),
        (21, "95", 1, 60, 130, 5000),
    ]
    records = [
        heart_record(
            TODAY.replace(day=day),
            Decimal(avg),
            count,
            {
                str(hour): dict(avg=float(avg), min=low, max=high, count=count // hours)
                for hour in range(hours)
            },
            low,
            high,
        )
        for day, avg, hours, low, high, count in inputs
    ]
    result = calculate(
        records,
        metric="heart_rates",
        start=TODAY.replace(day=19),
        end=TODAY.replace(day=21),
    )
    payload = result.to_public().model_dump(mode="json")
    assert fixture["detail"] == payload["detail"]
    assert fixture["summary"] == payload["summary"]
