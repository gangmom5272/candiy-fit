from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.health.calculations import DailyRecord, calculate_metric
from app.health.schemas import DataStatus, HealthQuery
from tests.health.test_schemas import USER

TODAY = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 5, tzinfo=UTC)
STATUS = DataStatus(
    last_data_at=NOW - timedelta(minutes=7),
    synced_until=NOW - timedelta(minutes=7),
    last_sync_status="success",
    last_success_at=NOW - timedelta(minutes=5),
    checked_at=NOW,
)


def calculate(
    records,
    *,
    metric="steps",
    start=date(2026, 10, 16),
    end=TODAY,
    linked=date(2026, 10, 9),
    status=STATUS,
    activity_source_statuses=None,
):
    query = HealthQuery.model_validate(
        dict(user_id=USER, metric=metric, start=start, end=end),
        context={"today": TODAY},
    )
    return calculate_metric(
        query,
        records,
        linked_from=linked,
        status=status,
        today=TODAY,
        activity_source_statuses=activity_source_statuses,
    )


def steps():
    values = {16: 7980, 17: 6450, 18: 0, 19: 8120, 21: 7310, 22: 4210}
    return [DailyRecord(date(2026, 10, day), value) for day, value in values.items()]


def test_steps_match_fixed_week_and_seven_day_examples():
    result = calculate(steps())
    assert result.summary.avg == Decimal("7465")
    assert result.summary.total == 29860
    assert result.summary.completed_days == 6
    assert result.summary.days_with_data == 4
    assert result.summary.missing_days == [date(2026, 10, 18), date(2026, 10, 20)]
    assert result.days[2].value == 0
    assert result.days[2].reason == "ZERO_STEPS"
    week = calculate(steps(), start=date(2026, 10, 19))
    assert (week.summary.avg, week.summary.total) == (7715, 15430)
    today = calculate(steps(), start=TODAY)
    assert today.summary.avg is None and today.summary.total is None
    assert today.in_progress.value == 4210
    assert today.in_progress.as_of == STATUS.last_data_at


def test_today_zero_is_preserved_without_counting_as_completed():
    result = calculate([DailyRecord(TODAY, 0)], start=TODAY)
    assert result.summary.completed_days == result.summary.days_with_data == 0
    assert result.summary.avg is result.summary.total is None
    assert result.summary.missing_days == []
    assert result.summary.unsynced_days == [TODAY]
    assert result.in_progress.value == 0 and result.in_progress.missing
    assert result.days[0].reason == "UNSYNCED"


def test_missing_priority_null_absent_zero_and_before_link():
    records = [
        DailyRecord(date(2026, 10, day), value)
        for day, value in [(16, 0), (18, None), (19, 0), (22, 0)]
    ]
    result = calculate(records, linked=date(2026, 10, 17))
    assert [day.reason for day in result.days] == [
        "BEFORE_LINK",
        "NO_RECORD",
        "NULL_VALUE",
        "ZERO_STEPS",
        "NO_RECORD",
        "NO_RECORD",
        "UNSYNCED",
    ]
    assert result.days[0].value == 0
    assert result.summary.completed_days == 5


def test_ready_without_any_records_is_before_link_with_no_statistics():
    result = calculate([], linked=None)
    assert all(day.reason == "BEFORE_LINK" for day in result.days)
    assert result.summary.completed_days == 0
    assert result.summary.missing_days == result.summary.unsynced_days == []


def test_extreme_ties_use_earliest_day_and_unknown_synced_date_is_unsynced():
    records = [
        DailyRecord(date(2026, 10, 17), 100),
        DailyRecord(date(2026, 10, 16), 100),
    ]
    result = calculate(records, status=STATUS.model_copy(update={"synced_until": None}))
    assert (
        result.summary.daily_min.date
        == result.summary.daily_max.date
        == date(2026, 10, 16)
    )
    assert result.days[2].reason == "UNSYNCED"


@pytest.mark.parametrize("value,expected", [("95.725", "95.73"), ("1.005", "1.01")])
def test_round_half_up_is_applied_only_to_final_average(value, expected):
    result = calculate([DailyRecord(date(2026, 10, 16), Decimal(value))])
    assert result.summary.avg == Decimal(expected)
    assert result.days[0].value == Decimal(value)
