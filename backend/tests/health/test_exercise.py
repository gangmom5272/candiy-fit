import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.health.calculations import DailyRecord, exercise_records
from app.health.schemas import (
    ActivityDailyRecord,
    ActivitySourceStatus,
    ExerciseSessionDetail,
)
from coach_ai.ports import MetricResult
from tests.health.test_calculations import NOW, STATUS, TODAY, calculate

DAY = date(2026, 10, 21)


def activity_record(day=DAY, *, minutes=0, count=0, calories=None, distance=None):
    return DailyRecord(
        day,
        minutes,
        activity=ActivityDailyRecord(
            date=day,
            exercise_minutes=minutes,
            exercise_count=count,
            exercise_calories_kcal=calories,
            exercise_distance_m=distance,
            computed_at=NOW,
        ).model_dump(),
    )


def activity_statuses():
    return {
        name: ActivitySourceStatus(
            ready=True if name == "exercise_sessions" else None,
            last_sync_status="success" if name == "exercise_sessions" else None,
            last_data_at=NOW if name == "exercise_sessions" else None,
            last_success_at=NOW if name == "exercise_sessions" else None,
        )
        for name in ("exercise_sessions", "calories_burned", "distances")
    }


def activity_result():
    return calculate(
        [
            activity_record(
                DAY - timedelta(days=2),
                minutes=30,
                count=1,
                calories=240,
                distance=4200,
            ),
            activity_record(DAY - timedelta(days=1), calories=0),
            activity_record(TODAY, minutes=20, count=1, calories=100, distance=2000),
        ],
        metric="exercises",
        start=DAY - timedelta(days=2),
        activity_source_statuses=activity_statuses(),
    )


def test_activity_extension_counts_zero_excludes_today_and_preserves_missing_values():
    result = activity_result()
    detail = result.detail.exercise_activity
    assert [day.exercise_count for day in detail.days] == [1, 0, None, 1]
    assert [day.reason for day in detail.days] == [None, None, "NO_RECORD", None]
    assert detail.days[1].distance_reason == "NULL_VALUE"
    assert detail.days[1].calories_reason is None
    assert detail.days[2].computed_at is None
    assert detail.days[3].is_in_progress
    assert detail.summary.exercise_count.model_dump() == dict(
        total=1, avg=Decimal("0.50"), days_with_data=2
    )
    assert detail.summary.exercise_calories_kcal.model_dump() == dict(
        total=240, avg=120, days_with_data=2
    )
    assert detail.summary.exercise_distance_m.model_dump() == dict(
        total=4200, avg=4200, days_with_data=1
    )
    assert detail.summary.exercise_minutes.total == result.summary.total == 30
    assert detail.summary.exercise_minutes.avg == result.summary.avg == 15
    assert detail.source_statuses["distances"].ready is None


@pytest.mark.parametrize("linked", [None, TODAY])
def test_activity_extension_does_not_expose_values_before_link(linked):
    result = calculate(
        [activity_record(calories=123)],
        metric="exercises",
        start=DAY,
        linked=linked,
        activity_source_statuses=activity_statuses(),
    )
    day = result.detail.exercise_activity.days[0]
    assert day.reason == day.calories_reason == day.distance_reason == "BEFORE_LINK"
    assert day.exercise_count is day.exercise_calories_kcal is None
    assert (
        result.detail.exercise_activity.summary.exercise_calories_kcal.days_with_data
        == 0
    )


@pytest.mark.parametrize("synced", [None, NOW - timedelta(days=1), NOW])
def test_activity_extension_uses_common_sync_boundary_for_missing_measurements(synced):
    result = calculate(
        [activity_record()],
        metric="exercises",
        start=DAY,
        end=DAY,
        status=STATUS.model_copy(update={"synced_until": synced}),
        activity_source_statuses=activity_statuses(),
    )
    day = result.detail.exercise_activity.days[0]
    expected = "NULL_VALUE" if synced == NOW else "UNSYNCED"
    assert day.reason is None and day.exercise_count == 0
    assert day.calories_reason == day.distance_reason == expected


def test_activity_today_only_has_values_but_no_completed_statistics():
    result = calculate(
        [activity_record(TODAY, count=1, minutes=20, calories=0)],
        metric="exercises",
        start=TODAY,
        activity_source_statuses=activity_statuses(),
    )
    detail = result.detail.exercise_activity
    assert detail.days[0].exercise_count == 1 and detail.days[0].is_in_progress
    for name in type(detail.summary).model_fields:
        value = getattr(detail.summary, name)
        assert value.total is value.avg is None
        assert value.days_with_data == 0
    assert result.in_progress.as_of == STATUS.last_data_at
    assert result.data_status.synced_until == STATUS.synced_until


def test_activity_rest_and_snapshot_preserve_nulls_and_old_detail_shape():
    from app.health.rest_schemas import metric_response
    from app.health.schemas import PublicHealthMetricResult

    public = activity_result().to_public()
    snapshot = public.model_dump(mode="json")
    assert (
        snapshot["detail"]["exercise_activity"]["days"][1]["exercise_distance_m"]
        is None
    )
    rest = metric_response(public).model_dump(mode="json")
    detail = rest["detail"]["exerciseActivity"]
    assert detail["days"][1]["exerciseDistanceM"] is None
    assert detail["summary"]["exerciseCaloriesKcal"]["daysWithData"] == 2
    assert detail["sourceStatuses"]["exercise_sessions"]["lastSyncStatus"] == "success"
    del snapshot["detail"]["exercise_activity"]
    assert (
        PublicHealthMetricResult.model_validate(snapshot).model_dump(mode="json")
        == snapshot
    )
    for metric in ("steps", "heart_rates", "sleeps", "oxygen_saturations", "exercises"):
        old = calculate([], metric=metric).to_public()
        assert "exercise_activity" not in old.model_dump()["detail"]
        assert "exerciseActivity" not in metric_response(old).model_dump()["detail"]


@pytest.mark.parametrize(
    "corruption", ["metric", "dates", "sources", "negative", "boolean", "summary"]
)
def test_activity_extension_rejects_invalid_public_contract(corruption):
    from app.health.schemas import PublicHealthMetricResult

    payload = activity_result().to_public().model_dump(mode="json")
    detail = payload["detail"]["exercise_activity"]
    if corruption == "metric":
        payload["metric"] = "steps"
    elif corruption == "dates":
        detail["days"].pop()
    elif corruption == "sources":
        del detail["source_statuses"]["distances"]
    elif corruption == "negative":
        detail["days"][0]["exercise_distance_m"] = -1
    elif corruption == "boolean":
        detail["days"][0]["exercise_calories_kcal"] = True
    else:
        detail["summary"]["exercise_count"]["days_with_data"] = 0
    with pytest.raises(ValidationError):
        PublicHealthMetricResult.model_validate(payload)


def exercise(**changes):
    return ExerciseSessionDetail.model_validate(
        dict(
            record_id="synthetic-exercise",
            date=DAY,
            start_at=datetime(2026, 10, 21, 14, 30, tzinfo=UTC),
            end_at=datetime(2026, 10, 21, 15, 30, tzinfo=UTC),
            duration_minutes=60,
            exercise_type=64,
            type_name="축구",
            title=None,
            category="cardio",
            calories_kcal=Decimal("123.4"),
            distance_m=None,
            avg_heart_rate=Decimal("120.5"),
            max_heart_rate=150,
            counts_as_exercise=True,
            avg_speed_mps=None,
        )
        | changes
    )


def test_exercise_detail_serializes_only_contract_fields_and_preserves_nulls():
    detail = exercise(avg_heart_rate=None, max_heart_rate=None)
    payload = json.loads(detail.model_dump_json())
    assert set(payload) == {
        "record_id",
        "date",
        "start_at",
        "end_at",
        "duration_minutes",
        "exercise_type",
        "type_name",
        "title",
        "category",
        "calories_kcal",
        "distance_m",
        "avg_heart_rate",
        "max_heart_rate",
        "counts_as_exercise",
        "avg_speed_mps",
    }
    assert payload["start_at"] == "2026-10-21T14:30:00Z"
    assert payload["calories_kcal"] == 123.4
    assert payload["distance_m"] is None
    assert payload["counts_as_exercise"] is True
    assert payload["avg_speed_mps"] is None
    assert (
        payload["title"]
        is payload["avg_heart_rate"]
        is payload["max_heart_rate"]
        is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"date": DAY + timedelta(days=1)},
        {"end_at": datetime(2026, 10, 21, 14, 30, tzinfo=UTC)},
        {"start_at": "2026-10-21T14:30:00"},
        {"duration_minutes": 0},
        {"duration_minutes": 1.5},
        {"calories_kcal": Decimal("NaN")},
        {"avg_heart_rate": Decimal("Infinity")},
        {"category": "unknown"},
        {"data_id": "private-source-id"},
        {"counts_as_exercise": "false"},
        {"counts_as_exercise": None},
        {"avg_speed_mps": Decimal("NaN")},
        {"distance_m": Decimal("NaN")},
        {"distance_m": Decimal("Infinity")},
    ],
)
def test_exercise_detail_rejects_invalid_stored_data(changes):
    with pytest.raises(ValidationError):
        exercise(**changes)


def test_exercise_detail_preserves_stored_decision_and_numeric_speed():
    detail = exercise(counts_as_exercise=False, avg_speed_mps=Decimal("1.11"))
    payload = json.loads(detail.model_dump_json())
    assert payload["counts_as_exercise"] is False
    assert payload["avg_speed_mps"] == 1.11


@pytest.mark.parametrize("field", ["counts_as_exercise", "avg_speed_mps", "distance_m"])
def test_exercise_detail_requires_contract_fields(field):
    payload = exercise().model_dump()
    del payload[field]
    with pytest.raises(ValidationError):
        ExerciseSessionDetail.model_validate(payload)


@pytest.mark.parametrize("distance", [None, Decimal("0.0"), Decimal("1234.5")])
def test_exercise_distance_preserves_stored_value_as_json_number(distance):
    payload = exercise(distance_m=distance).model_dump(mode="json")
    assert payload["distance_m"] == (None if distance is None else float(distance))


def test_exercise_records_sum_stored_minutes_by_start_date_and_sort_sessions():
    across_midnight = exercise(duration_minutes=59)
    earlier = exercise(
        start_at=datetime(2026, 10, 21, 1, tzinfo=UTC),
        end_at=datetime(2026, 10, 21, 1, 20, tzinfo=UTC),
        duration_minutes=20,
        exercise_type=79,
        type_name="걷기",
    )
    next_day = exercise(
        date=DAY + timedelta(days=1),
        start_at=datetime(2026, 10, 21, 16, tzinfo=UTC),
        end_at=datetime(2026, 10, 21, 16, 10, tzinfo=UTC),
        duration_minutes=10,
    )
    records = exercise_records([next_day, across_midnight, earlier])
    assert [(r.date, r.value, r.weight) for r in records] == [
        (DAY, 79, 1),
        (DAY + timedelta(days=1), 10, 1),
    ]
    assert records[0].sessions == (earlier, across_midnight)
    assert records[1].sessions == (next_day,)
    assert records[0].measured_min is records[0].measured_max is None


def test_exercise_records_do_not_invent_zero_days_without_sessions():
    # 0분/결측은 일별 행 및 담당자 확인이 필요한 별도 단계다.
    assert exercise_records([]) == []


@pytest.mark.parametrize("counted", [True, False])
def test_exercise_time_uses_stored_decision_but_preserves_all_sessions(counted):
    included = exercise(duration_minutes=20, counts_as_exercise=counted)
    excluded = exercise(
        start_at=included.start_at - timedelta(hours=1),
        end_at=included.start_at,
        duration_minutes=30,
        exercise_type=79,
        counts_as_exercise=False,
        avg_speed_mps=None,
    )
    records = exercise_records([included, excluded])
    result = calculate(records, metric="exercises", start=DAY, end=DAY)
    assert result.days[0].value == (20 if counted else 0)
    assert result.days[0].missing is False and result.days[0].reason is None
    assert result.summary.total == result.summary.avg == result.days[0].value
    assert result.detail.sessions == [excluded, included]


def test_uncounted_today_has_zero_progress_without_losing_session():
    session = exercise(
        date=TODAY,
        start_at=datetime(2026, 10, 22, 1, tzinfo=UTC),
        end_at=datetime(2026, 10, 22, 2, tzinfo=UTC),
        counts_as_exercise=False,
    )
    result = calculate(exercise_records([session]), metric="exercises", start=TODAY)
    assert result.in_progress.value == 0 and not result.in_progress.missing
    assert result.summary.total is None
    assert result.summary.days_with_data == 0
    assert result.detail.sessions == [session]


def test_ai_port_preserves_exercise_decision_and_speed_in_snapshot():
    session = exercise(counts_as_exercise=False, avg_speed_mps=Decimal("0.50"))
    result = calculate(exercise_records([session]), metric="exercises", start=DAY)
    public = result.to_public().model_dump(mode="json")
    adapted = MetricResult(str(result.user_id), public)
    assert adapted.snapshot == public
    assert adapted.detail.sessions[0]["counts_as_exercise"] is False
    assert adapted.detail.sessions[0]["avg_speed_mps"] == 0.5


def test_exercise_summary_includes_zero_and_excludes_today_but_keeps_sessions():
    yesterday = exercise(duration_minutes=31)
    today = exercise(
        date=TODAY,
        start_at=yesterday.start_at + timedelta(days=1),
        end_at=yesterday.end_at + timedelta(days=1),
        duration_minutes=60,
    )
    records = [
        DailyRecord(DAY - timedelta(days=2), 0),
        *exercise_records([today, yesterday]),
    ]
    result = calculate(records, metric="exercises", start=DAY - timedelta(days=2))
    assert result.unit == "min"
    assert result.summary.avg == Decimal("15.50")
    assert result.summary.total == 31
    assert result.summary.days_with_data == result.summary.days_used_for_summary == 2
    assert result.summary.completed_days == 3
    assert result.summary.missing_days == [DAY - timedelta(days=1)]
    assert result.summary.daily_min.value == 0
    assert result.summary.daily_max.value == 31
    assert not result.days[0].missing and result.days[0].reason is None
    assert result.in_progress.value == 60 and not result.in_progress.missing
    assert result.detail.sessions == [yesterday, today]
    assert result.detail.nights == result.detail.daily_ranges == []
    assert result.detail.measured_min is result.detail.measured_max is None
    public = result.to_public().model_dump(mode="json")
    assert "user_id" not in public
    assert public["detail"]["sessions"][0]["duration_minutes"] == 31


def test_exercise_today_zero_is_valid_but_not_a_completed_summary_day():
    result = calculate([DailyRecord(TODAY, 0)], metric="exercises", start=TODAY)
    assert result.in_progress.value == 0 and not result.in_progress.missing
    assert result.days[0].reason is None
    assert result.summary.avg is result.summary.total is None
    assert result.summary.days_with_data == result.summary.completed_days == 0
    assert result.summary.missing_days == result.summary.unsynced_days == []


def test_exercise_missing_days_keep_before_link_and_unsynced_priority():
    result = calculate(
        [], metric="exercises", start=DAY - timedelta(days=1), linked=DAY
    )
    assert [day.reason for day in result.days] == [
        "BEFORE_LINK",
        "NO_RECORD",
        "UNSYNCED",
    ]
    assert result.summary.avg is result.summary.total is None
    assert result.summary.daily_min is result.summary.daily_max is None
    assert result.summary.completed_days == 1
    assert result.summary.missing_days == [DAY]
    assert result.summary.unsynced_days == [TODAY]
    assert result.detail.sessions == []
