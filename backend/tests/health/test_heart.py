from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.health.calculations import heart_record
from app.health.errors import InvalidHealthData
from tests.health.test_calculations import TODAY, calculate


def hours(count, value=70):
    return {
        str(hour): dict(avg=value, min=50, max=140, count=1) for hour in range(count)
    }


def test_heart_uses_hour_count_not_samples_or_daily_mean():
    records = [
        heart_record(
            date(2026, 10, day), Decimal(avg), samples, hours(count, avg), low, high
        )
        for day, avg, count, samples, low, high in [
            (19, 70, 14, 150, 50, 110),
            (20, 76, 15, 2000, 52, 140),
            (21, 95, 1, 8, 60, 130),
        ]
    ]
    result = calculate(
        records, metric="heart_rates", start=date(2026, 10, 19), end=date(2026, 10, 21)
    )
    assert result.summary.avg == Decimal("73.83")
    assert (
        result.summary.daily_min.value == 70 and result.summary.daily_min.date.day == 19
    )
    assert (
        result.summary.daily_max.value == 95 and result.summary.daily_max.date.day == 21
    )
    assert (
        result.detail.measured_min.value == 50
        and result.detail.measured_min.date.day == 19
    )
    assert (
        result.detail.measured_max.value == 140
        and result.detail.measured_max.date.day == 20
    )
    assert result.in_progress is result.summary.total is None
    assert len(result.detail.daily_ranges) == 3
    assert [item.sample_count for item in result.detail.daily_ranges] == [150, 2000, 8]


@pytest.mark.parametrize(
    "hourly",
    [
        None,
        {},
        [],
        {"24": dict(avg=70, min=50, max=100, count=1)},
        {"0": dict(avg=70)},
        {"0": dict(avg=70, min=80, max=100, count=1)},
    ],
)
def test_invalid_hour_contract_is_not_normal_missing(hourly):
    with pytest.raises(InvalidHealthData):
        heart_record(TODAY, Decimal(70), 100, hourly, 50, 100)


def test_no_samples_is_missing_and_today_is_separate_with_range():
    assert heart_record(TODAY, None, 0, None, None, None).value is None
    result = calculate(
        [heart_record(TODAY, Decimal("72.3"), 10, hours(1), 50, 100)],
        metric="heart_rates",
        start=TODAY,
    )
    assert result.in_progress.value == Decimal("72.3")
    assert result.summary.avg is result.detail.measured_min is None
    assert result.detail.daily_ranges[0].min == 50


def test_yesterday_average_matches_q5_without_in_progress():
    yesterday = TODAY - timedelta(days=1)
    result = calculate(
        [heart_record(yesterday, Decimal("72.3"), 20, hours(2), 50, 100)],
        metric="heart_rates",
        start=yesterday,
        end=yesterday,
    )
    assert result.summary.avg == Decimal("72.30")
    assert result.summary.days_with_data == result.summary.completed_days == 1
    assert result.in_progress is None


def test_non_finite_average_is_a_data_contract_error():
    with pytest.raises(InvalidHealthData):
        heart_record(TODAY, Decimal("NaN"), 20, hours(2), 50, 100)


@pytest.mark.parametrize("count", [-1, True, "1", 1.5])
def test_daily_sample_count_is_a_strict_nonnegative_integer(count):
    with pytest.raises(InvalidHealthData):
        heart_record(TODAY, None, count, None, None, None)


def test_zero_samples_null_average_and_absent_day_are_distinct():
    result = calculate(
        [heart_record(TODAY, None, 0, None, None, None)],
        metric="heart_rates",
        start=TODAY - timedelta(days=1),
        end=TODAY,
    )
    assert [item.sample_count for item in result.detail.daily_ranges] == [None, 0]
    assert result.summary.avg is None


@pytest.mark.parametrize("average", ["59.5", "59.6", "60.4", "60.5"])
def test_rounded_heart_ranges_preserve_average_extremes_and_count(average):
    record = heart_record(
        TODAY,
        Decimal(average),
        1,
        {"0": dict(avg=float(average), min=60, max=60, count=1)},
        60,
        60,
    )
    assert record.value == Decimal(average)
    assert record.measured_min == record.measured_max == 60
    assert record.sample_count == 1


@pytest.mark.parametrize("target", ["daily", "hourly"])
@pytest.mark.parametrize(
    "average,low,high", [("59.49", 60, 60), ("60.51", 60, 60), ("60.5", 61, 60)]
)
def test_heart_ranges_reject_beyond_rounding_tolerance_and_reversed_extremes(
    target, average, low, high
):
    hourly = dict(avg=60, min=60, max=60, count=1)
    daily = (Decimal(60), 60, 60)
    if target == "daily":
        daily = (Decimal(average), low, high)
    else:
        hourly.update(avg=Decimal(average), min=low, max=high)
    with pytest.raises(InvalidHealthData):
        heart_record(TODAY, daily[0], 1, {"0": hourly}, daily[1], daily[2])
