from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.health.schemas import (
    ExerciseComparisonDay,
    ExerciseComparisonGroup,
    ExerciseRecordComparison,
    ExerciseRecordComparisonQuery,
    PublicExerciseRecordComparison,
)
from tests.health.test_calculations import STATUS, TODAY
from tests.health.test_schemas import USER


def query(**changes):
    return ExerciseRecordComparisonQuery.model_validate(
        dict(user_id=USER, metric="steps", start=TODAY, end=TODAY) | changes,
        context={"today": TODAY},
    )


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
def test_validates_inclusive_period_and_code_shape_without_database_catalog(metric):
    value = query(metric=metric, start=TODAY - timedelta(days=365), exercise_type=0)
    assert value.metric == metric and value.exercise_type == 0
    # 기준표 등록 여부는 같은 DB 스냅샷을 읽는 서비스에서 검증한다.
    assert query(exercise_type=99999).exercise_type == 99999


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "heart_rates"},
        {"metric": "exercises"},
        {"start": TODAY + timedelta(days=1)},
        {"end": TODAY + timedelta(days=1)},
        {"start": TODAY - timedelta(days=366)},
        {"user_id": "not-a-uuid"},
        {"exercise_type": True},
        {"exercise_type": "70"},
        {"exercise_type": 70.5},
        {"wear_hours": 10},
    ],
)
def test_rejects_invalid_boundary_inputs(changes):
    with pytest.raises(ValidationError):
        query(**changes)


@pytest.fixture
def comparison_result():
    day = date(2026, 10, 20)
    group = ExerciseComparisonGroup(
        days=[
            ExerciseComparisonDay(
                exercise_date=day, metric_date=day + timedelta(days=1), value=0
            )
        ],
        days_with_data=1,
        avg=Decimal("0.00"),
    )
    return ExerciseRecordComparison(
        user_id=USER,
        metric="sleeps",
        period={"start": day, "end": day},
        metric_period={
            "start": day + timedelta(days=1),
            "end": day + timedelta(days=1),
        },
        exercise_type=None,
        unit="min",
        with_exercise=group,
        without_exercise=ExerciseComparisonGroup(days=[], days_with_data=0, avg=None),
        difference=None,
        comparable=False,
        reliable=False,
        reliable_min_days=3,
        excluded_days=[],
        linked_from=day,
        exercise_data_status=STATUS,
        metric_data_status=STATUS,
    )


def test_public_contract_preserves_dates_null_zero_and_numeric_precision(
    comparison_result,
):
    result = comparison_result
    public = result.to_public().model_dump(mode="json")
    assert "user_id" not in public
    assert result.user_id == USER
    assert public["with_exercise"]["avg"] == 0
    assert public["with_exercise"]["days"][0]["metric_date"] == "2026-10-21"
    assert public["difference"] is None and public["exercise_type"] is None
    assert public["reliable"] is False
    assert public["reliable_min_days"] == 3
    assert (
        PublicExerciseRecordComparison.model_validate_json(
            result.to_public().model_dump_json()
        ).reliable_min_days
        == 3
    )


@pytest.mark.parametrize(
    "model", [ExerciseRecordComparison, PublicExerciseRecordComparison]
)
@pytest.mark.parametrize("value", [None, True, False, "3", 3.0, 0, -1])
def test_reliable_min_days_rejects_non_positive_or_non_integer_values(
    comparison_result, model, value
):
    result = (
        comparison_result
        if model is ExerciseRecordComparison
        else comparison_result.to_public()
    )
    with pytest.raises(ValidationError) as error:
        model.model_validate(result.model_dump() | {"reliable_min_days": value})
    assert error.value.errors()[0]["loc"] == ("reliable_min_days",)


@pytest.mark.parametrize(
    "model", [ExerciseRecordComparison, PublicExerciseRecordComparison]
)
def test_reliable_min_days_is_required(comparison_result, model):
    result = (
        comparison_result
        if model is ExerciseRecordComparison
        else comparison_result.to_public()
    )
    payload = result.model_dump(exclude={"reliable_min_days"})
    with pytest.raises(ValidationError) as error:
        model.model_validate(payload)
    assert error.value.errors()[0]["type"] == "missing"
    assert error.value.errors()[0]["loc"] == ("reliable_min_days",)
