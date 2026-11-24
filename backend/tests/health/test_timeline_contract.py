import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.core.schemas import DataResponse
from app.health.rest_schemas import (
    HealthTimelineResponse,
    TimelinePeriodRequest,
    timeline_response,
)
from app.health.schemas import HealthTimelineResult, Period, TimelineDay, TimelineSpan

TODAY = date(2026, 10, 26)


@pytest.mark.parametrize("length", [1, 31])
def test_inclusive_period_limits(length):
    query = TimelinePeriodRequest(
        start=(TODAY - timedelta(days=length - 1)).isoformat(), end=TODAY.isoformat()
    )
    query.validate_period(today=TODAY)


@pytest.mark.parametrize(
    ("start", "end", "field"),
    [
        ("2026-09-25", "2026-10-26", "end"),
        ("2026-10-26", "2026-10-25", "start"),
        ("2026-10-26", "2026-10-27", "end"),
    ],
)
def test_invalid_period(start, end, field):
    with pytest.raises(ApiError) as caught:
        TimelinePeriodRequest(start=start, end=end).validate_period(today=TODAY)
    assert caught.value.details[0].field == field


@pytest.mark.parametrize(
    "value", [None, "", "2026-1-01", "2026-02-30", "2026-10-26T00:00:00Z", 20261026]
)
@pytest.mark.parametrize("field", ["start", "end"])
def test_dates_require_canonical_calendar_strings(field, value):
    with pytest.raises(ValidationError):
        TimelinePeriodRequest.model_validate(
            {"start": "2026-10-26", "end": "2026-10-26", field: value}
        )


@pytest.mark.parametrize("field", ["start", "end"])
def test_both_dates_required(field):
    with pytest.raises(ValidationError):
        TimelinePeriodRequest.model_validate({field: "2026-10-26"})


def empty_day(**changes):
    return (
        dict(
            date=TODAY,
            is_in_progress=True,
            hourly_steps=None,
            hourly_heart_rates=None,
            main_sleep=None,
            sleep_segments=[],
            exercise_segments=[],
        )
        | changes
    )


def test_empty_day_preserves_null_and_empty_lists_without_owner():
    result = HealthTimelineResult(
        user_id=uuid4(),
        period=Period(start=TODAY, end=TODAY),
        days=[TimelineDay(**empty_day())],
    )
    data = DataResponse(data=timeline_response(result)).model_dump(mode="json")["data"]
    assert data == {
        "period": {"start": "2026-10-26", "end": "2026-10-26"},
        "days": [
            {
                "date": "2026-10-26",
                "isInProgress": True,
                "hourlySteps": None,
                "hourlyHeartRates": None,
                "mainSleep": None,
                "sleepSegments": [],
                "exerciseSegments": [],
            }
        ],
    }


@pytest.mark.parametrize(
    "value",
    [
        [0] * 23,
        [0] * 25,
        [0] * 23 + [None],
        [0] * 23 + [True],
        [0] * 23 + [-1],
        [0] * 23 + [1.5],
    ],
)
def test_invalid_hourly_steps_are_not_repaired(value):
    with pytest.raises(ValidationError):
        TimelineDay(**empty_day(hourly_steps=value))


@pytest.mark.parametrize(
    "value",
    [
        [None] * 23,
        [None] * 25,
        [None] * 23 + [{"avg": True, "min": 1, "max": 2}],
        [None] * 23 + [{"avg": float("nan"), "min": 1, "max": 2}],
        [None] * 23 + [{"avg": 3, "min": 1, "max": 2}],
    ],
)
def test_invalid_hourly_heart_values(value):
    with pytest.raises(ValidationError):
        TimelineDay(**empty_day(hourly_heart_rates=value))


def test_zero_steps_and_unmeasured_hours_remain_distinct():
    day = TimelineDay(
        **empty_day(hourly_steps=[0] * 24, hourly_heart_rates=[None] * 24)
    )
    assert day.hourly_steps == [0] * 24
    assert day.hourly_heart_rates == [None] * 24


def spec_example():
    spec = (Path(__file__).parents[2] / "docs/api-spec.md").read_text(encoding="utf-8")
    section = spec.split("### 5.4 ", 1)[1]
    return json.loads(section.split("```json", 1)[1].split("```", 1)[0])


def test_spec_v11_complete_example_round_trips():
    example = spec_example()
    response = DataResponse(data=HealthTimelineResponse.model_validate(example["data"]))
    assert response.model_dump(mode="json") == example


def test_spans_normalize_utc_and_reject_naive_or_reversed_times():
    span = TimelineSpan(
        start_at="2026-10-25T23:00:00+09:00", end_at="2026-10-26T06:00:00+09:00"
    )
    assert span.model_dump(mode="json")["start_at"] == "2026-10-25T14:00:00Z"
    for start, end in [
        ("2026-10-25T23:00:00", "2026-10-26T06:00:00"),
        (span.end_at, span.start_at),
        (span.start_at, span.start_at),
    ]:
        with pytest.raises(ValidationError):
            TimelineSpan(start_at=start, end_at=end)
