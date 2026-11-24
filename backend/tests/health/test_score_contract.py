from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.core.schemas import DataResponse
from app.health.rest_schemas import LifestyleScoreRequest, lifestyle_score_response
from app.health.schemas import KST, LifestyleScoreRecord, MissingLifestyleScore

TODAY = date(2026, 10, 26)
OWNER = UUID("00000000-0000-4000-8000-000000000111")


def stored_score(**changes):
    return (
        dict(
            end_user_id=OWNER,
            date=TODAY,
            total_score=67,
            activity_score=80,
            activity_grade="excellent",
            steps_score=48,
            exercise_score=32,
            steps=5400,
            exercise_minutes=24,
            sleep_score=65,
            sleep_grade="stable",
            sleep_minutes=380,
            heart_score=55,
            heart_grade="attention",
            sleep_hr_avg=Decimal("66.0"),
            hr_baseline=Decimal("58.0"),
            hr_baseline_nights=20,
            check_wear=False,
            computed_at=datetime(2026, 10, 26, 12, 15, tzinfo=KST),
        )
        | changes
    )


def response(**changes):
    record = LifestyleScoreRecord(**stored_score(**changes))
    return lifestyle_score_response(record, today=TODAY).model_dump(mode="json")


def test_complete_public_response_matches_spec_and_keeps_record():
    record = LifestyleScoreRecord(**stored_score())
    before = record.model_dump()
    result = lifestyle_score_response(record, today=TODAY)
    assert DataResponse(data=result).model_dump(mode="json") == {
        "data": {
            "date": "2026-10-26",
            "reason": None,
            "isInProgress": True,
            "total": {"score": 67, "missingAreas": []},
            "areas": {
                "activity": {
                    "score": 80,
                    "grade": 1,
                    "steps": {"score": 48, "value": 5400},
                    "exercise": {"score": 32, "minutes": 24},
                },
                "sleep": {"score": 65, "grade": 2, "sleepMinutes": 380},
                "heart": {
                    "score": 55,
                    "grade": 3,
                    "sleepHrAvg": 66.0,
                    "baselineHrAvg": 58.0,
                    "baselineNights": 20,
                    "checkWear": False,
                },
            },
            "computedAt": "2026-10-26T03:15:00Z",
        }
    }
    assert record.model_dump() == before


@pytest.mark.parametrize("reason", ["BEFORE_LINK", "UNSYNCED", "NO_RECORD"])
def test_absent_row_exposes_reason_and_null_details_without_invented_values(reason):
    missing = MissingLifestyleScore(end_user_id=OWNER, date=TODAY, reason=reason)
    result = lifestyle_score_response(missing, today=TODAY).model_dump(mode="json")
    assert result == {
        "date": TODAY.isoformat(),
        "isInProgress": True,
        "reason": reason,
        "total": {"score": None, "missingAreas": ["activity", "sleep", "heart"]},
        "areas": {
            "activity": {
                "score": None,
                "grade": None,
                "steps": {"score": None, "value": None},
                "exercise": {"score": None, "minutes": None},
            },
            "sleep": {"score": None, "grade": None, "sleepMinutes": None},
            "heart": {
                "score": None,
                "grade": None,
                "sleepHrAvg": None,
                "baselineHrAvg": None,
                "baselineNights": None,
                "checkWear": None,
            },
        },
        "computedAt": None,
    }


@pytest.mark.parametrize("field", ["computed_at", "hr_baseline_nights", "check_wear"])
def test_saved_rows_still_require_their_stored_time_and_heart_metadata(field):
    with pytest.raises(ValidationError):
        LifestyleScoreRecord(**stored_score(**{field: None}))


def test_zero_score_is_not_missing_and_past_is_not_in_progress():
    result = response(
        date=date(2000, 1, 1),
        total_score=40,
        activity_score=0,
        activity_grade="care",
        steps_score=0,
        exercise_score=0,
        steps=800,
        exercise_minutes=0,
    )
    assert result["isInProgress"] is False
    assert result["areas"]["activity"]["grade"] == 4
    assert result["areas"]["activity"]["exercise"]["minutes"] == 0
    assert result["total"] == {"score": 40, "missingAreas": []}


def test_partial_heart_data_keeps_baseline_without_a_score():
    result = response(
        total_score=80,
        sleep_score=None,
        sleep_grade=None,
        sleep_minutes=None,
        heart_score=None,
        heart_grade=None,
        sleep_hr_avg=None,
    )
    assert result["total"] == {"score": 80, "missingAreas": ["sleep", "heart"]}
    assert result["areas"]["heart"]["baselineHrAvg"] == 58.0
    assert result["areas"]["heart"]["baselineNights"] == 20


@pytest.mark.parametrize("steps", [None, 0])
def test_existing_all_null_row_is_a_valid_response_and_preserves_steps(steps):
    result = response(
        total_score=None,
        activity_score=None,
        activity_grade=None,
        steps_score=None,
        exercise_score=None,
        steps=steps,
        exercise_minutes=None,
        sleep_score=None,
        sleep_grade=None,
        sleep_minutes=None,
        heart_score=None,
        heart_grade=None,
        sleep_hr_avg=None,
        hr_baseline=None,
        hr_baseline_nights=0,
    )
    assert result["total"] == {
        "score": None,
        "missingAreas": ["activity", "sleep", "heart"],
    }
    assert result["areas"]["activity"]["steps"]["value"] == steps
    assert result["areas"]["activity"]["exercise"]["minutes"] is None
    assert result["reason"] is None
    assert result["computedAt"] is not None
    assert result["areas"]["heart"]["baselineNights"] == 0
    assert result["areas"]["heart"]["checkWear"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"total_score": 101},
        {"steps_score": 61},
        {"exercise_score": -1},
        {"heart_score": True},
        {"sleep_score": 65.5},
        {"activity_grade": "unknown"},
        {"activity_grade": None},
        {"heart_score": None},
        {"total_score": None},
        {"exercise_minutes": None},
        {"hr_baseline_nights": -1},
        {"sleep_hr_avg": Decimal("NaN")},
        {"computed_at": datetime(2026, 10, 26, tzinfo=UTC).replace(tzinfo=None)},
    ],
)
def test_invalid_stored_contract_is_rejected_without_defaults(changes):
    with pytest.raises(ValidationError):
        LifestyleScoreRecord(**stored_score(**changes))


def test_conversion_does_not_recompute_scores_or_grades():
    # Stored outputs are authoritative; the API does not reimplement score rules.
    result = response(total_score=66, activity_grade="stable")
    assert result["total"]["score"] == 66
    assert result["areas"]["activity"]["grade"] == 2


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 10, 25, 14, 59, 59, tzinfo=UTC),
        datetime(2026, 10, 25, 15, tzinfo=UTC),
    ],
)
def test_omitted_date_and_progress_share_one_kst_today(instant):
    today = instant.astimezone(KST).date()
    selected = LifestyleScoreRequest().resolve_date(today=today)
    assert selected == today
    record = LifestyleScoreRecord(**stored_score(date=selected))
    assert lifestyle_score_response(record, today=today).is_in_progress


@pytest.mark.parametrize("value", ["2000-01-01", "2024-02-29", "2026-10-26"])
def test_score_date_accepts_old_dates_and_leap_day(value):
    assert LifestyleScoreRequest(date=value).resolve_date(
        today=TODAY
    ) == date.fromisoformat(value)


@pytest.mark.parametrize(
    "value",
    [None, "", "null", "20261026", "2026-02-29", "2026-10-26T00:00:00Z", True, 1],
)
def test_explicit_invalid_date_is_not_treated_as_omission(value):
    with pytest.raises(ValidationError):
        LifestyleScoreRequest(date=value)


def test_future_score_date_has_public_field_error():
    with pytest.raises(ApiError) as caught:
        LifestyleScoreRequest(date="2026-10-27").resolve_date(today=TODAY)
    assert caught.value.status_code == 422
    assert [detail.model_dump() for detail in caught.value.details] == [
        {"field": "date", "reason": "out_of_range"}
    ]


def test_score_request_does_not_accept_metric_period_parameters():
    with pytest.raises(ValidationError):
        LifestyleScoreRequest(start="2026-10-01")
