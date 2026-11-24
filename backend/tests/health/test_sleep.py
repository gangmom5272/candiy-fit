from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.health.calculations import oxygen_record, sleep_record
from app.health.errors import InvalidHealthData
from app.health.schemas import Night
from tests.health.test_calculations import TODAY, calculate


def night_values(day, minutes=400):
    end = datetime(2026, 10, day, 7, tzinfo=UTC) - timedelta(hours=9)
    return dict(
        date=date(2026, 10, day),
        start_at=end - timedelta(hours=8),
        end_at=end,
        sleep_minutes=minutes,
        deep_minutes=70,
        rem_minutes=85,
        light_minutes=None,
        awake_minutes=18,
    )


def night(day, minutes=400):
    return Night(
        **night_values(day, minutes),
        session_minutes=480,
        unspecified_minutes=None,
        unknown_minutes=None,
        avg_heart_rate=None,
        min_heart_rate=None,
        max_heart_rate=None,
        spo2_avg=None,
        spo2_min=None,
        spo2_max=None,
        spo2_count=None,
        spo2_low_count=None,
    )


def test_sleep_average_and_details_preserve_null_minutes_and_end_date():
    records = [
        sleep_record(night(day, minutes))
        for day, minutes in [(16, 418), (17, 441), (18, 368), (20, 340), (21, 412)]
    ]
    result = calculate(records, metric="sleeps")
    assert result.summary.avg == Decimal("395.80")
    assert result.summary.days_with_data == 5 and result.summary.completed_days == 7
    assert result.summary.missing_days == [date(2026, 10, 19)]
    assert result.summary.unsynced_days == [TODAY]
    assert len(result.detail.nights) == 5
    assert result.in_progress is result.summary.total is None
    unknown = calculate([sleep_record(night(16, None))], metric="sleeps")
    assert unknown.days[0].reason == "NULL_VALUE" and unknown.summary.avg is None
    assert unknown.detail.nights[0].start_at is not None


def test_oxygen_uses_daily_average_even_when_sleep_minutes_are_null():
    records = [
        oxygen_record(
            item["date"],
            item["start_at"],
            item["end_at"],
            spo2_avg=Decimal(value),
            spo2_min=Decimal(90),
            spo2_max=Decimal(99),
        )
        for day, value in [(16, "96.3"), (17, "95.5"), (20, "95.1"), (21, "96.0")]
        for item in [night_values(day, None)]
    ]
    result = calculate(records, metric="oxygen_saturations")
    assert result.summary.avg == Decimal("95.73")
    assert result.summary.days_with_data == 4
    assert result.detail.measured_min.date.day == 16
    assert result.detail.nights == []
    assert len(result.detail.daily_ranges) == 7
    assert result.detail.daily_ranges[2].min is None


def test_today_completed_sleep_and_oxygen_are_included():
    result = calculate([sleep_record(night(22, 420))], metric="sleeps", start=TODAY)
    assert result.summary.avg == 420 and result.summary.completed_days == 1
    assert result.in_progress is None and not result.days[0].is_in_progress
    item = night_values(22)
    oxygen = calculate(
        [
            oxygen_record(
                item["date"],
                item["start_at"],
                item["end_at"],
                spo2_avg=Decimal(96),
                spo2_min=Decimal(94),
                spo2_max=Decimal(99),
            )
        ],
        metric="oxygen_saturations",
        start=TODAY,
    )
    assert oxygen.summary.avg == 96 and oxygen.summary.days_with_data == 1


def test_non_finite_oxygen_is_a_data_contract_error():
    item = night_values(22)
    with pytest.raises(InvalidHealthData):
        oxygen_record(
            item["date"],
            item["start_at"],
            item["end_at"],
            spo2_avg=Decimal("NaN"),
            spo2_min=Decimal(90),
            spo2_max=Decimal(99),
        )
