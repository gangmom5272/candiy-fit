from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health.calculations import (
    build_timeline,
    timeline_exercises,
    timeline_hearts,
    timeline_sleeps,
)
from app.health.errors import InvalidHealthData
from app.health.schemas import HourlyHeartRecord, MainSleepRecord
from tests.health.test_exercise import exercise
from tests.health.test_timeline_contract import TODAY


def test_all_requested_dates_preserve_null_zero_and_current_day():
    owner = uuid4()
    start = TODAY - timedelta(days=30)
    result = build_timeline(
        owner,
        start,
        TODAY,
        today=TODAY,
        steps={start: [0] * 24, TODAY: None},
        hearts={},
        sleeps={},
        exercises={},
    )
    assert result.user_id == owner and len(result.days) == 31
    assert [item.date for item in result.days] == [
        start + timedelta(days=i) for i in range(31)
    ]
    assert [item.is_in_progress for item in result.days] == [False] * 30 + [True]
    assert result.days[0].hourly_steps == [0] * 24
    for day in result.days[1:]:
        assert day.hourly_steps is None and day.hourly_heart_rates is None
        assert (
            day.main_sleep is None
            and day.sleep_segments == []
            and day.exercise_segments == []
        )


def test_sparse_hour_keys_preserve_stored_range_and_sample_count():
    record = HourlyHeartRecord(
        date=TODAY,
        hourly_hr={
            "23": {"avg": 76.2, "min": 60, "max": 90, "count": 99},
            "0": {"avg": 60, "min": 55, "max": 65, "count": 1},
        },
    )
    before = record.model_dump()
    result = timeline_hearts([record])[TODAY]
    assert len(result) == 24 and result[1:23] == [None] * 22
    assert result[23].model_dump() == {
        "avg": 76.2,
        "min": 60,
        "max": 90,
        "sample_count": 99,
    }
    assert result[0].avg == 60
    assert record.model_dump() == before
    assert timeline_hearts([HourlyHeartRecord(date=TODAY, hourly_hr=None)]) == {
        TODAY: None
    }


@pytest.mark.parametrize("hour", ["-1", "24", "01", "1.0", "09:00", "bad"])
def test_invalid_hour_keys_fail_without_dropping_data(hour):
    with pytest.raises(InvalidHealthData):
        timeline_hearts(
            [
                HourlyHeartRecord(
                    date=TODAY, hourly_hr={hour: {"avg": 70, "min": 60, "max": 80}}
                )
            ]
        )


@pytest.mark.parametrize(
    "value",
    [
        {"avg": 70},
        {"avg": 90, "min": 60, "max": 80},
        {"avg": "70", "min": 60, "max": 80},
        {"avg": float("inf"), "min": 60, "max": 80},
        {"avg": 70, "min": 60, "max": 80, "count": 0},
        {"avg": 70, "min": 60, "max": 80, "count": -1},
        {"avg": 70, "min": 60, "max": 80, "count": True},
        {"avg": 70, "min": 60, "max": 80, "count": "1"},
        {"avg": 70, "min": 60, "max": 80, "count": 1.5},
    ],
)
def test_invalid_heart_ranges_fail(value):
    with pytest.raises((InvalidHealthData, ValidationError)):
        timeline_hearts([HourlyHeartRecord(date=TODAY, hourly_hr={"0": value})])


@pytest.mark.parametrize("average", [59.5, 59.6, 60.4, 60.5])
def test_hourly_heart_rounding_tolerance_preserves_stored_values(average):
    hours = timeline_hearts(
        [
            HourlyHeartRecord(
                date=TODAY, hourly_hr={"0": dict(avg=average, min=60, max=60, count=1)}
            )
        ]
    )[TODAY]
    assert hours[0].model_dump() == dict(avg=average, min=60, max=60, sample_count=1)


@pytest.mark.parametrize(
    "average,low,high", [(59.49, 60, 60), (60.51, 60, 60), (60.5, 61, 60)]
)
def test_hourly_heart_rejects_outside_tolerance_and_reversed_extremes(
    average, low, high
):
    with pytest.raises(ValidationError):
        timeline_hearts(
            [
                HourlyHeartRecord(
                    date=TODAY,
                    hourly_hr={"0": dict(avg=average, min=low, max=high, count=1)},
                )
            ]
        )


def main_sleep(**changes):
    return MainSleepRecord(
        **(
            dict(
                date=TODAY,
                start_at="2026-10-25T23:00:00+09:00",
                end_at="2026-10-26T06:00:00+09:00",
                sleep_timeline=None,
            )
            | changes
        )
    )


def test_sleep_wake_date_keeps_cross_midnight_span_and_sorts_all_stages():
    record = main_sleep(
        sleep_timeline=[
            {
                "start": "2026-10-26T00:10:00+09:00",
                "end": "2026-10-26T06:00:00+09:00",
                "stage": "unknown",
            },
            {
                "start": "2026-10-25T23:00:00+09:00",
                "end": "2026-10-26T00:30:00+09:00",
                "stage": "unspecified",
            },
        ]
    )
    before = record.model_dump()
    span, segments = timeline_sleeps([record])[TODAY]
    assert span.model_dump(mode="json") == {
        "start_at": "2026-10-25T14:00:00Z",
        "end_at": "2026-10-25T21:00:00Z",
    }
    assert [s.stage for s in segments] == ["unspecified", "unknown"]
    # 저장된 겹침·틈을 백엔드가 재병합하거나 보정하지 않는다.
    assert segments[0].end_at > segments[1].start_at
    assert record.model_dump() == before
    assert timeline_sleeps([main_sleep()])[TODAY][1] == []


@pytest.mark.parametrize(
    "stage",
    [
        {"start": "2026-10-25T23:00:00+09:00"},
        {
            "start": "2026-10-25T23:00:00+09:00",
            "end": "2026-10-26T06:00:00+09:00",
            "stage": "invalid",
        },
    ],
)
def test_malformed_saved_stage_is_not_hidden_as_no_stages(stage):
    with pytest.raises((InvalidHealthData, ValidationError)):
        timeline_sleeps([main_sleep(sleep_timeline=[stage])])


def test_cross_midnight_exercises_stay_on_start_date_and_exclude_uncounted_sessions():
    evening = exercise()
    earlier = exercise(
        start_at=evening.start_at - timedelta(hours=4),
        end_at=evening.start_at - timedelta(hours=3),
    )
    excluded = exercise(
        start_at=evening.start_at - timedelta(hours=2),
        end_at=evening.start_at - timedelta(hours=1),
        counts_as_exercise=False,
    )
    result = timeline_exercises([evening, excluded, earlier])
    assert list(result) == [evening.date]
    segments = result[evening.date]
    assert [segment.start_at for segment in segments] == [
        earlier.start_at,
        evening.start_at,
    ]
    assert segments[1].end_at == evening.end_at
    assert set(segments[0].model_dump()) == {
        "start_at",
        "end_at",
        "exercise_type",
        "type_name",
        "title",
    }


@pytest.mark.parametrize("exercise_type", [79, 56])
@pytest.mark.parametrize("speed", [None, 0.1, 3])
@pytest.mark.parametrize("counted", [True, False])
def test_timeline_uses_stored_decision_regardless_of_type_or_speed(
    exercise_type, speed, counted
):
    session = exercise(
        exercise_type=exercise_type,
        avg_speed_mps=speed,
        counts_as_exercise=counted,
    )
    before = session.model_dump()
    result = build_timeline(
        uuid4(),
        session.date,
        session.date,
        today=TODAY,
        steps={},
        hearts={},
        sleeps={},
        exercises=timeline_exercises([session]),
    )
    assert len(result.days) == 1 and result.days[0].date == session.date
    assert len(result.days[0].exercise_segments) == int(counted)
    assert session.model_dump() == before
