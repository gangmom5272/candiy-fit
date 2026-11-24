import datetime as dt

import pytest

from coach_ai.tools.results import ClockRange, format_value

DAY = dt.date(2026, 10, 21)


def average(*times: str) -> str:
    items = [(DAY + dt.timedelta(days=i), dt.time.fromisoformat(t)) for i, t in enumerate(times)]
    return ClockRange.from_times(items).average


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("steps", 30.5, "31걸음"),
        ("steps", 7412.5, "7,413걸음"),
        ("min", 30.5, "31분"),
        ("min", 396.5, "6시간 37분"),
    ],
)
def test_half_is_rounded_up_in_display(unit, value, expected):
    assert format_value(unit, value) == expected


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("steps", 7412, "7,412걸음"),
        ("steps", 7412.0, "7,412걸음"),
        ("steps", 0, "0걸음"),
        ("steps", 30.4, "30걸음"),
        ("min", 397, "6시간 37분"),
        ("min", 60.0, "1시간"),
        ("bpm", 72.3, "72.3bpm"),
        ("%", 96.0, "96%"),
    ],
)
def test_values_without_half_keep_their_display(unit, value, expected):
    assert format_value(unit, value) == expected


@pytest.mark.parametrize(("value", "expected"), [(-0.5, "-1걸음"), (-2.5, "-3걸음"), (-2.4, "-2걸음")])
def test_negative_half_follows_round_half_up(value, expected):
    assert format_value("steps", value) == expected


def test_average_clock_time_at_half_minute_is_rounded_up():
    # 23:30 과 23:31 의 평균은 23:30:30 이다
    assert average("23:30", "23:31") == "23:31"
    assert average("23:58", "23:59") == "23:59"


def test_average_clock_time_at_half_minute_after_midnight_is_rounded_up():
    # 23:59 와 00:02 의 평균은 00:00:30 이다
    assert average("23:59", "00:02") == "00:01"


def test_average_clock_time_without_half_minute_is_unchanged():
    assert average("23:30", "23:32") == "23:31"
    assert average("23:30", "23:31", "23:35") == "23:32"
    assert average("07:05") == "07:05"
