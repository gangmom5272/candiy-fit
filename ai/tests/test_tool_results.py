import re
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from coach_ai.tools.results import (
    BASES,
    MEASURED_DURING,
    UNITS,
    ClockRange,
    DailyTrend,
    DatedValue,
    Duration,
    HealthOverview,
    InProgress,
    MetricSummary,
    PeriodAverage,
    PeriodComparison,
    PeriodSpan,
    SleepNight,
    SleepRecords,
    SleepSummary,
    TrendPoint,
    Value,
    format_clock_word,
    format_in_progress,
    format_minutes,
    format_sleep_name,
    format_sleep_window,
    format_value,
)

D = date
# 진행 중 값의 기준 시각이 오늘보다 이를 때 붙는 사실 문장
NOT_TODAY = "이 시각은 오늘이 아니고, 오늘 기록은 아직 반영되지 않았다"


def test_bedtime_range_crosses_midnight():
    r = ClockRange.from_times([(D(2026, 10, 12), time(23, 50)), (D(2026, 10, 13), time(0, 40))])
    assert r.earliest.time == "23:50" and r.earliest.date == D(2026, 10, 12)
    assert r.latest.time == "00:40" and r.latest.date == D(2026, 10, 13)
    assert r.average == "00:15"


def test_bedtime_range_matches_sample_week():
    bedtimes = ["00:40", "01:10", "01:55", "00:58", "23:50", "00:35"]
    items = [(D(2026, 10, 7 + i), time.fromisoformat(b)) for i, b in enumerate(bedtimes)]
    r = ClockRange.from_times(items)
    assert (r.earliest.time, r.latest.time) == ("23:50", "01:55")
    # 23:50을 전날 밤으로 이어 보면 평균은 00:51이다. 단순 평균이면 04:48이 나온다
    assert r.average == "00:51"


def test_range_without_midnight():
    r = ClockRange.from_times([(D(2026, 10, 7), time(7, 21)), (D(2026, 10, 8), time(6, 55))])
    assert (r.earliest.time, r.latest.time, r.average) == ("06:55", "07:21", "07:08")


def test_average_wraps_past_midnight():
    r = ClockRange.from_times([(D(2026, 10, 7), time(23, 30)), (D(2026, 10, 8), time(23, 50))])
    assert r.average == "23:40"
    r = ClockRange.from_times([(D(2026, 10, 7), time(23, 40)), (D(2026, 10, 8), time(0, 40))])
    assert r.average == "00:10"


def test_range_requires_times():
    with pytest.raises(ValueError):
        ClockRange.from_times([])


@pytest.mark.parametrize(("minutes", "text"), [(364, "6시간 4분"), (420, "7시간"), (45, "45분"), (0, "0분")])
def test_format_minutes(minutes, text):
    assert format_minutes(minutes) == text


def _span(period="last_7_days", basis="날짜(KST)"):
    return PeriodSpan(period=period, start=D(2026, 10, 9), end=D(2026, 10, 15), basis=basis)


def _dated(value=1):
    return DatedValue(value=value, display=str(value), date=D(2026, 10, 9))


def _summary(metric, unit, days=1, **extra):
    stats = days > 0
    heart = metric == "heart_rate" and stats
    return MetricSummary(
        metric=metric,
        period=_span(basis=BASES[metric]),
        unit=unit,
        measured_during=MEASURED_DURING.get(metric),
        avg=Value(value=1, display="1") if stats else None,
        lowest_day=_dated() if stats else None,
        highest_day=_dated() if stats else None,
        measured_min=_dated() if heart else None,
        measured_max=_dated() if heart else None,
        days_with_data=days,
        completed_days=days,
        missing_days=[],
        unsynced_days=[],
        linked_from=None,
        coverage=f"{days}일 중 {days}일 기록",
        **extra,
    )


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("unsynced", [False, True])
def test_summary_record_note_follows_status_without_changing_values(missing, unsynced):
    raw = _summary("steps", "steps").model_dump(mode="json")
    raw.update(
        missing_days=["2026-10-10"] if missing else [],
        unsynced_days=["2026-10-15"] if unsynced else [],
        record_note="이전 안내",
    )
    result = MetricSummary.model_validate(raw).model_dump(mode="json")
    assert {k: v for k, v in result.items() if k != "record_note"} == {
        k: v for k, v in raw.items() if k != "record_note"
    }
    note = result["record_note"]
    assert raw["coverage"] in note and "이전 안내" not in note
    assert ("동기화 전인 날" in note) is unsynced
    assert ("앱 동기화를 안내한다" in note) is unsynced
    assert ("평균에 0으로 포함되지 않는다" in note) is missing
    assert MetricSummary.model_validate(result).model_dump(mode="json") == result


@pytest.mark.parametrize("missing", [None, "기록 없음", "동기화 전", "연동 전"])
def test_trend_record_note_does_not_invent_missing_reason(missing):
    point = TrendPoint(date=D(2026, 10, 10), missing=missing)
    trend = DailyTrend(metric="steps", period=_span(), unit="steps", points=[point], days_with_data=0)
    result = trend.model_dump(mode="json")
    assert result["points"][0]["missing"] == missing
    assert (result["record_note"] is None) is (missing is None)
    if missing is not None:
        assert "확인할 수 없다" in result["record_note"]
        assert "미착용" not in result["record_note"]


@pytest.mark.parametrize(
    ("metric", "value", "has_progress", "expected"),
    [
        ("exercise", None, False, False),
        ("exercise", 0, False, False),
        ("exercise", 30, False, False),
        ("exercise", None, True, False),
        ("exercise", 0, True, True),
        ("exercise", 30, True, False),
        ("steps", None, False, False),
        ("steps", 0, False, False),
        ("steps", 30, False, False),
        ("steps", None, True, False),
        ("steps", 0, True, False),
        ("steps", 30, True, False),
    ],
)
def test_zero_exercise_note_requires_recorded_today_zero(metric, value, has_progress, expected):
    progress = (
        InProgress(date=D(2026, 10, 15), value=value, display="테스트 기록", as_of=None) if has_progress else None
    )
    unit = UNITS[metric]
    summary = _summary(metric, unit, in_progress=progress)
    trend = DailyTrend(metric=metric, period=_span(), unit=unit, points=[], days_with_data=0,
                       in_progress=progress)  # fmt: skip
    for result in (summary, trend):
        assert ("오늘 운동 시간은 0분" in (result.record_note or "")) is expected
        assert result.in_progress == progress
        assert type(result).model_validate(result.model_dump()) == result


def test_stats_are_taken_from_prototype_without_day_count_check():
    # 기록 일수와 통계 값의 유무는 공통 원형 값이라 서로 맞지 않아도 거부하지 않는다
    s = MetricSummary(**{**_summary("steps", "steps", days=0).model_dump(), "avg": {"value": 1, "display": "1"}})
    assert (s.days_with_data, s.avg.value) == (0, 1)
    s = MetricSummary(**{**_summary("heart_rate", "bpm").model_dump(), "avg": None, "measured_max": None})
    assert (s.days_with_data, s.avg, s.measured_max) == (1, None, None)


def test_total_only_for_steps_and_exercise():
    total = {"value": 122, "display": "2시간 2분"}
    assert MetricSummary(**{**_summary("exercise", "min").model_dump(), "total": total}).total.value == 122
    for metric, unit in [("heart_rate", "bpm"), ("oxygen_saturation", "%"), ("sleep", "min")]:
        with pytest.raises(ValidationError):
            MetricSummary(**{**_summary(metric, unit).model_dump(), "total": total})


def test_exercise_uses_minutes_and_date_basis():
    # 운동 세션은 시작한 날(KST)로 묶여 수면 종료일 기준이 아니다
    assert (UNITS["exercise"], BASES["exercise"], MEASURED_DURING.get("exercise")) == ("min", "날짜(KST)", None)
    with pytest.raises(ValidationError):
        MetricSummary(**{**_summary("exercise", "min").model_dump(), "period": _span(basis="수면 종료일(KST)")})
    with pytest.raises(ValidationError):
        DailyTrend(metric="exercise", period=_span(), unit="min", measured_during="주 수면 중", points=[],
                   days_with_data=0)  # fmt: skip


def test_trend_point_takes_prototype_day_without_relation_check():
    # 값과 결측 사유의 관계는 공통 원형이 정한 값이라 서로 맞지 않아도 거부하지 않는다
    point = TrendPoint(date=D(2026, 10, 9))
    assert (point.value, point.display, point.missing, point.in_progress) == (None, None, None, None)
    point = TrendPoint(date=D(2026, 10, 22), value=4210, display="4,210걸음", missing="동기화 전", in_progress=True)
    assert (point.missing, point.in_progress) == ("동기화 전", True)


def test_trend_point_marks_only_in_progress_day():
    # 진행 중이 아닌 날은 false 대신 비워 두어 LLM 결과에서 빠진다
    with pytest.raises(ValidationError):
        TrendPoint(date=D(2026, 10, 9), value=8000, display="8,000걸음", in_progress=False)
    point = TrendPoint(date=D(2026, 10, 9), value=8000, display="8,000걸음")
    assert point.model_dump(mode="json", exclude_none=True) == {
        "date": "2026-10-09",
        "value": 8000,
        "display": "8,000걸음",
    }


@pytest.mark.parametrize("missing", ["NO_RECORD", "기록없음", "결측"])
def test_trend_point_missing_is_reason_label(missing):
    with pytest.raises(ValidationError):
        TrendPoint(date=D(2026, 10, 9), missing=missing)


def test_daily_trend_carries_in_progress_like_summary():
    progress = InProgress(date=D(2026, 10, 15), value=4210, display="오늘 진행 중 4,210걸음", as_of=None)
    point = TrendPoint(date=D(2026, 10, 15), value=4210, display="4,210걸음", in_progress=True)
    trend = DailyTrend(metric="steps", period=_span(), unit="steps", points=[point], days_with_data=0,
                       in_progress=progress)  # fmt: skip
    assert trend.in_progress == progress
    assert DailyTrend(metric="steps", period=_span(), unit="steps", points=[], days_with_data=0).in_progress is None


def test_daily_trend_measured_extremes_only_for_heart():
    peak = DatedValue(value=134, display="134bpm", date=D(2026, 10, 14))
    trend = DailyTrend(metric="heart_rate", period=_span(), unit="bpm", points=[], days_with_data=0, measured_max=peak)
    assert (trend.measured_min, trend.measured_max) == (None, peak)
    # 요약 도구와 같이 측정 최저·최고는 심박에만 둔다
    with pytest.raises(ValidationError):
        DailyTrend(metric="steps", period=_span(), unit="steps", points=[], days_with_data=0, measured_max=peak)


def test_sleep_night_rejects_bad_clock():
    base = dict(
        date=D(2026, 10, 13), slept="10월 13일 새벽 12시 35분에 잠들어 오전 7시 5분에 깸", bedtime="00:35",
        wake="07:05", total_min=368, total="6시간 8분",
        deep_min=60, rem_min=84, light_min=224, awake_min=22,
    )  # fmt: skip
    SleepNight(**base)
    with pytest.raises(ValidationError):
        SleepNight(**{**base, "bedtime": "24:10"})


@pytest.mark.parametrize(
    "slept",
    ["10월 12일 밤", "전날 밤", "2026-10-12", "10월 13일 새벽 12시 35분", "13월 1일 새벽 1시에 잠들어 7시에 깸"],
)
def test_sleep_night_slept_is_dated_sleep_and_wake_times(slept):
    # 잠든 날과 깬 날이 다를 수 있어 "밤" 같은 때 구분 대신 실제 날짜와 시각으로 잠든 때와 깬 때를 쓴다
    base = dict(
        date=D(2026, 10, 13), slept="10월 13일 새벽 12시 35분에 잠들어 오전 7시 5분에 깸", bedtime="00:35",
        wake="07:05", total_min=368, total="6시간 8분",
        deep_min=60, rem_min=84, light_min=224, awake_min=22,
    )  # fmt: skip
    assert SleepNight(**base).slept == "10월 13일 새벽 12시 35분에 잠들어 오전 7시 5분에 깸"
    with pytest.raises(ValidationError):
        SleepNight(**{**base, "slept": slept})


def test_overview_slots_must_match_metric():
    good = dict(
        steps=_summary("steps", "steps"),
        exercise=_summary("exercise", "min"),
        sleep=_summary("sleep", "min"),
        heart_rate=_summary("heart_rate", "bpm"),
    )
    overview = HealthOverview(**good)
    # 수면은 수면 종료일 기준이라 기간 기준을 지표마다 담는다
    assert overview.sleep.period.basis == "수면 종료일(KST)" and overview.steps.period.basis == "날짜(KST)"
    with pytest.raises(ValidationError):
        HealthOverview(**{**good, "sleep": _summary("steps", "steps")})
    with pytest.raises(ValidationError):
        HealthOverview(**good, oxygen_saturation=_summary("oxygen_saturation", "%"))
    with pytest.raises(ValidationError):
        HealthOverview(
            **{**good, "steps": _summary("steps", "steps").model_copy(update={"period": _span("this_week")})}
        )


def test_results_reject_unknown_fields():
    with pytest.raises(ValidationError):
        Value(value=1, display="1", user_id="u1")


def test_heart_summary_has_daily_and_measured_extremes():
    s = _summary("heart_rate", "bpm")
    assert s.lowest_day is not None and s.measured_min is not None
    with pytest.raises(ValidationError):
        MetricSummary(**{**_summary("steps", "steps").model_dump(), "measured_min": _dated().model_dump()})


def test_oxygen_is_marked_as_main_sleep_value():
    s = _summary("oxygen_saturation", "%")
    assert s.measured_during == "주 수면 중"
    assert s.period.basis == "수면 종료일(KST)"
    with pytest.raises(ValidationError):
        MetricSummary(**{**s.model_dump(), "measured_during": None})
    with pytest.raises(ValidationError):
        MetricSummary(**{**_summary("steps", "steps").model_dump(), "measured_during": "주 수면 중"})


def test_sleep_based_metrics_use_wake_date_basis():
    with pytest.raises(ValidationError):
        MetricSummary(**{**_summary("sleep", "min").model_dump(), "period": _span().model_dump()})
    with pytest.raises(ValidationError):
        DailyTrend(metric="oxygen_saturation", period=_span(), unit="%", measured_during="주 수면 중",
                   points=[], days_with_data=0)  # fmt: skip


def test_comparison_checks_metric_fields():
    def avg(period):
        return PeriodAverage(period=_span(period, BASES["oxygen_saturation"]), avg=None, days_with_data=0,
                             completed_days=7, coverage="7일 중 0일 기록")  # fmt: skip

    base = dict(metric="oxygen_saturation", unit="%", current=avg("this_week"), baseline=avg("last_week"),
                difference=None, change_rate_pct=None, reliable=False, reliable_note="기록이 적다")  # fmt: skip
    assert PeriodComparison(**base, measured_during="주 수면 중").measured_during == "주 수면 중"
    with pytest.raises(ValidationError):
        PeriodComparison(**base)


def test_comparison_without_average_has_no_difference_and_is_not_reliable():
    def avg(period, value):
        return PeriodAverage(period=_span(period), avg=value, days_with_data=0 if value is None else 5,
                             completed_days=6, coverage="6일 중 5일 기록")  # fmt: skip

    base = dict(metric="steps", unit="steps", current=avg("this_week", Value(value=7000.0, display="7,000걸음")))
    diff = Value(value=1000.0, display="+1,000걸음")
    full = avg("last_week", Value(value=6000.0, display="6,000걸음"))
    assert PeriodComparison(**base, baseline=full, difference=diff, change_rate_pct=None, reliable=True).reliable
    empty = avg("last_week", None)
    for bad in (
        dict(difference=diff, change_rate_pct=None, reliable=False, reliable_note="기록이 적다"),
        dict(difference=None, change_rate_pct=diff, reliable=False, reliable_note="기록이 적다"),
        dict(difference=None, change_rate_pct=None, reliable=True),
    ):
        with pytest.raises(ValidationError):
            PeriodComparison(**base, baseline=empty, **bad)
    with pytest.raises(ValidationError):
        PeriodComparison(**base, baseline=full, difference=None, change_rate_pct=None, reliable=False,
                         reliable_note="기록이 적다")  # fmt: skip
    # 기록 부족 안내는 reliable 이 false 이고 진행 중 안내가 없을 때만 있다
    with pytest.raises(ValidationError):
        PeriodComparison(**base, baseline=empty, difference=None, change_rate_pct=None, reliable=False)
    progress = dict(baseline=empty, difference=None, change_rate_pct=None, reliable=False, in_progress_note="진행 중")
    assert PeriodComparison(**base, **progress).reliable_note is None
    with pytest.raises(ValidationError):
        PeriodComparison(**base, **progress, reliable_note="기록이 적다")
    with pytest.raises(ValidationError):
        PeriodComparison(**base, baseline=full, difference=diff, change_rate_pct=None, reliable=True,
                         reliable_note="기록이 적다")  # fmt: skip


def _steps(**changes):
    base = dict(
        metric="steps",
        period=_span(),
        unit="steps",
        avg=Value(value=6000, display="6,000걸음"),
        lowest_day=DatedValue(value=4000, display="4,000걸음", date=D(2026, 10, 9)),
        highest_day=DatedValue(value=8000, display="8,000걸음", date=D(2026, 10, 11)),
        total=Value(value=12000, display="12,000걸음"),
        days_with_data=2,
        completed_days=6,
        missing_days=[D(2026, 10, 10), D(2026, 10, 12), D(2026, 10, 13)],
        unsynced_days=[D(2026, 10, 14)],
        linked_from=D(2026, 9, 1),
        coverage="6일 중 2일 기록, 동기화 전 1일",
        in_progress=InProgress(date=D(2026, 10, 15), value=4210, display="오늘 진행 중 4,210걸음", as_of=None),
    )
    return {**base, **changes}


def test_summary_separates_missing_unsynced_and_in_progress():
    s = MetricSummary(**_steps())
    assert s.missing_days[-1] == D(2026, 10, 13) and s.unsynced_days == [D(2026, 10, 14)]
    assert s.in_progress is not None and s.in_progress.date not in s.missing_days + s.unsynced_days


def test_day_counts_are_taken_from_prototype_without_sum_check():
    # 일수와 결측 분류는 공통 원형 값이라 합이 맞지 않아도 거부하지 않는다
    assert MetricSummary(**_steps(completed_days=7)).completed_days == 7


def test_missing_and_unsynced_do_not_overlap():
    with pytest.raises(ValidationError):
        MetricSummary(**_steps(missing_days=[D(2026, 10, 10), D(2026, 10, 12), D(2026, 10, 14)]))


def test_in_progress_day_is_taken_from_prototype_without_stats_check():
    # 진행 중 날짜와 통계·결측 목록은 공통 원형 값이라 오늘 날짜가 섞여 있어도 거부하지 않는다
    today_max = DatedValue(value=8000, display="8,000걸음", date=D(2026, 10, 15))
    assert MetricSummary(**_steps(highest_day=today_max)).highest_day == today_max
    missing = [D(2026, 10, 10), D(2026, 10, 12), D(2026, 10, 15)]
    assert MetricSummary(**_steps(missing_days=missing)).missing_days == missing


def test_in_progress_only_for_steps_heart_and_exercise():
    progress = InProgress(date=D(2026, 10, 15), value=0, display="오늘 진행 중 0분", as_of=None).model_dump()
    s = _summary("exercise", "min")
    assert MetricSummary(**{**s.model_dump(), "in_progress": progress}).in_progress.value == 0
    for metric, unit in [("sleep", "min"), ("oxygen_saturation", "%")]:
        with pytest.raises(ValidationError):
            MetricSummary(**{**_summary(metric, unit).model_dump(), "in_progress": progress})


def _sleep_summary(**changes):
    base = dict(
        avg_total=Duration(minutes=400, display="6시간 40분"),
        shortest=None,
        longest=None,
        bedtime=None,
        wake=None,
        days_with_data=1,
        completed_days=3,
        missing_days=[D(2026, 10, 13)],
        unsynced_days=[D(2026, 10, 15)],
        linked_from=None,
        coverage="3일 중 1일 기록, 동기화 전 1일",
    )
    return {**base, **changes}


def test_sleep_summary_takes_stats_and_day_counts_from_prototype():
    # 기록 일수와 통계 값의 유무, 일수 합은 공통 원형 값이라 서로 맞지 않아도 거부하지 않는다
    assert SleepSummary(**_sleep_summary()).shortest is None  # 기록이 있는데 최단·최장이 비어 있다
    assert SleepSummary(**_sleep_summary(days_with_data=0)).avg_total is not None  # 기록이 없는데 평균이 있다
    assert SleepSummary(**_sleep_summary(avg_total=None, days_with_data=0)).completed_days == 3  # 0 + 1 + 1 ≠ 3


def test_sleep_clock_range_follows_nights():
    summary = SleepSummary(**_sleep_summary(avg_total=None, days_with_data=0, completed_days=2))
    SleepRecords(period=_span(basis="수면 종료일(KST)"), nights=[], summary=summary)
    night = SleepNight(date=D(2026, 10, 13), slept="10월 12일 밤 11시 50분에 잠들어 13일 오전 7시에 깸",
                       bedtime="23:50", wake="07:00", total_min=None, total=None, deep_min=None, rem_min=None,
                       light_min=None, awake_min=None)  # fmt: skip
    with pytest.raises(ValidationError):
        SleepRecords(period=_span(basis="수면 종료일(KST)"), nights=[night], summary=summary)


@pytest.mark.parametrize(
    ("unit", "value", "text"),
    [("steps", 4210.0, "4,210걸음"), ("bpm", 72.44, "72.4bpm"), ("bpm", 51.0, "51bpm"), ("%", 95.8, "95.8%"),
     ("min", 386.4, "6시간 26분")],
)  # fmt: skip
def test_format_value(unit, value, text):
    assert format_value(unit, value) == text


def test_format_in_progress():
    today = D(2026, 10, 15)
    as_of = datetime(2026, 10, 15, 3, 5, tzinfo=UTC)
    assert format_in_progress("steps", 4210, as_of, today) == "오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"
    assert format_in_progress("heart_rate", None, None, today) == "오늘 진행 중 기록 아직 없음"
    # 기준 시각이 이전 날이면 걸음 수·심박도 날짜를 함께 적되 "오늘"과 다른 문장에 두고, 그 시각이 오늘이 아니라는
    # 문장을 붙인다
    yesterday = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    expected = f"오늘 진행 중 71.5bpm. 10월 14일 22:00까지 기록 기준이다. {NOT_TODAY}"
    assert format_in_progress("heart_rate", 71.5, yesterday, today) == expected


def _sentences_with_today_and_date(text: str) -> list[str]:
    """text 의 문장 중 "오늘"과 "M월 D일" 날짜가 함께 든 문장."""
    return [s for s in re.split(r"(?<=\.)\s+", text) if "오늘" in s and re.search(r"\d{1,2}월 \d{1,2}일", s)]


@pytest.mark.parametrize("metric", ["steps", "heart_rate", "exercise"])
@pytest.mark.parametrize(
    ("as_of", "basis", "before_today"),
    [
        (datetime(2026, 10, 15, 3, 5, tzinfo=UTC), "12:05", False),
        (datetime(2026, 10, 14, 14, 30, tzinfo=UTC), "10월 14일 23:30", True),
        (datetime(2026, 10, 11, 0, 0, tzinfo=UTC), "10월 11일 09:00", True),
        # 자정 바로 전·정각·바로 뒤. UTC 날짜는 모두 14일이고, 오늘인지는 KST 날짜로 정한다
        (datetime(2026, 10, 14, 14, 59, tzinfo=UTC), "10월 14일 23:59", True),
        (datetime(2026, 10, 14, 15, 0, tzinfo=UTC), "00:00", False),
        (datetime(2026, 10, 14, 15, 1, tzinfo=UTC), "00:01", False),
        # 오늘보다 늦은 시각은 날짜만 적는다
        (datetime(2026, 10, 15, 15, 0, tzinfo=UTC), "10월 16일 00:00", False),
    ],
    ids=["today", "yesterday", "days_ago", "before_midnight", "midnight", "after_midnight", "tomorrow"],
)
def test_in_progress_basis_time_adds_kst_date_unless_today(metric, as_of, basis, before_today):
    if before_today:
        expected = f"오늘 진행 중 기록 아직 없음. {basis}까지 기록 기준이다. {NOT_TODAY}"
    else:
        expected = f"오늘 진행 중 기록 아직 없음 ({basis}까지 기록 기준)"
    text = format_in_progress(metric, None, as_of, D(2026, 10, 15))
    assert text == expected
    # 오늘보다 이른 날짜는 "오늘"이 든 문장에 두지 않는다. 오늘보다 늦은 날짜는 지금처럼 괄호 안에 적는다
    later_date = not before_today and "월" in basis
    assert _sentences_with_today_and_date(text) == ([text] if later_date else [])


@pytest.mark.parametrize(
    ("value", "as_of", "text"),
    [
        (0, datetime(2026, 10, 27, 4, 34, tzinfo=UTC), "오늘 진행 중 0분 (13:34까지 기록 기준)"),
        (35, datetime(2026, 10, 27, 0, 5, tzinfo=UTC), "오늘 진행 중 35분 (09:05까지 기록 기준)"),
        # 운동 기준 시각(동기화 기준 시각)이 오늘이 아니면 날짜를 함께 적는다
        (
            0,
            datetime(2026, 10, 26, 14, 50, tzinfo=UTC),
            f"오늘 진행 중 0분. 10월 26일 23:50까지 기록 기준이다. {NOT_TODAY}",
        ),
        (0, None, "오늘 진행 중 0분"),
        (None, None, "오늘 진행 중 기록 아직 없음"),
    ],
)
def test_format_exercise_in_progress_adds_date_when_basis_is_not_today(value, as_of, text):
    assert format_in_progress("exercise", value, as_of, D(2026, 10, 27)) == text


@pytest.mark.parametrize(
    ("hour", "minute", "text"),
    [
        (0, 0, "새벽 12시"),
        (5, 59, "새벽 5시 59분"),
        (6, 0, "오전 6시"),
        (11, 59, "오전 11시 59분"),
        (12, 0, "오후 12시"),
        (17, 59, "오후 5시 59분"),
        (18, 0, "저녁 6시"),
        (20, 59, "저녁 8시 59분"),
        (21, 0, "밤 9시"),
        (23, 30, "밤 11시 30분"),
    ],
)
def test_clock_word_follows_the_actual_hour(hour, minute, text):
    # 0~5시 새벽, 6~11시 오전, 12~17시 오후, 18~20시 저녁, 21~23시 밤이고 시는 12시간제다
    assert format_clock_word(time(hour, minute)) == text
    assert format_clock_word(datetime(2026, 10, 13, hour, minute, tzinfo=ZoneInfo("Asia/Seoul"))) == text


@pytest.mark.parametrize(
    ("start", "woke", "name"),
    [
        (datetime(2026, 10, 21, 17, 59), D(2026, 10, 22), "10월 22일 수면"),
        (datetime(2026, 10, 21, 18, 0), D(2026, 10, 22), "어젯밤"),
        (datetime(2026, 10, 21, 23, 30), D(2026, 10, 22), "어젯밤"),
        (datetime(2026, 10, 22, 0, 45), D(2026, 10, 22), "어젯밤"),
        (datetime(2026, 10, 22, 5, 59), D(2026, 10, 22), "어젯밤"),
        (datetime(2026, 10, 22, 6, 0), D(2026, 10, 22), "오늘 수면"),
        (datetime(2026, 10, 22, 9, 0), D(2026, 10, 22), "오늘 수면"),
        (datetime(2026, 10, 20, 23, 30), D(2026, 10, 21), "10월 21일 수면"),
        (None, D(2026, 10, 22), "어젯밤"),
    ],
)
def test_sleep_name_uses_wake_date_and_last_night_or_today(start, woke, name):
    # 오늘(10월 22일) 깬 잠은 어제 저녁 6시부터 오늘 새벽 6시 전까지 잠들었으면 어젯밤(자정 넘어 잔 밤잠 포함),
    # 오늘 아침 6시 이후(교대 근무로 아침에) 잠들었으면 오늘 수면이다. 그보다 앞서 잠들었으면 깬 날로 부른다
    start_at = None if start is None else start.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    assert format_sleep_name(woke, start_at, D(2026, 10, 22)) == name


def test_sleep_window_of_a_day_sleep_after_a_night_shift():
    # 교대 근무로 낮에 잔 잠도 "밤·낮" 같은 때 구분 없이 실제 날짜와 시각으로 쓴다
    kst = ZoneInfo("Asia/Seoul")
    day_sleep = format_sleep_window(datetime(2026, 10, 14, 9, 0, tzinfo=kst), datetime(2026, 10, 14, 16, 0, tzinfo=kst))
    assert day_sleep == "10월 14일 오전 9시에 잠들어 오후 4시에 깸"
    across = format_sleep_window(datetime(2026, 10, 31, 23, 0, tzinfo=kst), datetime(2026, 11, 1, 7, 0, tzinfo=kst))
    assert across == "10월 31일 밤 11시에 잠들어 11월 1일 오전 7시에 깸"
