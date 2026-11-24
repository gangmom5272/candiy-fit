"""수면 코칭 계산 (calculation-rules.md 11장, 계산 세부 11.9)."""

import dataclasses
import datetime as dt
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest

from coach_ai.ports import ResultDay
from coach_ai.tools.periods import DateRange
from coach_ai.tools.sleep_coaching import (
    ClockStat,
    DayRecord,
    LastNightValue,
    NightGroup,
    NightRecord,
    NotJudged,
    SleepCoachingInput,
    SleepVsBaseline,
    Spo2AvgRange,
    WeekendGap,
    day_records,
    factors,
    last_night_vs_week,
    pattern,
    signals,
    sleep_vs_baseline,
)
from eval.golden.fixtures import load_fixture

KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.date(2026, 10, 22)  # 목요일
LINKED = dt.date(2026, 1, 1)
SYNCED = dt.datetime(2026, 10, 22, 20, 0, tzinfo=KST)

FIXTURE = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "golden" / "fixtures" / "synth-11.json"
REFERENCE = Path(__file__).parent / "data" / "sleep_reference_synth-11.json"
# 참고 구현 결과에 없는 칸. pattern 의 spo2_low_nights 와 sleep_vs_baseline.declining 은 계산에만 있고, signals 의
# baseline_period 는 도구 정의 초안 3.1 의 공통 칸이고, 나머지는 결과 문장에 쓴다 (synth-11 의 90% 미만 시각은 5개)
ONLY_CALCULATED = (
    "pattern.spo2_low_nights.low_nights",
    "pattern.spo2_low_nights.recorded_nights",
    "pattern.sleep_vs_baseline.declining",
    "signals.baseline_period.start",
    "signals.baseline_period.end",
    *(f"signals.spo2_low_repeat.slept_at.{i}" for i in range(5)),
    *(f"signals.spo2_low_repeat.woke_on.{i}" for i in range(5)),
    "factors.steps_median",
    "factors.day_hr_median",
    *(
        f"signals.spo2_avg_range.{side}.{key}"
        for side in ("lowest", "highest")
        for key in ("value", "date", "start_at")
    ),
    "signals.spo2_avg_range.nights",
)


def days_before(n: int) -> dt.timedelta:
    return dt.timedelta(days=n)


def back(n: int, end: dt.date = TODAY) -> list[dt.date]:
    """end 부터 거꾸로 n 일."""
    return [end - days_before(i) for i in range(n)]


def night(
    wake: dt.date,
    bed: str = "23:00",
    hours: float = 8,
    *,
    sleep: int | None = None,
    awake: int | None = 10,
    deep: int | None = 60,
    hr: float | None = 60.0,
    low: int | None = 0,
    low_times: tuple[dt.datetime, ...] | None = None,
    main: bool = True,
    spo2: float | None = None,
) -> NightRecord:
    """wake 에 깬 수면. bed 는 잠든 시각(KST "HH:MM")이고 18시 이후면 전날 밤이다. spo2 는 산소포화도 평균이다."""
    h, m = map(int, bed.split(":"))
    start = dt.datetime(wake.year, wake.month, wake.day, h, m, tzinfo=KST) - days_before(1 if h >= 18 else 0)
    session = round(hours * 60)
    return NightRecord(
        date=wake,
        start_at=start,
        end_at=start + dt.timedelta(minutes=session),
        session_minutes=session,
        sleep_minutes=session - (awake or 0) if sleep is None else sleep,
        awake_minutes=awake,
        deep_minutes=deep,
        sleep_hr_avg=hr,
        spo2_low_count=low,
        spo2_low_times=low_times,
        is_main=main,
        spo2_avg=spo2,
    )


def day(
    d: dt.date,
    *,
    steps: int | None = 5000,
    hr: float | None = 70.0,
    worn: bool | None = True,
    ends: tuple[dt.datetime, ...] = (),
    row: bool = True,
) -> DayRecord:
    return DayRecord(
        date=d,
        has_activity_row=row,
        steps=steps if row else None,
        steps_missing=not row or not steps,
        day_hr_avg=hr,
        well_worn=worn,
        exercise_ends=ends,
    )


def data(nights, days=(), *, today=TODAY, linked=LINKED, synced=SYNCED) -> SleepCoachingInput:
    return SleepCoachingInput(
        today=today, linked_from=linked, synced_until=synced, nights=tuple(nights), days=tuple(days)
    )


def at(d: dt.date, clock: str) -> dt.datetime:
    h, m = map(int, clock.split(":"))
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def half_up(x: float) -> int:
    """비교용 표시 반올림. 0.5 는 올린다 (11.9)."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def hhmm(minutes: float) -> str:
    m = half_up(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def problem_types(result) -> list[str]:
    return [p.type for p in result.problems]


# 11.9 계산 세부


@pytest.mark.parametrize("extra, hit", [(0, False), (7, True)])  # 14일 깬 시간 평균 30.0분 / 30.5분
def test_fragmented_is_judged_on_unrounded_awake_average(extra, hit):
    awakes = [30] * 14
    awakes[0] += extra
    nights = [night(d, "23:30", 7.5, awake=a, sleep=420) for d, a in zip(back(14), awakes, strict=True)]
    p = pattern(data(nights))
    assert p.awake_avg_min.value == 30 + extra / 14
    assert ("fragmented" in problem_types(p)) is hit


def test_clock_average_crosses_midnight():
    beds = ["23:50", "00:40", "23:50", "00:40"]
    p = pattern(data(night(d, b, 7) for d, b in zip(back(4), beds, strict=True)))
    assert p.bedtime.avg == 15 and hhmm(p.bedtime.avg) == "00:15"
    assert p.bedtime.sd_min == pytest.approx((4 * 25**2 / 3) ** 0.5)


def test_clock_average_cuts_widest_gap_for_daytime_sleep():
    # 낮에 자는 사람도 가장 넓게 빈 구간(밤)을 잘라 평균한다
    beds = ["09:00", "10:00", "09:30", "09:30"]
    p = pattern(data(night(d, b, 6) for d, b in zip(back(4), beds, strict=True)))
    assert p.bedtime.avg == 9 * 60 + 30


def test_weekday_weekend_gap_unwraps_all_nights_together():
    # 가운데 시각 평일 23:50, 주말 00:50 (잠든 시각 + 세션 ÷ 2). 그룹마다 따로 처리하면 −1380분이 된다
    nights = [night(d, "21:20" if d.weekday() >= 5 else "20:20", 7) for d in back(14)]
    p = pattern(data(nights))
    assert p.midpoint_gap_min == WeekendGap(min=60, weekday_nights=10, weekend_nights=4)


def test_ratio_average_is_mean_of_nightly_ratios():
    ds = back(4)
    nights = [night(ds[0], "23:00", 6, awake=0, sleep=300, deep=60)]  # 깊은 잠 20%, 효율 83.3%
    nights += [night(d, "23:00", 10, awake=0, sleep=600, deep=60) for d in ds[1:]]  # 10%, 100%
    p = pattern(data(nights))
    # 합끼리 나누면 깊은 잠 240/2100 = 11.4%, 효율 2100/2160 = 97.2%
    assert p.deep_ratio_avg_pct.value == pytest.approx(12.5)
    assert p.efficiency_avg_pct.value == pytest.approx((100 * 300 / 360 + 300) / 4)


def test_difference_is_rounded_after_subtracting_unrounded_averages():
    recent = [night(d, "23:00", 7, sleep=s) for d, s in zip(back(5), [350, 350, 351, 351, 351], strict=True)]
    base_days = back(5, TODAY - days_before(14))
    base = [night(d, "23:00", 7, sleep=s) for d, s in zip(base_days, [395, 395, 395, 396, 396], strict=True)]
    v = pattern(data(recent + base)).sleep_vs_baseline
    assert (v.recent_avg, v.baseline_avg, v.diff) == (pytest.approx(350.6), pytest.approx(395.4), pytest.approx(-44.8))
    # 반올림한 평균끼리 빼면 351 − 395 = −44 다
    assert half_up(v.diff) == -45 and half_up(v.recent_avg) - half_up(v.baseline_avg) == -44


def test_terciles_sort_each_way():
    ds = sorted(back(6))
    nights = [night(d, "23:00", 10, sleep=s) for d, s in zip(ds, [400, 550, 300, 500, 350, 450], strict=True)]
    gb = factors(data(nights)).good_bad_nights
    assert (gb.good.sleep_range, gb.bad.sleep_range) == ((500, 550), (300, 350))


def test_tercile_ties_take_earlier_night():
    ds = sorted(back(6))
    nights = [night(d, "23:00", 8, sleep=s) for d, s in zip(ds, [450, 300, 450, 300, 450, 300], strict=True)]
    # 그룹에 든 밤을 전날(D) 걸음으로 구분한다
    days = [day(d - days_before(1), steps=1000 * (i + 1)) for i, d in enumerate(ds)]
    gb = factors(data(nights, days)).good_bad_nights
    # k = 2. 잘 잔 밤은 450분 중 먼저 깬 1·3번째 밤, 못 잔 밤은 300분 중 먼저 깬 2·4번째 밤
    assert (gb.good.nights, gb.good.steps_avg) == (2, 2000)
    assert (gb.bad.nights, gb.bad.steps_avg) == (2, 3000)


def test_short_sleep_streak_breaks_on_day_without_record():
    ds = sorted(back(5))
    nights = [night(d, "01:00", 4.5) for i, d in enumerate(ds) if i != 2]  # 가운데 하루 기록 없음
    s = signals(data(nights)).short_sleep_streak
    assert (s.hit, s.longest, s.dates) == (False, 2, tuple(ds[:2]))
    s = signals(data([*nights, night(ds[2], "01:00", 4.5)])).short_sleep_streak
    assert (s.hit, s.longest, s.dates) == (True, 5, tuple(ds))


def test_short_sleep_is_below_five_hours():
    ds = sorted(back(3))
    s = signals(data(night(d, "01:00", 6, sleep=300) for d in ds)).short_sleep_streak
    assert (s.hit, s.longest, s.dates) == (False, 0, ())
    s = signals(data(night(d, "01:00", 6, sleep=299) for d in ds)).short_sleep_streak
    assert (s.hit, s.longest) == (True, 3)


def test_late_bedtime_streak_compares_after_midnight_handling():
    period = DateRange(TODAY - days_before(9), TODAY)
    late = {TODAY - days_before(6), TODAY - days_before(5)}
    nights = [night(d, "00:50" if d in late else "23:30", 7) for d in back(12)]
    days = [day(d) for d in back(10, TODAY - days_before(1))]
    c = factors(data(nights, days), period, "late_bedtime_streak").conditions[0]
    # 기간 잠든 시각 평균 23:46, 00:50 은 64분 늦다. 둘 다 늦은 D 1일, 둘 다 아닌 D 7일, 하나만 늦은 D 2일은 제외
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights, c.excluded_days) == (1, 7, 2)


@pytest.mark.parametrize(
    "default, beds, outcome",
    [
        # 평균 23:40. 00:40 은 딱 60분 늦어 해당하고, 00:39 는 59분이라 해당하지 않는다. 00:41 은 61분
        ("23:30", {10: "00:40", 11: "00:40", 15: "00:39", 16: "00:41"}, 12),
        # 평균 00:20. 01:20 은 딱 60분 늦고, 자정 전 23:20 은 60분 이르다
        ("00:20", {5: "01:20", 6: "01:20", 20: "01:20", 12: "23:20", 13: "23:20", 25: "23:20"}, 7),
    ],
    ids=["mean_before_midnight", "mean_after_midnight"],
)
def test_late_bedtime_streak_matches_hand_calculation(default, beds, outcome):
    # k 번째 깬 날. 1 이 분석 기간 첫날(09-25), 28 이 오늘이고 0·−1 은 기간 밖 밤이다
    def wake(k: int) -> dt.date:
        return TODAY - days_before(28 - k)

    nights = [night(wake(k), beds.get(k, default), 8, sleep=400 if k == outcome else 420) for k in range(-1, 29)]
    days = [day(wake(k)) for k in range(-1, 29)]
    c = factors(data(nights, days), condition="late_bedtime_streak").conditions[0]
    m = c.sleep_minutes
    # 둘 다 늦은 D 1일(그날 밤 400분), 하루만 늦은 D 4일은 제외, 둘 다 아닌 D 23일(420분)
    assert (m.true_avg, m.true_nights, m.false_avg, m.false_nights, m.diff, c.excluded_days) == (
        400,
        1,
        420,
        23,
        -20,
        4,
    )


@pytest.mark.parametrize("recent_pct, hit", [(16, True), (17, False)])
def test_low_deep_is_relative_change_not_points(recent_pct, hit):
    # 기준 20% → 16%는 상대 변화 −20(해당), 17%는 −15(해당 없음). %포인트로는 −4·−3이다
    base = [night(d, "23:00", 9, sleep=500, deep=100) for d in back(28, TODAY - days_before(14))]
    recent = [night(d, "23:00", 9, sleep=500, deep=5 * recent_pct) for d in back(14)]
    p = pattern(data(base + recent))
    v = p.deep_ratio_vs_baseline
    assert (v.recent_avg_pct, v.baseline_avg_pct, v.change_pct) == (recent_pct, 20, recent_pct * 5 - 100)
    assert ("low_deep" in problem_types(p)) is hit


def test_condition_dates_are_day_before_each_wake_date():
    # 분석 기간 깬 날짜 10-16~10-20 → 조건 날짜 D 10-15~10-19
    period = DateRange(TODAY - days_before(6), TODAY - days_before(2))
    sleeps = {7: 200, 6: 400, 5: 350, 4: 400, 3: 400, 2: 600}
    nights = [night(TODAY - days_before(k), "23:00", 11, sleep=s) for k, s in sleeps.items()]
    steps = {7: 1000, 6: 2000, 5: 3000, 4: 4000, 3: 5000, 2: 100_000, 1: 100_000}
    days = [day(TODAY - days_before(k), steps=s) for k, s in steps.items()]
    f = {c.condition: c for c in factors(data(nights, days), period).conditions}
    # 수면 시간 평균은 분석 기간의 밤(430분)으로 낸다. 조건 날짜에 깬 밤으로 내면 350분이라 짧은 밤이 1일이 된다
    short = f["short_prev_night"].sleep_minutes
    assert (short.true_nights, short.false_nights) == (2, 3)
    # 걸음 중앙값은 조건 날짜 D 의 걸음(3000)으로 낸다. 깬 날짜의 걸음으로 내면 4000이다
    many = f["high_steps"].sleep_minutes
    assert (many.true_nights, many.false_nights) == (3, 2)


# 11.2·11.3 pattern


def test_pattern_default_period_and_baseline():
    p = pattern(data(night(d) for d in back(14)))
    assert (p.period, p.baseline_period) == (
        DateRange(dt.date(2026, 10, 9), TODAY),
        DateRange(dt.date(2026, 9, 11), dt.date(2026, 10, 8)),
    )
    assert (p.analysis, p.nights, p.enough_data) == ("pattern", 14, True)


def test_pattern_with_too_few_nights_judges_nothing():
    base = [night(d, "23:00", 8) for d in back(20, TODAY - days_before(14))]
    p = pattern(data([*base, *(night(d, "02:00", 4) for d in back(3))]))
    assert (p.nights, p.enough_data, p.problems) == (3, False, ())
    assert p.not_judged == (NotJudged(type="all", reason="nights_below_4", nights=3),)
    assert p.bedtime == ClockStat(avg=None, sd_min=None, nights=3)
    assert p.sleep_avg_min.value is None
    # 줄어드는 중에 쓴 값은 4.1 비교처럼 밤 수가 적어도 낸다. 최근 14일이 reliable 이 아니라 판정하지 않는다
    assert p.sleep_vs_baseline == SleepVsBaseline(
        recent_avg=230,
        baseline_avg=470,
        diff=-240,
        recent_nights=3,
        baseline_nights=20,
        recent_reliable=False,
        baseline_reliable=True,
        declining=None,
    )


def test_average_needs_four_nights_with_value():
    nights = [night(d, awake=None if i < 2 else 10) for i, d in enumerate(back(5))]
    p = pattern(data(nights))
    assert (p.awake_avg_min.value, p.awake_avg_min.nights) == (None, 3)


@pytest.mark.parametrize("recent_sleep, hit", [(360, True), (361, False)])
def test_declining_is_sixty_minutes_or_more_below_baseline(recent_sleep, hit):
    base = [night(d, "23:00", 8, sleep=420) for d in back(28, TODAY - days_before(14))]
    recent = [night(d, "23:00", 8, sleep=recent_sleep) for d in back(14)]
    p = pattern(data(base + recent))
    assert ("declining" in problem_types(p)) is hit
    v = p.sleep_vs_baseline
    assert (v.diff, v.recent_nights, v.baseline_nights, v.recent_reliable, v.baseline_reliable, v.declining) == (
        recent_sleep - 420,
        14,
        28,
        True,
        True,
        hit,
    )


def test_declining_not_judged_when_baseline_is_not_reliable():
    # 기준 28일 중 13일은 완성된 날의 절반이 안 된다 (4.1)
    base = [night(d, "23:00", 8, sleep=480) for d in back(13, TODAY - days_before(14))]
    recent = [night(d, "23:00", 8, sleep=360) for d in back(14)]
    p = pattern(data(base + recent))
    assert "declining" not in problem_types(p)
    assert NotJudged(type="declining", reason="not_reliable", nights=14, baseline_nights=13) in p.not_judged
    v = p.sleep_vs_baseline
    assert (v.diff, v.baseline_reliable, v.declining) == (-120, False, None)


def test_sleep_vs_baseline_uses_fourteen_days_to_base_date_and_twenty_eight_before():
    base = TODAY - days_before(3)
    nights = [night(d, sleep=360) for d in back(14, base)]  # 기준일까지 14일
    nights += [night(d, sleep=420) for d in back(28, base - days_before(14))]  # 그 전 28일
    nights += [night(d, sleep=600) for d in back(3)]  # 기준일 뒤의 밤은 쓰지 않는다
    assert sleep_vs_baseline(data(nights), base) == SleepVsBaseline(
        recent_avg=360,
        baseline_avg=420,
        diff=-60,
        recent_nights=14,
        baseline_nights=28,
        recent_reliable=True,
        baseline_reliable=True,
        declining=True,
    )


@pytest.mark.parametrize("period", [None, DateRange(TODAY - days_before(9), TODAY - days_before(3))])
def test_sleep_vs_baseline_alone_matches_pattern(period):
    inp = data(night(d, sleep=400 + i % 5 * 10) for i, d in enumerate(back(45)))
    p = pattern(inp, period)
    assert p.sleep_vs_baseline == sleep_vs_baseline(inp, p.period.end)
    # 질문 기간이 7일이어도 기간 끝을 기준일로 14일과 그 전 28일을 비교한다
    assert (p.sleep_vs_baseline.recent_nights, p.sleep_vs_baseline.baseline_nights) == (14, 28)


def test_sleep_vs_baseline_rejects_base_date_after_today():
    with pytest.raises(ValueError):
        sleep_vs_baseline(data([night(TODAY)]), TODAY + dt.timedelta(days=1))


def test_reliable_counts_completed_days_from_linked_date():
    # 기준 기간 마지막 6일만 연동 뒤라 완성된 날은 6일이고, 4일이면 reliable 이다
    linked = TODAY - days_before(19)
    base = [night(d, "23:00", 8, sleep=480) for d in back(4, TODAY - days_before(14))]
    recent = [night(d, "23:00", 8, sleep=400) for d in back(14)]
    p = pattern(data(base + recent, linked=linked))
    assert p.sleep_vs_baseline.baseline_reliable
    assert "declining" in problem_types(p)


def test_low_deep_not_judged_with_fewer_than_seven_baseline_nights():
    base = [night(d, "23:00", 9, sleep=500, deep=100) for d in back(6, TODAY - days_before(14))]
    recent = [night(d, "23:00", 9, sleep=500, deep=50) for d in back(14)]
    p = pattern(data(base + recent))
    assert "low_deep" not in problem_types(p)
    assert NotJudged(type="low_deep", reason="baseline_below_7", nights=14, baseline_nights=6) in p.not_judged
    assert p.deep_ratio_vs_baseline.change_pct == -50


def test_problem_values_and_order():
    # 짧음·불규칙(잠든 시각 흔들림)·자주 깸이 함께 나온다
    beds = ["22:00", "02:00"] * 7
    nights = [night(d, b, 6.5, awake=40) for d, b in zip(back(14), beds, strict=True)]
    p = pattern(data(nights))
    assert problem_types(p) == ["short", "irregular", "fragmented"]
    short, irregular, fragmented = p.problems
    assert (short.value, short.threshold, short.nights) == (350, 420, 14)
    assert irregular.bedtime_sd_min == p.bedtime.sd_min and irregular.bedtime_sd_min >= 60
    assert (fragmented.awake_avg_min, fragmented.nights) == (40, 14)


@pytest.mark.parametrize(
    "sleeps, hit",
    [
        ([540] * 14, True),  # 평균 540분 = 9시간
        ([540] * 13 + [533], False),  # 평균 539.5분. 표시는 540분이지만 반올림 전 값으로 판정한다
        ([540] * 13 + [541], True),  # 평균 540.07분
    ],
    ids=["exactly_540", "539.5", "540.07"],
)
def test_long_sleep_is_nine_hours_or_more_on_unrounded_average(sleeps, hit):
    nights = [night(d, "22:00", 10, sleep=s) for d, s in zip(back(14), sleeps, strict=True)]
    p = pattern(data(nights))
    assert ("long" in problem_types(p)) is hit
    assert "short" not in problem_types(p)
    assert p.sleep_avg_min.value == pytest.approx(sum(sleeps) / 14)
    if hit:
        [long] = [x for x in p.problems if x.type == "long"]
        assert (long.value, long.threshold, long.nights) == (pytest.approx(sum(sleeps) / 14), 540, 14)


def test_long_sleep_comes_right_after_short_in_problem_order():
    # 긴 수면과 줄어드는 중이 함께 나오면 11.3 표 순서대로 긴 수면이 앞이다 (참고 구현과 같다)
    base = [night(d, "22:00", 11, sleep=640) for d in back(28, TODAY - days_before(14))]
    recent = [night(d, "22:00", 10, sleep=560) for d in back(14)]
    assert problem_types(pattern(data(base + recent))) == ["long", "declining"]


def test_short_and_long_are_not_judged_as_one_entry_without_four_nights_of_sleep_minutes():
    nights = [
        night(d, sleep=600) if i < 3 else dataclasses.replace(night(d), sleep_minutes=None)
        for i, d in enumerate(back(14))
    ]
    p = pattern(data(nights))
    assert not {"short", "long"} & set(problem_types(p))
    # 평균 수면 시간이 없으면 두 유형 모두 판정하지 않고, not_judged 에는 참고 구현처럼 short 하나로 둔다
    assert [j for j in p.not_judged if j.type in ("short", "long")] == [
        NotJudged(type="short", reason="nights_below_4", nights=3)
    ]


def test_non_main_sleep_is_ignored():
    nights = [night(d) for d in back(4)] + [night(d, "14:00", 1, main=False) for d in back(4)]
    p = pattern(data(nights))
    assert (p.nights, p.bedtime.avg) == (4, 23 * 60)


def test_pattern_counts_low_spo2_and_recorded_nights():
    ds = back(5)
    t = at(ds[0], "03:00")
    nights = [
        night(ds[0], low=2, low_times=(t, t)),
        night(ds[1], low=1),  # 시각 칸이 생기기 전에 집계한 행
        night(ds[2], low=0, low_times=()),
        night(ds[3], low=None),  # 산소포화도 기록 없음
        night(ds[4], low=0),
    ]
    p = pattern(data(nights))
    assert (p.spo2_low_nights.low_nights, p.spo2_low_nights.recorded_nights) == (2, 4)


# 11.4 factors


def test_condition_dates_skip_unlinked_unsynced_and_no_activity_row():
    period = DateRange(TODAY - days_before(5), TODAY)  # D 10-16~10-21
    nights = [night(d) for d in back(6)]
    days = [day(TODAY - days_before(k), row=k != 3) for k in range(1, 7)]
    inp = data(nights, days, linked=TODAY - days_before(5), synced=at(TODAY - days_before(1), "09:00"))
    c = factors(inp, period, "weekend").conditions[0]
    # 10-16 연동 전, 10-19 활동 행 없음, 10-21 아직 안 들어옴(동기화 기준일과 같음)
    assert c.excluded_days == 3
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights) == (1, 2)  # 10-18(일)에 깬 밤만 주말


def test_late_exercise_ends_within_three_hours_of_bedtime():
    period = DateRange(TODAY - days_before(3), TODAY)  # D 10-18~10-21, 그날 밤 23:00 잠듦
    nights = [night(d, "23:00", 8) for d in back(4)]
    ds = sorted(back(4, TODAY - days_before(1)))
    days = [
        day(ds[0], ends=(at(ds[0], "20:00"),)),  # 정확히 3시간 전 → 늦은 운동
        day(ds[1], ends=(at(ds[1], "19:59"),)),  # 3시간 1분 전 → 일찍 끝남
        day(ds[2], ends=(at(ds[2], "10:00"), at(ds[2], "21:30"))),  # 하나라도 3시간 안
        day(ds[3]),  # 인정 운동 없음 → 제외
    ]
    c = factors(data(nights, days), period, "late_exercise").conditions[0]
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights, c.excluded_days) == (2, 1, 1)


def test_exercise_condition_and_group_values():
    period = DateRange(TODAY - days_before(7), TODAY)
    nights = [night(d, "23:00", 8, sleep=480 if i % 2 else 420, deep=50 + i) for i, d in enumerate(back(9))]
    ds = sorted(back(8, TODAY - days_before(1)))
    days = [day(d, ends=(at(d, "18:00"),) if i % 2 else ()) for i, d in enumerate(ds)]
    c = factors(data(nights, days), period, "exercise").conditions[0]
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights) == (4, 4)
    assert c.sleep_minutes.enough_data
    assert c.deep_minutes.diff == c.deep_minutes.true_avg - c.deep_minutes.false_avg


def test_high_steps_median_of_even_count_is_mean_of_middle_two():
    period = DateRange(TODAY - days_before(3), TODAY)
    nights = [night(d) for d in back(4)]
    ds = sorted(back(4, TODAY - days_before(1)))
    days = [day(d, steps=s) for d, s in zip(ds, [1000, 2000, 3000, 4000], strict=True)]
    f = factors(data(nights, days), period, "high_steps")
    c = f.conditions[0]
    # 중앙값 2500 → 3000·4000 만 걸음 많은 날. 가른 값은 결과 문장에 쓰게 결과에 넣는다
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights) == (2, 2)
    assert f.steps_median == 2500


def test_missing_steps_are_left_out_of_median_and_groups():
    period = DateRange(TODAY - days_before(3), TODAY)
    nights = [night(d) for d in back(4)]
    ds = sorted(back(4, TODAY - days_before(1)))
    days = [day(d, steps=s) for d, s in zip(ds, [1000, 0, None, 3000], strict=True)]  # 0보와 NULL 은 결측
    f = factors(data(nights, days), period, "high_steps")
    c = f.conditions[0]
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights, c.excluded_days) == (1, 1, 2)
    assert f.steps_median == 2000


@pytest.mark.parametrize("worn", [False, None], ids=["short_wear", "unknown_wear"])
def test_high_day_hr_leaves_out_only_days_not_known_as_well_worn(worn):
    period = DateRange(TODAY - days_before(6), TODAY)
    nights = [night(d) for d in back(7)]
    ds = sorted(back(7, TODAY - days_before(1)))
    hrs = [(60, True), (60, True), (60, True), (65, True), (90, worn), (90, worn), (90, worn)]
    days = [day(d, hr=h, worn=w) for d, (h, w) in zip(ds, hrs, strict=True)]
    f = factors(data(nights, days), period, "high_day_hr")
    c = f.conditions[0]
    # 충분히 착용한 날의 중앙값 60 → 65 는 5bpm 이상 높다. 90bpm 인 날이 중앙값에 들어가면 중앙값이 65 가 된다.
    # 착용이 짧거나 착용을 모르는 날은 그날만 중앙값과 그룹에서 빠지고 excluded_days 에 들어간다
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights, c.excluded_days) == (1, 3, 3)
    assert f.day_hr_median == 60


def test_high_day_hr_is_not_judged_when_wear_is_unknown_for_every_day():
    period = DateRange(TODAY - days_before(6), TODAY)
    nights = [night(d) for d in back(7)]
    days = [day(d, hr=60.0 + i, worn=None) for i, d in enumerate(back(7, TODAY - days_before(1)))]
    f = factors(data(nights, days), period, "high_day_hr")
    c = f.conditions[0]
    assert c.excluded_days == 7 and f.day_hr_median is None
    outcomes = (c.sleep_minutes, c.deep_minutes, c.efficiency_pct, c.awake_minutes, c.sleep_hr_avg)
    assert {(o.true_nights, o.false_nights, o.diff, o.enough_data) for o in outcomes} == {(0, 0, None, False)}


def test_group_difference_is_none_when_one_side_is_empty():
    period = DateRange(TODAY - days_before(3), TODAY)
    nights = [night(d) for d in back(4)]
    days = [day(d) for d in back(4, TODAY - days_before(1))]
    c = factors(data(nights, days), period, "exercise").conditions[0]
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.diff, c.sleep_minutes.enough_data) == (0, None, False)


def test_good_bad_nights_values():
    period = DateRange(TODAY - days_before(11), TODAY)  # 12일 → 그룹마다 4일
    sleeps = [300, 310, 320, 330, 400, 400, 400, 400, 500, 510, 520, 530]
    ds = sorted(back(12))
    nights = [night(d, "23:00" if s >= 500 else "01:00", 10, sleep=s) for d, s in zip(ds, sleeps, strict=True)]
    nights.append(night(ds[0] - days_before(1), "23:00", 10, sleep=600))
    days = [day(d - days_before(1), steps=8000 if s >= 500 else 4000) for d, s in zip(ds, sleeps, strict=True)]
    days[-1] = day(ds[-1] - days_before(1), steps=8000, ends=(at(ds[-1] - days_before(1), "21:00"),))
    gb = factors(data(nights, days), period).good_bad_nights
    assert gb.good == NightGroup(
        nights=4,
        sleep_range=(500, 530),
        exercise_day_pct=25,
        late_exercise_pct=25,
        steps_avg=8000,
        prev_sleep_avg=(400 + 500 + 510 + 520) / 4,
        bedtime_avg=23 * 60,
        weekend_pct=0,  # 10-19~10-22(월~목)에 깸
        enough_data=True,
    )
    assert (gb.bad.nights, gb.bad.sleep_range, gb.bad.prev_sleep_avg, gb.bad.bedtime_avg) == (
        4,
        (300, 330),
        (600 + 300 + 310 + 320) / 4,
        60,
    )
    # 못 잔 밤은 10-11(일)~10-14(수)에 깸
    assert (gb.bad.exercise_day_pct, gb.bad.steps_avg, gb.bad.weekend_pct, gb.bad.enough_data) == (0, 4000, 25, True)


def test_good_bad_nights_need_twelve_nights():
    gb = factors(data(night(d, sleep=400 + i) for i, d in enumerate(back(11)))).good_bad_nights
    assert (gb.good.nights, gb.good.enough_data, gb.bad.enough_data) == (3, False, False)


def test_single_condition_has_no_good_bad_nights():
    f = factors(data(night(d) for d in back(5)), condition="weekend")
    assert [c.condition for c in f.conditions] == ["weekend"] and f.good_bad_nights is None


# 11.5 signals


def test_spo2_times_take_earliest_five_and_count_the_rest():
    ds = sorted(back(4))
    nights = [night(d, low=2, low_times=(at(d, "03:05"), at(d, "03:00"))) for d in ds]
    s = signals(data(nights)).spo2_low_repeat
    assert (s.hit, s.low_nights, s.low_records, s.more, s.recorded_nights) == (True, 4, 8, 3, 4)
    assert s.times == tuple(sorted(t for n in nights for t in n.spo2_low_times))[:5]
    assert s.low_dates == tuple(ds)
    # 보여 주는 시각마다 그 시각이 기록된 밤의 잠든 시각이 같은 순서로 붙는다
    starts = {n.date: n.start_at for n in nights}
    assert s.slept_at == tuple(starts[t.date()] for t in s.times)


def test_spo2_times_are_none_when_a_low_night_has_no_times():
    ds = back(3)
    nights = [night(ds[0], low=1, low_times=(at(ds[0], "03:00"),)), night(ds[1], low=1), night(ds[2], low=0)]
    s = signals(data(nights)).spo2_low_repeat
    assert (s.hit, s.low_nights, s.low_records, s.times, s.more, s.recorded_nights) == (False, 2, 2, None, None, 3)
    assert s.slept_at is None


def test_spo2_slept_at_follows_the_night_of_each_time():
    # 10-21 01:00 에 잠든 밤의 08:30 기록과 10-21 23:30 에 잠든 밤의 02:00 기록은 깬 날짜가 다르다
    first = night(TODAY - days_before(1), "01:00", 8, low=1, low_times=(at(TODAY - days_before(1), "08:30"),))
    second = night(TODAY, "23:30", 7, low=1, low_times=(at(TODAY, "02:00"),))
    s = signals(data([first, second])).spo2_low_repeat
    assert s.slept_at == (first.start_at, second.start_at)


@pytest.mark.parametrize("recent_hr, hit", [(66.0, False), (66.1, True)])
def test_sleep_hr_high_is_more_than_five_bpm_over_baseline(recent_hr, hit):
    end = TODAY
    recent = [night(d, hr=recent_hr) for d in back(7, end)]
    base = [night(d, hr=61.0) for d in back(28, end - days_before(7))]
    s = signals(data(recent + base))
    h = s.sleep_hr_high
    assert (h.hit, h.recent_nights, h.baseline_nights) == (hit, 7, 28)
    assert h.diff == pytest.approx(recent_hr - 61)
    assert s.baseline_period == DateRange(end - days_before(34), end - days_before(7))


# 11.6 어젯밤 비교


def test_last_night_compares_with_seven_days_before_it():
    week = [(420, 60.0), (400, 61.0), (440, 62.0), (430, 60.0), (410, 61.0), (420, 60.0), (450, 62.0)]  # 10-15~10-21
    nights = [night(TODAY, sleep=300, hr=64.0)]
    nights += [night(TODAY - days_before(7 - i), sleep=s, hr=h) for i, (s, h) in enumerate(week)]
    nights.append(night(TODAY - days_before(8), sleep=100, hr=90.0))  # 7일 밖
    v = last_night_vs_week(data(nights), TODAY)
    # 평균은 어젯밤을 빼고 낸다. 어젯밤을 넣으면 (2970 + 300) / 8 = 408.75분이다. 값은 반올림하지 않는다
    m = v.sleep_minutes
    assert (m.last_night, m.week_avg, m.diff, m.week_nights) == (
        300,
        pytest.approx(2970 / 7),
        pytest.approx(300 - 2970 / 7),
        7,
    )
    h = v.sleep_hr_avg
    assert (h.last_night, h.week_avg, h.diff, h.week_nights) == (
        64,
        pytest.approx(426 / 7),
        pytest.approx(64 - 426 / 7),
        7,
    )


def test_last_night_needs_four_nights_in_the_week_before():
    nights = [night(TODAY, sleep=300), *(night(TODAY - days_before(k), sleep=420) for k in (1, 3, 5))]
    v = last_night_vs_week(data(nights), TODAY)
    assert v.sleep_minutes == LastNightValue(last_night=300, week_avg=None, diff=None, week_nights=3)
    v = last_night_vs_week(data([*nights, night(TODAY - days_before(7), sleep=400)]), TODAY)
    assert v.sleep_minutes == LastNightValue(last_night=300, week_avg=415, diff=-115, week_nights=4)


def test_last_night_without_main_sleep_has_no_difference():
    nights = [night(TODAY - days_before(k), sleep=420, hr=60.0) for k in range(1, 8)]
    nights.append(night(TODAY, "14:00", 1, main=False))  # 주 수면이 아닌 낮잠은 어젯밤이 아니다
    v = last_night_vs_week(data(nights), TODAY)
    assert v.sleep_minutes == LastNightValue(last_night=None, week_avg=420, diff=None, week_nights=7)
    assert v.sleep_hr_avg == LastNightValue(last_night=None, week_avg=60, diff=None, week_nights=7)


def test_last_night_heart_rate_counts_nights_with_heart_rate():
    nights = [night(TODAY, sleep=300, hr=None)]
    nights += [night(TODAY - days_before(k), sleep=420, hr=60.0 if k <= 3 else None) for k in range(1, 8)]
    v = last_night_vs_week(data(nights), TODAY)
    assert v.sleep_minutes.week_nights == 7
    assert v.sleep_hr_avg == LastNightValue(last_night=None, week_avg=None, diff=None, week_nights=3)


def test_last_night_follows_base_date():
    base = TODAY - days_before(3)
    nights = [night(d, sleep=600) for d in back(3)]  # 기준일 뒤의 밤은 쓰지 않는다
    nights += [night(base, sleep=360), *(night(base - days_before(k), sleep=420) for k in range(1, 8))]
    v = last_night_vs_week(data(nights), base)
    assert v.sleep_minutes == LastNightValue(last_night=360, week_avg=420, diff=-60, week_nights=7)
    with pytest.raises(ValueError):
        last_night_vs_week(data(nights), TODAY + dt.timedelta(days=1))


# 포트 결과의 날짜별 값을 낮 기록으로 (9.1 착용 값)


def result_day(d: dt.date, value: float | None, reason=None, *, hours=None, worn=None) -> ResultDay:
    return ResultDay(
        date=d,
        value=value,
        missing=reason is not None,
        reason=reason,
        is_in_progress=False,
        wear_hours=hours,
        is_well_worn=worn,
    )


def test_day_records_read_activity_row_and_steps_from_missing_reason():
    ds = sorted(back(5, TODAY - days_before(1)))
    steps = [
        result_day(ds[0], 6000.0),
        result_day(ds[1], None, "NULL_VALUE"),  # 행은 있으나 걸음 값 없음
        result_day(ds[2], 0.0, "ZERO_STEPS"),  # 0보는 행이 있는 걸음 결측
        result_day(ds[3], None, "NO_RECORD"),  # 활동 행 없음
        result_day(ds[4], 0.0, "UNSYNCED"),  # 아직 안 들어온 날은 비교에서 빠지는 날이라 행이 없는 날로 둔다
    ]
    heart = [result_day(d, 70.5, hours=20, worn=True) for d in ds]
    ends = {ds[0]: (at(ds[0], "21:00"),)}
    days = day_records(steps, heart, ends)
    assert [(d.has_activity_row, d.steps, d.steps_missing) for d in days] == [
        (True, 6000, False),
        (True, None, True),
        (True, 0, True),
        (False, None, True),
        (False, None, True),
    ]
    assert [d.day_hr_avg for d in days] == [70.5] * 5
    assert [d.exercise_ends for d in days] == [(at(ds[0], "21:00"),), (), (), (), ()]


@pytest.mark.parametrize("worn", [None, False], ids=["unknown_wear", "short_wear"])
def test_day_records_keep_unknown_wear_and_high_day_hr_uses_only_well_worn_days(worn):
    period = DateRange(TODAY - days_before(6), TODAY)
    nights = [night(d) for d in back(7)]
    ds = sorted(back(7, TODAY - days_before(1)))
    hrs = [(60, True), (60, True), (60, True), (65, True), (90, worn), (90, worn), (90, worn)]
    hours = {True: 20, False: 6, None: None}
    steps = [result_day(d, 5000.0, hours=hours[w], worn=w) for d, (_, w) in zip(ds, hrs, strict=True)]
    heart = [result_day(d, float(h), hours=hours[w], worn=w) for d, (h, w) in zip(ds, hrs, strict=True)]
    days = day_records(steps, heart)
    # null 은 모름(None)으로 두고 충분히 착용한 날(True)이나 착용이 짧은 날(False)로 바꾸지 않는다
    assert [d.well_worn for d in days] == [w for _, w in hrs]
    c = factors(data(nights, days), period, "high_day_hr").conditions[0]
    # 충분히 착용한 4일의 중앙값 60 → 65 만 5bpm 이상 높다. 90bpm 인 날은 중앙값과 그룹에서 빠진다
    assert (c.sleep_minutes.true_nights, c.sleep_minutes.false_nights, c.excluded_days) == (1, 3, 3)


def test_day_records_need_the_same_dates():
    with pytest.raises(ValueError):
        day_records([result_day(TODAY, 100.0)], [result_day(TODAY - days_before(1), 60.0)])


async def test_synth11_days_from_memory_port_match_fixture_days():
    fixture = load_fixture("synth-11")
    inp = synth11_input()
    port = fixture.port()
    steps = await port.get_metric_result(fixture.user_id, "steps", inp.linked_from, inp.today)
    heart = await port.get_metric_result(fixture.user_id, "heart_rate", inp.linked_from, inp.today)
    days = day_records(steps.days, heart.days, {d.date: d.exercise_ends for d in inp.days})
    # 조건 날짜가 될 수 있는 날(동기화 기준일 전)은 고정 데이터를 바로 읽은 낮 기록과 같다.
    # 착용 값은 9.1 의 5·6번 줄이다
    synced_day = inp.synced_until.astimezone(KST).date()
    assert [d for d in days if d.date < synced_day] == [d for d in inp.days if d.date < synced_day]
    assert factors(dataclasses.replace(inp, days=days)) == factors(inp)


def test_input_rejects_naive_time_and_inconsistent_values():
    good = night(TODAY)
    with pytest.raises(ValueError):
        dataclasses.replace(good, start_at=good.start_at.replace(tzinfo=None))
    with pytest.raises(ValueError):
        dataclasses.replace(good, date=TODAY - days_before(1))
    with pytest.raises(ValueError):
        dataclasses.replace(good, spo2_low_count=2, spo2_low_times=(good.start_at,))
    with pytest.raises(ValueError):
        data([good, night(TODAY, "22:00")])
    with pytest.raises(ValueError):
        DayRecord(TODAY, has_activity_row=False, steps=10, steps_missing=False, day_hr_avg=None, well_worn=None)


# 참고 구현과 비교 (골든셋 고정 데이터 synth-11)


def synth11_input() -> SleepCoachingInput:
    """고정 데이터를 계산 입력으로 바꾼다. 포트가 전달할 값과 같은 뜻으로 맞춘다."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    synced = max(dt.datetime.fromisoformat(s["last_data_at"]) for s in raw["sync"].values() if s["last_data_at"])
    synced_day = synced.astimezone(KST).date()
    nights = []
    for r in raw["sleep"]:
        start, end = dt.datetime.fromisoformat(r["start_at"]), dt.datetime.fromisoformat(r["end_at"])
        times = r.get("spo2_low_times")
        nights.append(
            NightRecord(
                date=dt.date.fromisoformat(r["date"]),
                start_at=start,
                end_at=end,
                # sleep_session.session_minutes 와 같은 계산 (파이프라인 rules.minutes_between)
                session_minutes=round((end - start).total_seconds() / 60),
                sleep_minutes=r["sleep_minutes"],
                awake_minutes=r["awake_minutes"],
                deep_minutes=r["deep_minutes"],
                sleep_hr_avg=r["avg_heart_rate"],
                spo2_low_count=r["spo2_low_count"],
                spo2_low_times=None if times is None else tuple(dt.datetime.fromisoformat(t) for t in times),
                is_main=r["is_main"],
                spo2_avg=r["spo2_avg"],
            )
        )
    # 고정 데이터의 steps 는 activity_daily 행 전체다
    steps = {dt.date.fromisoformat(r["date"]): r["steps"] for r in raw["steps"]}
    heart = {dt.date.fromisoformat(r["date"]): r for r in raw["heart"]}
    ends: dict[dt.date, list[dt.datetime]] = {}
    for r in raw["exercise"]:
        if r["counts_as_exercise"]:
            ends.setdefault(dt.date.fromisoformat(r["date"]), []).append(dt.datetime.fromisoformat(r["end_at"]))
    days = []
    for d in sorted(steps.keys() | heart.keys()):
        h = heart.get(d)
        hours = h["hours"] if h else 0
        # 9.1: 완성된 날은 10시간 이상이면 True, 아니면 False 다. 동기화 기준일부터는 10시간 미만을 판단하지 않는다
        worn = hours >= 10 if d < synced_day else (True if hours >= 10 else None)
        days.append(
            DayRecord(
                date=d,
                has_activity_row=d in steps,
                steps=steps.get(d),
                steps_missing=not steps.get(d),
                day_hr_avg=h["hr_avg"] if h and h["sample_count"] else None,
                well_worn=worn,
                exercise_ends=tuple(ends.get(d, ())),
            )
        )
    return SleepCoachingInput(
        today=dt.datetime.fromisoformat(raw["now"]).astimezone(KST).date(),
        linked_from=dt.date.fromisoformat(raw["linked_from"]),
        synced_until=synced,
        nights=tuple(nights),
        days=tuple(days),
    )


def shown(value: Any, clock: bool = False) -> Any:
    """참고 구현의 표시 자릿수로 바꾼다. 분·bpm·걸음·비율은 정수(0.5 올림), 시각은 HH:MM 이다."""
    if dataclasses.is_dataclass(value):
        clocks = {"avg"} if isinstance(value, ClockStat) else {"bedtime_avg"}
        return {f.name: shown(getattr(value, f.name), f.name in clocks) for f in dataclasses.fields(value)}
    if isinstance(value, tuple | list):
        return [shown(v) for v in value]
    if isinstance(value, float):
        return hhmm(value) if clock else half_up(value)
    if isinstance(value, dt.datetime):
        return value.astimezone(KST).isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list) and value:
        items = enumerate(value)
    else:
        return {path: value}
    out: dict[str, Any] = {}
    for key, v in items:
        out |= flatten(v, f"{path}.{key}" if path else str(key))
    return out


def synth11_comparison() -> tuple[dict[str, Any], dict[str, Any]]:
    """(계산 값, 참고 값)을 칸 경로별로 반환한다."""
    inp = synth11_input()
    calculated = {"pattern": shown(pattern(inp)), "factors": shown(factors(inp)), "signals": shown(signals(inp))}
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))["output"]
    return flatten(calculated), flatten(reference)


def test_synth11_reference_file_records_source():
    source = json.loads(REFERENCE.read_text(encoding="utf-8"))["source"]
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert source["command"] == "uv run python -m candiy_pipeline.sleep_reference --persona synth-11"
    assert source["fixture_data_sha256"] == fixture["data_sha256"]
    # 참고 구현을 실행한 커밋은 설명 없이 커밋 해시 하나로 적는다
    assert re.fullmatch(r"[0-9a-f]{40}", source["sleep_reference_commit"])


def test_synth11_matches_reference_in_every_field():
    calculated, reference = synth11_comparison()
    assert sorted(set(calculated) - set(reference)) == sorted(ONLY_CALCULATED)
    assert sorted(set(reference) - set(calculated)) == []
    assert [(p, calculated[p], reference[p]) for p in reference if calculated[p] != reference[p]] == []
    inp = synth11_input()
    assert sleep_vs_baseline(inp, inp.today) == pattern(inp).sleep_vs_baseline
    # 계산에만 있는 칸: 기본 기간이 같은 signals 의 값과 맞는지, 기준선 기간이 최근 7일 앞 28일인지
    assert calculated["pattern.spo2_low_nights.low_nights"] == reference["signals.spo2_low_repeat.low_nights"]
    assert calculated["pattern.spo2_low_nights.recorded_nights"] == reference["signals.spo2_low_repeat.recorded_nights"]
    assert (calculated["signals.baseline_period.start"], calculated["signals.baseline_period.end"]) == (
        "2026-09-18",
        "2026-10-15",
    )


@pytest.mark.parametrize("days, steps, day_hr", [(7, 3247, 73.9), (14, 3561, 73.5), (28, 4731, 73.35)])
def test_synth11_medians_are_the_split_values_of_reference(days, steps, day_hr):
    # 참고 구현(candiy_pipeline.sleep_reference factors)이 걸음 많은 날·낮 심박 높은 날을 가를 때 쓴 중앙값이다.
    # 참고 결과 파일에는 이 칸이 없어 참고 구현 안의 값을 적는다
    inp = synth11_input()
    f = factors(inp, DateRange(inp.today - days_before(days - 1), inp.today))
    assert (f.steps_median, f.day_hr_median) == pytest.approx((steps, day_hr))


@pytest.mark.parametrize("days", [7, 14, 28])
def test_synth11_spo2_avg_range_is_the_lowest_and_highest_nightly_average(days):
    # 참고 구현에는 밤별 산소포화도 평균이 없어, 고정 데이터의 주 수면 spo2_avg 에서 따로 구한 값과 비교한다
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    inp = synth11_input()
    start = inp.today - days_before(days - 1)
    rows = [
        r
        for r in raw["sleep"]
        if r["is_main"] and r["spo2_avg"] is not None and start <= dt.date.fromisoformat(r["date"]) <= inp.today
    ]
    low = min(rows, key=lambda r: (r["spo2_avg"], r["date"]))
    high = min(rows, key=lambda r: (-r["spo2_avg"], r["date"]))
    a = signals(inp, DateRange(start, inp.today)).spo2_avg_range
    assert a.lowest is not None and a.highest is not None
    assert a.nights == len(rows)
    assert (a.lowest.value, a.lowest.date.isoformat()) == (low["spo2_avg"], low["date"])
    assert (a.highest.value, a.highest.date.isoformat()) == (high["spo2_avg"], high["date"])
    if days == 14:
        # 기본 14일: 10월 16일 03:28 에 잠든 밤 84.4%, 10월 9일 00:09 에 잠든 밤 96.5%
        assert (a.lowest.value, a.highest.value, a.nights) == (84.4, 96.5, 14)


def test_spo2_avg_range_takes_the_earlier_night_on_ties_and_skips_missing_values():
    ds = sorted(back(4))
    nights = [night(ds[0], spo2=92.0), night(ds[1]), night(ds[2], spo2=92.0), night(ds[3], spo2=97.5)]
    a = signals(data(nights)).spo2_avg_range
    assert a.lowest is not None and a.highest is not None
    assert (a.lowest.value, a.lowest.date, a.highest.value, a.highest.date, a.nights) == (92.0, ds[0], 97.5, ds[3], 3)
    assert signals(data(night(d) for d in back(4))).spo2_avg_range == Spo2AvgRange(lowest=None, highest=None, nights=0)
