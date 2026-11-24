from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.health.calculations import DailyRecord, calculate_exercise_record_comparison
from app.health.schemas import KST, ExerciseRecordComparisonQuery
from tests.health.test_calculations import STATUS, TODAY
from tests.health.test_exercise import exercise
from tests.health.test_schemas import USER

START = date(2026, 10, 10)


def session(day, code=56, counted=True):
    start = datetime.combine(day, time(23, 30), KST)
    return exercise(
        date=day,
        start_at=start,
        end_at=start + timedelta(hours=1),
        exercise_type=code,
        counts_as_exercise=counted,
    )


def activity(day, *sessions):
    return DailyRecord(
        day,
        sum(s.duration_minutes for s in sessions if s.counts_as_exercise),
        sessions=tuple(sessions),
    )


def compare(
    activities,
    values,
    *,
    metric="steps",
    start=START,
    end=None,
    code=None,
    linked=START,
    status=STATUS,
    today=TODAY,
    registered_codes=frozenset({0, 56, 70, 79}),
):
    end = end or max([r.date for r in activities], default=start)
    query = ExerciseRecordComparisonQuery.model_validate(
        dict(user_id=USER, metric=metric, start=start, end=end, exercise_type=code),
        context={"today": today},
    )
    return calculate_exercise_record_comparison(
        query,
        activities,
        values,
        linked_from=linked,
        exercise_status=status,
        metric_status=status,
        today=today,
        registered_codes=registered_codes,
    )


@pytest.mark.parametrize(
    "metric,values,expected",
    [
        ("steps", [10000, 8000, 6000, 4000], (9000, 5000, 4000)),
        ("sleeps", [450, 420, 360, 390], (435, 375, 60)),
    ],
)
def test_fixed_two_day_groups_are_comparable_but_not_reliable(metric, values, expected):
    dates = [START + timedelta(days=i) for i in range(4)]
    offset = timedelta(days=metric == "sleeps")
    result = compare(
        [
            activity(d, session(d)) if i < 2 else activity(d)
            for i, d in enumerate(dates)
        ],
        [DailyRecord(d + offset, v) for d, v in zip(dates, values, strict=True)],
        metric=metric,
    )
    assert (
        result.with_exercise.avg,
        result.without_exercise.avg,
        result.difference,
    ) == expected
    assert result.comparable and not result.reliable
    assert (
        result.with_exercise.days_with_data
        == result.without_exercise.days_with_data
        == 2
    )
    assert result.metric_period.end == dates[-1] + offset


@pytest.mark.parametrize(
    "with_count,without_count", [(0, 0), (0, 3), (3, 0), (1, 3), (2, 3), (3, 2), (3, 3)]
)
@pytest.mark.parametrize("metric", ["steps", "sleeps"])
def test_reliability_uses_actual_days_and_empty_groups_preserve_other_average(
    with_count, without_count, metric
):
    dates = [START + timedelta(days=i) for i in range(with_count + without_count)]
    result = compare(
        [
            activity(d, session(d)) if i < with_count else activity(d)
            for i, d in enumerate(dates)
        ],
        [DailyRecord(d + timedelta(days=metric == "sleeps"), 100) for d in dates],
        metric=metric,
    )
    assert result.reliable_min_days == 3
    assert result.with_exercise.days_with_data == with_count
    assert result.without_exercise.days_with_data == without_count
    assert result.reliable == (with_count >= 3 and without_count >= 3)
    assert result.comparable == bool(with_count and without_count)
    assert result.with_exercise.avg == (100 if with_count else None)
    assert result.without_exercise.avg == (100 if without_count else None)
    assert result.difference == (0 if result.comparable else None)


def test_no_half_period_requirement_and_metric_specific_missing_days():
    dates = [START + timedelta(days=i) for i in range(6)]
    activities = [
        activity(d, session(d)) if i < 3 else activity(d) for i, d in enumerate(dates)
    ]
    steps = compare(
        activities,
        [DailyRecord(d, 100) for d in dates],
        start=START - timedelta(days=200),
        linked=START - timedelta(days=200),
    )
    sleeps = compare(
        activities,
        [DailyRecord(d + timedelta(days=1), 400) for d in dates[1:]],
        metric="sleeps",
    )
    assert steps.reliable and len(steps.excluded_days) == 200
    assert not sleeps.reliable
    assert steps.reliable_min_days == sleeps.reliable_min_days == 3
    assert (
        sleeps.with_exercise.days_with_data,
        sleeps.without_exercise.days_with_data,
    ) == (2, 3)
    assert sleeps.excluded_days[0].reason == "NO_RECORD"


def test_specific_code_mixed_other_only_uncounted_and_multiple_sessions():
    dates = [START + timedelta(days=i) for i in range(4)]
    a, b, c, d = dates
    result = compare(
        [
            activity(a, session(a), session(a, 70)),
            activity(b, session(b, 70)),
            activity(c, session(c, 79, False)),
            activity(d, session(d), session(d)),
        ],
        [DailyRecord(day, 100) for day in dates],
        code=56,
    )
    assert [v.exercise_date for v in result.with_exercise.days] == [a, d]
    assert [v.exercise_date for v in result.without_exercise.days] == [c]
    assert [(v.exercise_date, v.reason) for v in result.excluded_days] == [
        (b, "OTHER_EXERCISE")
    ]
    assert result.with_exercise.days_with_data == 2


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
def test_unknown_codes_join_zero_only_for_recognized_sessions(metric):
    a, b, c, d, e, f = [START + timedelta(days=i) for i in range(6)]
    activities = [
        activity(a, session(a, 999)),
        activity(b, session(b, 0)),
        activity(c, session(c, 998, False)),
        activity(d, session(d, 70)),
        activity(e, session(e, 999), session(e, 70), session(e, 998)),
        activity(f),
    ]
    result = compare(
        activities,
        [
            DailyRecord(row.date + timedelta(days=metric == "sleeps"), 100)
            for row in activities
        ],
        code=0,
        metric=metric,
    )
    assert [v.exercise_date for v in result.with_exercise.days] == [a, b, e]
    assert [v.exercise_date for v in result.without_exercise.days] == [c, f]
    assert [(v.exercise_date, v.reason) for v in result.excluded_days] == [
        (d, "OTHER_EXERCISE")
    ]
    assert activities[0].sessions[0].exercise_type == 999


def test_registered_code_is_not_other_even_with_other_display_metadata():
    row = activity(
        START,
        session(START, 999).model_copy(
            update={"type_name": "기타 운동", "category": "other"}
        ),
    )
    result = compare(
        [row], [DailyRecord(START, 100)], code=0, registered_codes={0, 999}
    )
    assert result.with_exercise.days == []
    assert result.excluded_days[0].reason == "OTHER_EXERCISE"
    exact = compare(
        [row], [DailyRecord(START, 100)], code=999, registered_codes={0, 999}
    )
    assert exact.with_exercise.days_with_data == 1


@pytest.mark.parametrize(
    "metric,zero_reason", [("steps", "ZERO_STEPS"), ("sleeps", None)]
)
def test_metric_zero_null_and_absent_keep_existing_missing_policy(metric, zero_reason):
    dates = [START + timedelta(days=i) for i in range(3)]
    shift = timedelta(days=metric == "sleeps")
    result = compare(
        [activity(d) for d in dates],
        [DailyRecord(dates[0] + shift, 0), DailyRecord(dates[1] + shift, None)],
        metric=metric,
    )
    assert [v.reason for v in result.excluded_days] == (
        [zero_reason] if zero_reason else []
    ) + ["NULL_VALUE", "NO_RECORD"]
    assert result.without_exercise.days_with_data == int(metric == "sleeps")


def test_today_and_unsynced_exercise_are_excluded_even_with_stored_values():
    yesterday = TODAY - timedelta(days=1)
    old_status = STATUS.model_copy(
        update={"synced_until": datetime.combine(yesterday, time(12), KST)}
    )
    result = compare(
        [activity(yesterday, session(yesterday)), activity(TODAY, session(TODAY))],
        [DailyRecord(yesterday, 100), DailyRecord(TODAY, 200)],
        start=yesterday,
        end=TODAY,
        status=old_status,
    )
    assert [(v.reason, v.source) for v in result.excluded_days] == [
        ("UNSYNCED", "exercises"),
        ("IN_PROGRESS", "exercises"),
    ]


def test_before_link_and_missing_activity_are_not_normal_no_exercise_days():
    result = compare(
        [],
        [DailyRecord(START, 100), DailyRecord(START + timedelta(days=1), 100)],
        start=START,
        end=START + timedelta(days=1),
        linked=START + timedelta(days=1),
    )
    assert [(v.reason, v.source) for v in result.excluded_days] == [
        ("BEFORE_LINK", "exercises"),
        ("NO_RECORD", "exercises"),
    ]


def test_yesterday_links_today_sleep_but_today_never_links_tomorrow():
    yesterday = TODAY - timedelta(days=1)
    result = compare(
        [activity(yesterday, session(yesterday)), activity(TODAY)],
        [DailyRecord(TODAY, 420)],
        start=yesterday,
        end=TODAY,
        metric="sleeps",
    )
    assert result.with_exercise.days[0].metric_date == TODAY
    assert result.metric_period.start == result.metric_period.end == TODAY
    assert result.excluded_days[0].metric_date is None
    today_only = compare([activity(TODAY)], [], start=TODAY, metric="sleeps")
    assert today_only.metric_period is None and not today_only.comparable


def test_missing_today_sleep_uses_unsynced_reason():
    yesterday = TODAY - timedelta(days=1)
    result = compare([activity(yesterday)], [], start=yesterday, metric="sleeps")
    assert [(v.source, v.reason) for v in result.excluded_days] == [
        ("sleeps", "UNSYNCED")
    ]


@pytest.mark.parametrize("day", [date(2026, 1, 31), date(2025, 12, 31)])
def test_sleep_crosses_month_and_year_and_keeps_exercise_start_day(day):
    result = compare(
        [activity(day, session(day))],
        [DailyRecord(day + timedelta(days=1), 420)],
        start=day,
        metric="sleeps",
        linked=day,
    )
    assert result.with_exercise.days[0].exercise_date == day
    assert result.with_exercise.days[0].metric_date == day + timedelta(days=1)


def test_difference_uses_unrounded_means_and_half_up_at_output():
    dates = [START + timedelta(days=i) for i in range(6)]
    values = [1, 1, 2, 2, 2, 1]
    result = compare(
        [
            activity(d, session(d)) if i < 3 else activity(d)
            for i, d in enumerate(dates)
        ],
        [DailyRecord(d, v) for d, v in zip(dates, values, strict=True)],
    )
    assert (
        result.with_exercise.avg,
        result.without_exercise.avg,
        result.difference,
    ) == (Decimal("1.33"), Decimal("1.67"), Decimal("-0.33"))
