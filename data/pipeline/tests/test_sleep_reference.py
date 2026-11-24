"""수면 코칭 계산 참고 구현 (calculation-rules.md 11장·11.9, S15P31A403-305)."""

from datetime import date, datetime, timedelta

import pytest

from candiy_pipeline import sleep_reference as ref
from candiy_pipeline.records import KST

TODAY = date(2026, 10, 22)  # 목


def night(d: date, bed: str, hours: float, *, awake=10, deep=60, hr=60.0, low=0, low_times=None, sleep=None):
    """d에 깬 주 수면. bed는 잠든 시각(KST "HH:MM"), 18시 이후면 전날 밤이다."""
    h, m = map(int, bed.split(":"))
    start = datetime(d.year, d.month, d.day, h, m, tzinfo=KST) - (timedelta(days=1) if h >= 18 else timedelta())
    end = start + timedelta(hours=hours)
    return ref.Night(
        date=d,
        start=start,
        end=end,
        sleep=round(hours * 60) - awake if sleep is None else sleep,
        awake=awake,
        deep=deep,
        hr=hr,
        spo2_count=1,
        low=low,
        low_times=low_times,
    )


def data(nights, *, steps=None, heart=None, exercise=None, linked_from=date(2026, 1, 1)):
    nights = list(nights)
    days = steps if steps is not None else {n.date - timedelta(days=1): 5000 for n in nights}
    return ref.Data(
        today=TODAY,
        linked_from=linked_from,
        synced_day=TODAY,
        nights={n.date: n for n in nights},
        steps=days,
        heart=heart or {},
        exercise_ends=exercise or {},
    )


def days_back(n):
    return [TODAY - timedelta(days=i) for i in range(n)]


# ----- 고정 데이터 synth-11 (골든셋 정답 회귀) -----


def test_synth11_fixture_reference_values():
    d = ref.load(ref.FIXTURES / "synth-11.json")
    p = ref.pattern(d)
    assert (p["nights"], p["bedtime"]["avg"], p["waketime"]["avg"]) == (14, "01:37", "07:56")
    # 운동한 다음 밤 깊은 잠이 길던 평소와 견줘 최근 2주는 깊은 잠이 20% 줄었다 (S15P31A403-385)
    assert [x["type"] for x in p["problems"]] == ["short", "declining", "irregular", "low_deep"]
    # 야근이 최근 2주라 본인 기준 28일은 평소 구간이다: 줄어드는 중에 해당 (Q1 시연)
    assert p["sleep_vs_baseline"] == {
        "recent_avg": 356,
        "baseline_avg": 423,
        "diff": -67,
        "recent_nights": 14,
        "baseline_nights": 28,
        "recent_reliable": True,
        "baseline_reliable": True,
    }
    f = {c["condition"]: c for c in ref.factors(d)["conditions"]}
    weekend = f["weekend"]["sleep_minutes"]
    assert (weekend["true_avg"], weekend["true_nights"], weekend["diff"]) == (471, 8, 107)
    # 평소 구간의 저녁 헬스·오전 등산은 늦은 운동이 아니다 (운동한 날 8일, 늦은 운동 0일)
    exercise = f["exercise"]["sleep_minutes"]
    assert (exercise["true_nights"], exercise["false_nights"], exercise["enough_data"]) == (8, 20, True)
    assert f["late_exercise"]["sleep_minutes"]["true_nights"] == 0
    s = ref.signals(d)
    assert s["spo2_low_repeat"]["hit"] and s["spo2_low_repeat"]["low_nights"] == 7
    # 운동한 다음 밤 수면 중 심박이 낮던 평소보다 최근 7일이 5bpm 넘게 높다 (S15P31A403-385)
    assert s["sleep_hr_high"]["hit"] and not s["short_sleep_streak"]["hit"]
    last = ref.last_night_vs_week(d)["sleep_minutes"]
    assert (last["last_night"], last["week_avg"], last["diff"], last["week_nights"]) == (308, 350, -42, 7)


# ----- 11.9 계산 세부 -----


def test_show_rounds_half_up_not_to_even():
    # 분·bpm·걸음·비율은 정수로 표시 (11.9). Python round는 482.5를 482로 보낸다
    assert ref.show(482.5) == 483 and round(482.5) == 482
    assert ref.show(-45.5) == -46 and isinstance(ref.show(30.4), int)
    assert ref.show(-45.55, "0.1") == -45.6


@pytest.mark.parametrize("recent_deep, hit", [(16, True), (17, False)])
def test_low_deep_is_relative_change_not_points(recent_deep, hit):
    # 기준 20% → 최근 16%는 상대 변화 −20 (해당), 17%는 −15 (해당 없음). %포인트로 보면 둘 다 −4·−3이라 못 가린다
    base = [night(TODAY - timedelta(days=i), "23:00", 9, awake=0, deep=100, sleep=500) for i in range(14, 42)]
    recent = [night(d, "23:00", 9, awake=0, deep=5 * recent_deep, sleep=500) for d in days_back(14)]
    p = ref.pattern(data(base + recent))
    assert p["deep_ratio_vs_baseline"]["change_pct"] == recent_deep * 5 - 100
    assert ("low_deep" in [x["type"] for x in p["problems"]]) is hit


@pytest.mark.parametrize("awake_total, hit", [(420, False), (427, True)])  # 평균 30.0분 / 30.5분 (14밤)
def test_fragmented_judged_before_rounding(awake_total, hit):
    awakes = [30] * 14
    awakes[0] += awake_total - 420
    d = data(night(day, "23:30", 7.5, awake=a) for day, a in zip(days_back(14), awakes, strict=True))
    types = [x["type"] for x in ref.pattern(d)["problems"]]
    assert ("fragmented" in types) is hit


def test_clock_mean_crosses_midnight():
    assert ref.clock(ref.statistics.mean(ref.unwrap([23 * 60 + 50, 40]))) == "00:15"


def test_unwrap_aligns_digits_before_cut():
    # 자르는 기준과 값의 자릿수가 다르면 경계 값이 하루 뒤로 넘어간다 (참고 계산에서 흔들림 360분이 나왔던 버그)
    assert ref.unwrap([419.99999, 420.0, 500.0]) == [420.0, 420.0, 500.0]


def test_weekday_weekend_gap_unwraps_all_nights_together():
    # 평일 23:30, 주말 00:30에 잠듦, 길이 같음 → 주말이 60분 늦다 (따로 처리하면 −1380분이 나올 수 있다)
    nights = [night(d, "00:30" if d.weekday() >= 5 else "23:30", 7) for d in days_back(14)]
    assert ref.pattern(data(nights))["midpoint_gap_min"]["min"] == 60.0


def test_ratio_average_is_mean_of_nightly_ratios():
    nights = [night(days_back(4)[0], "23:00", 5, deep=60, awake=0, sleep=300)]  # 20%
    nights += [night(d, "23:00", 10, deep=60, awake=0, sleep=600) for d in days_back(4)[1:]]  # 10%
    assert ref.pattern(data(nights))["deep_ratio_avg_pct"]["value"] == 13  # 12.5 (합끼리면 240/2100 ≈ 11.43 → 11)


def test_terciles_sort_each_way_and_ties_take_earlier_night():
    sleeps = [300, 300, 300, 450, 450, 450]  # k = 2
    ds = sorted(days_back(6))
    nights = [night(d, "23:00", 8, sleep=s) for d, s in zip(ds, sleeps, strict=True)]
    gb = ref.good_bad(data(nights), nights)
    assert gb["good"]["sleep_range"] == [450, 450] and gb["bad"]["sleep_range"] == [300, 300]
    tie = [night(d, "23:00", 8, sleep=400) for d in ds[:3]]  # k = 1, 모두 같은 값 → 둘 다 먼저 깬 밤
    gb = ref.good_bad(data(tie), tie)
    assert gb["good"]["prev_sleep_avg"] is None and gb["bad"]["prev_sleep_avg"] is None  # 먼저 깬 밤의 전날 기록 없음


def test_median_of_even_count_is_mean_of_middle_two():
    nights = [night(d, "23:00", 7) for d in days_back(5)]
    ds = sorted(n.date - timedelta(days=1) for n in nights)[1:]  # D 4일
    steps = dict(zip(ds, [1000, 2000, 3000, 4000], strict=True))
    f = ref.factors(data(nights, steps=steps), days=5, condition="high_steps")["conditions"][0]
    # 중앙값 2500 → 3000·4000만 많은 날
    assert (f["sleep_minutes"]["true_nights"], f["sleep_minutes"]["false_nights"]) == (2, 2)


def test_short_sleep_streak_breaks_on_missing_day():
    ds = sorted(days_back(5))
    nights = [night(d, "01:00", 4.5) for i, d in enumerate(ds) if i != 2]  # 가운데 하루 기록 없음
    s = ref.signals(data(nights), days=5)["short_sleep_streak"]
    assert (s["hit"], s["longest"]) == (False, 2)


def test_spo2_times_first_five_and_rest():
    t = datetime(2026, 10, 20, 3, 0, tzinfo=KST)
    nights = [
        night(d, "23:00", 7, low=2, low_times=(t + timedelta(days=i), t + timedelta(days=i, minutes=5)))
        for i, d in enumerate(sorted(days_back(4)))
    ]
    s = ref.signals(data(nights), days=4)["spo2_low_repeat"]
    assert (s["low_nights"], s["low_records"], len(s["times"]), s["more"]) == (4, 8, 5, 3)
    assert s["times"] == sorted(s["times"])


def test_spo2_times_null_when_fixture_has_no_times():
    nights = [night(d, "23:00", 7, low=1) for d in days_back(4)]
    s = ref.signals(data(nights), days=4)["spo2_low_repeat"]
    assert s["hit"] and s["times"] is None and s["more"] is None


def test_too_few_nights_judges_nothing():
    p = ref.pattern(data(night(d, "23:00", 5) for d in days_back(3)))
    assert not p["enough_data"] and p["problems"] == [] and p["sleep_avg_min"]["value"] is None


# ----- 11.9 추가 (11-09, 계산 모듈과 대조하며 정한 것) -----


def test_long_sleep_is_nine_hours_or_more():
    p = ref.pattern(data(night(d, "22:00", 9.5, awake=10) for d in days_back(14)))
    assert [x["type"] for x in p["problems"]] == ["long"]  # 560분 ≥ 540분


def test_four_nights_counted_by_nights_with_values():
    # 밤은 5밤이지만 수면 시간 값이 있는 밤이 3밤이면 평균 수면은 내지 않는다
    nights = [night(d, "23:00", 5, sleep=280) for d in days_back(5)]
    nights = [ref.Night(**{**n.__dict__, "sleep": None}) if i < 2 else n for i, n in enumerate(nights)]
    p = ref.pattern(data(nights))
    assert p["enough_data"] and p["sleep_avg_min"] == {"value": None, "nights": 3}
    assert {"type": "short", "reason": "nights_below_4", "nights": 3} in p["not_judged"]
    # 본인 기준 비교 평균은 4.1처럼 밤 수가 적어도 낸다
    assert p["sleep_vs_baseline"]["recent_avg"] == 280


def test_factors_skip_sync_day_and_count_every_skipped_day():
    nights = [night(d, "23:00", 7) for d in days_back(28)]
    d = data(nights)
    d = ref.Data(**{**d.__dict__, "synced_day": TODAY - timedelta(days=2)})
    c = ref.factors(d, condition="weekend")["conditions"][0]
    # D = 어제·그제 중 동기화 기준일(그제)과 같거나 이후인 날(어제·그제)은 뺀다. 활동 행 없는 날도 센다
    assert c["excluded_days"] == 2
    steps = {k: v for k, v in d.steps.items() if k != TODAY - timedelta(days=5)}
    c = ref.factors(ref.Data(**{**d.__dict__, "steps": steps}), condition="weekend")["conditions"][0]
    assert c["excluded_days"] == 3


def test_zero_steps_is_missing():
    nights = [night(d, "23:00", 7 if i % 3 else 8) for i, d in enumerate(days_back(28))]
    steps = {n.date - timedelta(days=1): (0 if i % 3 == 0 else 5000) for i, n in enumerate(nights)}
    f = ref.factors(data(nights, steps=steps))
    high = next(c for c in f["conditions"] if c["condition"] == "high_steps")
    assert high["sleep_minutes"]["true_nights"] + high["sleep_minutes"]["false_nights"] < 28  # 0보인 날은 판단 불가
    assert f["good_bad_nights"]["good"]["steps_avg"] in (None, 5000)  # 0보는 평균에 넣지 않는다


def test_last_night_week_average_excludes_last_night():
    nights = [night(TODAY, "02:00", 4)] + [night(d, "23:00", 7) for d in days_back(8)[1:]]
    out = ref.last_night_vs_week(data(nights))["sleep_minutes"]
    assert (out["last_night"], out["week_avg"], out["week_nights"]) == (230, 410, 7)
