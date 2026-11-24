from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.schemas import DataResponse
from app.health.calculations import (
    DailyRecord,
    exercise_records,
    oxygen_record,
    sleep_record,
)
from app.health.rest_schemas import MetricResponse, metric_response
from tests.health.test_calculations import calculate, steps
from tests.health.test_exercise import exercise
from tests.health.test_sleep import night, night_values


def result_for(metric):
    item = night_values(21)
    records = {
        "steps": steps(),
        "heart_rates": [
            DailyRecord(
                date(2026, 10, 21), Decimal("73.83"), measured_min=50, measured_max=140
            )
        ],
        "oxygen_saturations": [
            oxygen_record(
                item["date"],
                item["start_at"],
                item["end_at"],
                spo2_avg=Decimal("95.73"),
                spo2_min=Decimal(90),
                spo2_max=Decimal(99),
            )
        ],
        "sleeps": [sleep_record(night(21))],
        "exercises": exercise_records(
            [
                exercise(
                    counts_as_exercise=False,
                    avg_speed_mps=Decimal("0.85"),
                    title="synthetic_walk",
                ),
            ]
        ),
    }
    return calculate(records[metric], metric=metric)


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
)
def test_rest_conversion_keeps_values_and_internal_snapshot(metric):
    result = result_for(metric)
    before = result.model_dump_json()
    response = metric_response(result.to_public())
    payload = DataResponse(data=response).model_dump(mode="json")["data"]
    assert set(payload) == {
        "metric",
        "period",
        "unit",
        "summary",
        "days",
        "inProgress",
        "dataStatus",
        "detail",
    }
    assert payload["metric"] == metric
    assert (
        "dailyMin" in payload["summary"] and "daysUsedForSummary" in payload["summary"]
    )
    assert "isInProgress" in payload["days"][0]
    assert payload["dataStatus"]["checkedAt"] == "2026-10-22T05:00:00Z"
    assert set(payload["detail"]) == {
        "measuredMin",
        "measuredMax",
        "nights",
        "dailyRanges",
        "sessions",
    }
    assert "user_id" not in response.model_dump_json() and "userId" not in payload
    assert response.model_dump(
        mode="json", by_alias=False
    ) == result.to_public().model_dump(mode="json")
    assert result.model_dump_json() == before
    assert MetricResponse.model_validate(payload).model_dump(mode="json") == payload


def test_rest_exercise_preserves_false_speed_null_and_zero():
    payload = metric_response(result_for("exercises").to_public()).model_dump(
        mode="json"
    )
    session = payload["detail"]["sessions"][0]
    assert session["countsAsExercise"] is False
    assert session["avgSpeedMps"] == 0.85
    assert session["caloriesKcal"] == 123.4
    assert session["title"] == "synthetic_walk"
    assert session["durationMinutes"] == 60
    assert payload["summary"]["total"] == 0
    assert payload["days"][-2]["value"] == 0
    assert payload["days"][-2]["reason"] is None


@pytest.mark.parametrize("distance", [None, Decimal("0.0"), Decimal("1234.5")])
def test_rest_exercise_distance_uses_camel_case_without_changing_snapshot(distance):
    result = calculate(
        exercise_records([exercise(distance_m=distance)]), metric="exercises"
    )
    public = result.to_public().model_dump(mode="json")
    session = metric_response(result.to_public()).model_dump(mode="json")["detail"][
        "sessions"
    ][0]
    assert session["distanceM"] == (None if distance is None else float(distance))
    assert "distance_m" not in session
    assert public["detail"]["sessions"][0]["distance_m"] == session["distanceM"]
    assert "distanceM" not in public["detail"]["sessions"][0]


def test_rest_sleep_keeps_nullable_fields_and_empty_lists():
    payload = metric_response(result_for("sleeps").to_public()).model_dump(mode="json")
    assert payload["inProgress"] is payload["summary"]["total"] is None
    assert payload["detail"]["sessions"] == []
    assert payload["detail"]["nights"][0]["lightMinutes"] is None
    assert payload["detail"]["nights"][0]["sleepMinutes"] == 400


@pytest.mark.parametrize(
    "changes",
    [
        {"counts_as_exercise": "false"},
        {"duration_minutes": 0},
        {"avg_speed_mps": Decimal("NaN")},
        {"end_at": "2026-10-20T00:00:00Z"},
    ],
)
def test_rest_revalidates_nested_stored_contract(changes):
    payload = result_for("exercises").to_public().model_dump()
    payload["detail"]["sessions"][0].update(changes)
    with pytest.raises(ValidationError):
        MetricResponse.model_validate(payload)


def test_rest_rejects_internal_identity_in_public_model():
    with pytest.raises(ValidationError):
        MetricResponse.model_validate(result_for("steps").model_dump())
