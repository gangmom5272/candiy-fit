"""수면 코칭 결과 문장 (calculation-rules.md 11.7, 계산 세부 11.9)."""

import json
import re
from dataclasses import replace

import pytest

from coach_ai.tools.periods import DateRange
from coach_ai.tools.sleep_coaching import (
    LastNightValue,
    LastNightVsWeek,
    SleepVsBaseline,
    factors,
    last_night_vs_week,
    pattern,
    short_factors,
    signals,
    sleep_vs_baseline,
)
from coach_ai.tools.sleep_coaching_text import (
    SHORT_FACTORS_NOTE,
    SPO2_DEVICE_FACT,
    declining_line,
    exercise_comparison_text,
    extended_factors_lead,
    factors_text,
    last_night_line,
    pattern_text,
    short_factors_text,
    signals_text,
)
from tests.test_sleep_coaching import REFERENCE, TODAY, at, back, data, day, days_before, night, synth11_input

# synth-11 (골든셋 고정 데이터, 오늘 2026-10-22) 의 결과 문장
SYNTH11_PATTERN = (
    "최근 14일(10월 9일-22일) 수면은 짧고, 평소보다 줄어드는 중이고, 수면 시간대가 불규칙하고, 깊은 잠이 평소보다 "
    "적다. 평균 수면 시간은 5시간 56분(356분, 14일)으로 7시간 미만이라 짧다. 최근 14일(10월 9일-22일) 평균 수면은 "
    "5시간 56분(356분, 14일)으로 평소인 그 전 28일(9월 11일-10월 8일) 평균 7시간 3분(423분, 28일)보다 1시간 "
    "7분(67분) 짧다. 평소보다 60분 이상 짧아 수면이 줄어드는 중이다. 잠든 시각은 평균 01:37이고 날마다 보통 1시간 "
    "56분(116분) 안팎으로 달라진다(14일). 주말 자는 시간대(잠든 시각과 깬 시각의 가운데)가 평일보다 1시간 "
    "59분(119분) 늦다(평일 10일, 주말 4일). 잠든 시각이 날마다 보통 60분 이상 달라지거나 주말·평일 자는 시간대가 "
    "60분 이상 차이 나서 수면 시간대가 불규칙하다. 깬 시각은 평균 07:56이고 날마다 보통 2시간 6분(126분) 안팎으로 "
    "달라진다(14일). 주말 깬 시각이 평일보다 3시간 40분(220분) 늦다(평일 10일, 주말 4일). 자다가 깬 시간은 평균 "
    "24분(14일)이고, 수면 효율은 평균 94%(14일)이다. 자다가 깬 시간이 평균 30분 이하이고 수면 효율이 85% 이상이라 "
    "자다가 자주 깨지는 않았다. 깊은 잠 비율은 평균 13%(14일)로 평소(9월 11일-10월 8일) 17%(28일)보다 20% 낮다. "
    "평소보다 20% 이상 낮아 깊은 잠이 적다. 산소포화도를 기록한 14일 중 90% 미만으로 기록된 날은 7일이다."
)
SYNTH11_SIGNALS = (
    "최근 14일(10월 9일-22일) 수면에서 산소포화도를 기록한 14일 중 90% 미만으로 기록된 날은 7일, 기록은 8회(깬 날 "
    "기준 10월 13일 수면 새벽 3시 20분, 10월 16일 수면 새벽 4시 43분, 10월 17일 수면 새벽 3시 12분, 10월 18일 수면 "
    "새벽 4시 42분, 10월 19일 수면 새벽 3시 46분 외 3회)다. 90% 미만이 3일 이상 되풀이됐다. 잠마다의 평균 "
    "산소포화도(14일)는 가장 낮은 날 84.4%(10월 16일 수면), 가장 높은 날 96.5%(10월 9일 수면)다. 워치 측정은 "
    "의료기기 측정이 아니다. 최근 7일(10월 16일-22일) 수면 중 "
    "심박은 평균 "
    "65bpm(7일)으로 평소(9월 18일-10월 15일) 평균 60bpm(28일)보다 5bpm 높다. 평소보다 5bpm 넘게 높아 수면 중 심박이 "
    "계속 높은 상태다. 5시간 미만으로 잔 날이 3일 연달아 이어지지는 않았다(가장 길게 이어진 것은 1일, 10월 16일 "
    "수면)."
)
SYNTH11_FACTORS = (
    "아래 비교는 최근 28일(9월 25일-10월 22일) 수면을 잠들기 전 낮에 있었던 일로 나눠 본 것이고, 원인이 아니라 함께 "
    "나타난 차이다. 운동한 날(운동 기록이 있는 날, 느린 걷기 제외) 8일 평균: 수면 시간 7시간 18분(438분), 깊은 잠 "
    "1시간 23분(83분), 수면 효율 96%, 자다가 깬 시간 18분, 수면 중 심박 58bpm / 운동하지 않은 날(운동 기록이 없는 날) "
    "20일 평균: 수면 시간 6시간 16분(376분), 깊은 잠 51분, 수면 효율 95%, 자다가 깬 시간 20분, 수면 중 심박 62bpm. "
    "운동한 날이 운동하지 않은 날보다 수면 시간이 1시간 1분(61분) 길고, 깊은 잠이 33분 길고, 수면 효율이 1%포인트 "
    "높고, 자다가 깬 시간이 2분 짧고, 수면 중 심박이 5bpm 낮았다. 4,731보 이상 걸은 날 다음 잠은 수면 시간이 평균 "
    "7시간 "
    "2분(422분), 그보다 적게 걸은 날 다음 잠은 6시간 6분(366분)으로 56분 길고, 깊은 잠이 25분 길고, 수면 효율이 "
    "2%포인트 "
    "높고, 자다가 깬 시간이 7분 짧고, 수면 중 심박이 5bpm 낮았다(14일 대 14일). 앞선 수면이 기간 평균보다 60분 이상 "
    "짧았던 날 다음 잠은 수면 시간이 평균 6시간 7분(367분), 그렇지 않은 날 다음 잠은 6시간 41분(401분)으로 34분 "
    "짧고, 깊은 잠이 "
    "19분 짧고, 수면 효율이 1%포인트 낮고, 자다가 깬 시간이 1분 길고, 수면 중 심박이 4bpm 높았다(6일 대 22일). 토·일에 "
    "깬 잠은 수면 시간이 평균 7시간 51분(471분), 평일에 깬 잠은 6시간 3분(363분)으로 1시간 47분(107분) 길고, 깊은 잠이 "
    "9분 길고, 수면 효율은 차이가 없고, 자다가 깬 시간이 9분 길고, 수면 중 심박은 차이가 없었다(8일 대 20일). 한쪽이 "
    "4일보다 적어 비교하지 않은 것: 늦게 운동한 날(운동을 끝내고 3시간이 지나기 전에 잠든 날) 0일 대 일찍 운동한 "
    "날(운동을 끝내고 3시간 이상 지나 잠든 날) 8일, 이틀 연달아 늦게 잔 날(1일 대 20일), 낮 심박이 이 기간 보통보다 "
    "5bpm 이상 높았던 날(78.4bpm 이상, 0일 대 28일). 가장 오래 잔 9일은 7시간 21분~9시간 26분(441~566분), 가장 짧게 잔 "
    "9일은 2시간 58분~6시간 13분(178~373분) 잤다. 가장 오래 잔 9일은 가장 짧게 잔 9일보다 전날 걸음 수가 평균 "
    "4,103걸음 많고, 앞선 수면이 평균 53분 길고, 그 잠의 잠든 시각이 평균 1시간 47분(107분) 빨랐다. 가장 오래 잔 "
    "9일 대 가장 짧게 잔 9일의 비율은 전날 운동한 날 56% 대 0%, 전날 늦게 운동한 날 0% 대 0%, 토·일에 깬 날 67% 대 "
    "0%다."
)
SYNTH11_DECLINING = (
    "최근 14일(10월 9일-22일) 평균 수면은 5시간 56분(356분, 14일)으로 평소인 그 전 28일(9월 11일-10월 8일) "
    "평균 7시간 3분(423분, 28일)보다 1시간 7분(67분) 짧다. 평소보다 60분 이상 짧아 수면이 줄어드는 중이다."
)
SYNTH11_LAST_NIGHT = (
    "어젯밤(10월 22일에 깬 수면)은 수면 5시간 8분(308분), 수면 중 심박 63bpm이다. "
    "그 전 7일(10월 15일-21일) 평균은 수면 5시간 50분(350분, 7일), 수면 중 심박 65bpm(7일)이다. "
    "어젯밤은 그 전 7일 평균보다 수면이 42분 짧고 수면 중 심박이 2bpm 낮다."
)
# 결과 문장에 쓰지 않는 말. 원인은 "원인이 아니라" 에서만 쓰고, "측정된"은 11.7 산소포화도 표현이 아니다
NOT_USED = ("때문", "측정된", "진단")
# 11장 용어와 판정 표시. 코드 이름과 문서에는 그대로 두고, 답에 그대로 옮겨질 수 있는 결과 문장에는 쓰지 않는다
TERMS = (
    "본인 기준",
    "기준선",
    "흔들림",
    "가운데 시각",
    "상대 변화",
    "인정 운동",
    "%p",
    "일렀",
    "이르",
    "해당",
    "판정",
    "주 수면",
    "충분히 착용",
    "앞선 두 밤",
    "잘 잔",
    "못 잔",
    "미만인 쪽",
    "씩 차이",
)


@pytest.fixture(scope="module")
def synth11():
    return synth11_input()


def test_synth11_pattern_text(synth11):
    assert pattern_text(pattern(synth11), TODAY) == SYNTH11_PATTERN


def test_synth11_signals_text(synth11):
    assert signals_text(signals(synth11), TODAY) == SYNTH11_SIGNALS


def test_synth11_factors_text(synth11):
    assert factors_text(factors(synth11), TODAY) == SYNTH11_FACTORS


def test_synth11_basic_lines(synth11):
    assert declining_line(sleep_vs_baseline(synth11, TODAY), TODAY) == SYNTH11_DECLINING
    assert last_night_line(last_night_vs_week(synth11, TODAY), TODAY) == SYNTH11_LAST_NIGHT


@pytest.mark.parametrize("text", [SYNTH11_PATTERN, SYNTH11_SIGNALS, SYNTH11_FACTORS, SYNTH11_DECLINING])
def test_texts_state_facts_without_cause_or_measured_wording(text):
    assert not [word for word in NOT_USED if word in text]
    assert "원인" not in text.replace("원인이 아니라 함께 나타난 차이다", "")


def edge_texts() -> list[str]:
    """synth-11 의 7·14·28일 결과, 14일보다 짧은 기간의 factors 결과, 덧붙이는 운동 조건 비교, 다시 계산한 첫 문장과
    기록이 모자란 경우의 결과 문장."""
    inp = synth11_input()
    texts = [last_night_line(last_night_vs_week(inp, TODAY), TODAY)]
    recent = DateRange(TODAY - days_before(27), TODAY)
    for days in (7, 14, 28):
        period = DateRange(TODAY - days_before(days - 1), TODAY)
        texts += [pattern_text(pattern(inp, period), TODAY), signals_text(signals(inp, period), TODAY)]
        texts.append(factors_text(factors(inp, period), TODAY))
        texts.append(extended_factors_lead(factors(inp, period), "최근 기간", recent, TODAY))
    texts.append(exercise_comparison_text(factors(inp, recent), TODAY))
    for days in (1, 7, 13):
        period = DateRange(TODAY - days_before(days - 1), TODAY)
        texts.append(short_factors_text(short_factors(inp, period), TODAY, "최근 기간"))
    texts.append(short_factors_text(short_factors(data([]), DateRange(TODAY - days_before(1), TODAY)), TODAY))
    few = [night(d, "02:00", 4, low=None, hr=None) for d in back(3)]
    texts += [pattern_text(pattern(data(few)), TODAY), signals_text(signals(data(few)), TODAY)]
    texts += [factors_text(factors(data(few)), TODAY), declining_line(sleep_vs_baseline(data(few), TODAY), TODAY)]
    texts.append(last_night_line(last_night_vs_week(data(few[1:]), TODAY), TODAY))
    return texts


def test_texts_use_plain_words_and_do_not_end_like_instructions():
    # 결과 문장의 낱말은 답에 그대로 옮겨질 수 있어, 11장 용어 대신 사용자에게 옮겨도 자연스러운 말로 쓴다.
    # "~않는다" 로 끝나는 문장은 지시처럼 읽힌다
    for text in edge_texts():
        assert not [term for term in TERMS if term in text], text
        assert not re.search(r"(?<!자다가 )깬 시간", text), text
        assert not re.search(r"않는다[.)]|않는다$", text), text


# 반올림·표시 (11.9)


def test_difference_is_rounded_once_from_unrounded_averages():
    # 최근 350.6분, 그 전 395.4분. 반올림한 평균끼리 빼면 351 − 395 = 44분이다
    recent = [night(d, "23:00", 7, sleep=s) for d, s in zip(back(5), [350, 350, 351, 351, 351], strict=True)]
    base_days = back(5, TODAY - days_before(14))
    base = [night(d, "23:00", 7, sleep=s) for d, s in zip(base_days, [395, 395, 395, 396, 396], strict=True)]
    line = declining_line(sleep_vs_baseline(data(recent + base), TODAY), TODAY)
    assert "평균 수면은 5시간 51분(351분, 5일)으로" in line and "평균 6시간 35분(395분, 5일)보다 45분 짧다." in line


def test_half_is_rounded_up_and_judgment_uses_unrounded_value():
    # 14일 깬 시간 평균 30.5분은 31분으로 쓰고 "30분 초과"에 해당한다 (11.9 예)
    awakes = [37] + [30] * 13
    nights = [night(d, "23:30", 7.5, awake=a, sleep=420) for d, a in zip(back(14), awakes, strict=True)]
    text = pattern_text(pattern(data(nights)), TODAY)
    assert "자다가 깬 시간은 평균 31분(14일)이고" in text
    assert "자다가 깬 시간이 평균 30분을 넘거나 수면 효율이 85%보다 낮아 자다가 자주 깬다." in text


def test_minutes_are_written_with_hours_from_sixty_minutes():
    nights = [night(d, "23:00", 8, sleep=s, awake=60) for d, s in zip(back(4), [420, 420, 420, 420], strict=True)]
    text = pattern_text(pattern(data(nights)), TODAY)
    assert "평균 수면 시간은 7시간(420분, 4일)으로" in text
    assert "자다가 깬 시간은 평균 1시간(60분, 4일)이고" in text


def test_head_has_night_count_only_when_some_days_have_no_record():
    base = [night(d, "23:00", 8, sleep=470) for d in back(28, TODAY - days_before(14))]
    full = [night(d, "23:00", 8, sleep=470) for d in back(14)]
    assert pattern_text(pattern(data(base + full)), TODAY).startswith("최근 14일(10월 9일-22일) 수면에서 ")
    missing = [n for i, n in enumerate(full) if i not in (3, 7)]
    assert pattern_text(pattern(data(base + missing)), TODAY).startswith(
        "최근 14일(10월 9일-22일, 기록 12일) 수면에서 "
    )


def test_head_of_period_not_ending_on_base_date_has_dates_only():
    nights = [night(d, "23:00", 8, sleep=470) for d in back(42)]
    week = DateRange(TODAY - days_before(10), TODAY - days_before(4))
    assert pattern_text(pattern(data(nights), week), TODAY).startswith("10월 12일-18일 수면에서 ")
    assert signals_text(signals(data(nights), week), TODAY).startswith("10월 12일-18일 수면에서 ")


def test_long_sleep_names_nine_hours_with_judgment():
    nights = [night(d, "22:00", 10, sleep=540) for d in back(14)]
    text = pattern_text(pattern(data(nights)), TODAY)
    assert text.startswith("최근 14일(10월 9일-22일) 수면은 길다.")
    # 짧음과 긴 수면은 함께 해당할 수 없어 긴 수면이면 짧다는 판정을 쓰지 않는다
    assert "평균 수면 시간은 9시간(540분, 14일)으로 9시간 이상이라 길다." in text
    assert "7시간 미만" not in text


def test_judgment_is_first_and_types_not_judged_have_reason_and_nights():
    # 기준 28일에 6일만 있어 줄어드는 중(절반 미만)과 깊은 잠 적음(7일 미만)을 판정하지 않는다
    base = [night(d, "23:00", 8, sleep=470) for d in back(6, TODAY - days_before(14))]
    recent = [night(d, "23:00", 8, sleep=470) for d in back(14)]
    text = pattern_text(pattern(data(base + recent)), TODAY)
    assert text.startswith(
        "최근 14일(10월 9일-22일) 수면에서 판단한 것 중에는 뚜렷한 문제가 없다. "
        "기록이 모자라 판단하지 않은 것: 평소보다 줄어드는 중인지, 깊은 잠이 평소보다 적은지."
    )
    assert (
        "평소보다 60분 이상 짧아 줄어드는 중인지는 기록이 모자라 판단하지 않았다"
        "(그 전 28일 기록 6일이 연동 뒤 날 수의 절반보다 적음)."
    ) in text
    assert ("깊은 잠 비율이 평소보다 20% 이상 낮은지는 평소 깊은 잠 기록이 6일로 7일보다 적어 판단하지 않았다.") in text


def test_no_clear_type_when_every_type_is_judged_and_none_hit():
    base = [night(d, "23:00", 8, sleep=470) for d in back(28, TODAY - days_before(14))]
    recent = [night(d, "23:00", 8, sleep=470) for d in back(14)]
    text = pattern_text(pattern(data(base + recent)), TODAY)
    assert text.startswith(
        "최근 14일(10월 9일-22일) 수면에서 뚜렷한 문제는 없다. "
        "평균 수면 시간은 7시간 50분(470분, 14일)으로 7시간 이상 9시간 미만이라 짧거나 길지는 않았다."
    )
    assert "평소보다 60분 이상 짧지 않아 줄어드는 중인 상태는 아니다." in text
    assert "기록이 모자라" not in text


def test_too_few_nights_are_not_judged_but_baseline_values_are_written():
    base = [night(d, "23:00", 8, sleep=470) for d in back(20, TODAY - days_before(14))]
    text = pattern_text(pattern(data([*base, *(night(d, "02:00", 4) for d in back(3))])), TODAY)
    assert text.startswith("최근 14일(10월 9일-22일, 기록 3일) 수면은 기록이 4일보다 적어 판단하지 않았다.")
    assert not [word for word in ("이라 짧다", "줄어드는 중", "깊은 잠이 적", "불규칙", "자주 깨") if word in text]
    # 평소와 비교한 값은 판단하지 않아도 쓴다
    assert (
        "최근 14일(10월 9일-22일) 평균 수면은 3시간 50분(230분, 3일)으로 평소인 그 전 28일(9월 11일-10월 8일) "
        "평균 7시간 50분(470분, 20일)보다 4시간(240분) 짧다."
    ) in text


def test_spo2_line_says_when_no_night_has_oxygen_records():
    nights = [night(d, low=None) for d in back(14)]
    text = pattern_text(pattern(data(nights)), TODAY)
    assert text.endswith("산소포화도를 기록한 날이 없다.")
    assert SPO2_DEVICE_FACT not in text


def test_signals_start_with_low_oxygen_nights_and_device_fact():
    ds = sorted(back(4))
    nights = [night(d, low=2, low_times=(at(d, "03:05"), at(d, "03:00"))) for d in ds]
    text = signals_text(signals(data(nights)), TODAY)
    assert text.startswith(
        "최근 14일(10월 9일-22일, 기록 4일) 수면에서 산소포화도를 기록한 4일 중 90% 미만으로 기록된 날은 4일, "
        "기록은 8회(깬 날 기준 10월 19일 수면 새벽 3시·3시 5분, 10월 20일 수면 새벽 3시·3시 5분, 10월 21일 수면 "
        "새벽 3시 "
        "외 3회)다. "
        "90% 미만이 3일 이상 되풀이됐다. 워치 측정은 의료기기 측정이 아니다."
    )


def test_signals_write_counts_only_when_some_times_are_missing():
    ds = back(3)
    nights = [night(ds[0], low=1, low_times=(at(ds[0], "03:00"),)), night(ds[1], low=1), night(ds[2], low=0)]
    text = signals_text(signals(data(nights)), TODAY)
    assert text.startswith(
        "최근 14일(10월 9일-22일, 기록 3일) 수면은 기록이 4일보다 적다. "
        "산소포화도를 기록한 3일 중 90% 미만으로 기록된 날은 2일, 기록은 2회다. "
        "90% 미만이 3일 이상 되풀이되지는 않았다."
    )


def test_signals_without_oxygen_records_have_no_device_fact():
    text = signals_text(signals(data(night(d, low=None) for d in back(5))), TODAY)
    assert "산소포화도를 기록한 날이 없다." in text
    assert SPO2_DEVICE_FACT not in text and "되풀이" not in text


def test_signals_short_streak_and_heart_rate_hit():
    recent = [night(d, "01:00", 4.5, hr=67.0) for d in back(7)]
    base = [night(d, hr=61.0) for d in back(28, TODAY - days_before(7))]
    text = signals_text(signals(data(recent + base)), TODAY)
    assert (
        "최근 7일(10월 16일-22일) 수면 중 심박은 평균 67bpm(7일)으로 평소(9월 18일-10월 15일) 평균 61bpm(28일)보다 "
        "6bpm 높다. 평소보다 5bpm 넘게 높아 수면 중 심박이 계속 높은 상태다."
    ) in text
    assert ("5시간 미만으로 잔 날이 3일 이상 연달아 이어졌다(가장 길게 이어진 것은 7일, 10월 16일-22일 수면).") in text


def test_condition_with_fewer_than_four_nights_has_only_name_and_nights():
    period = DateRange(TODAY - days_before(3), TODAY)
    nights = [night(d) for d in back(4)]
    days = [day(d) for d in back(4, TODAY - days_before(1))]
    text = factors_text(factors(data(nights, days), period, "exercise"), TODAY)
    assert text == (
        "아래 비교는 최근 4일(10월 19일-22일) 수면을 잠들기 전 낮에 있었던 일로 나눠 본 것이고, "
        "원인이 아니라 함께 나타난 차이다. 한쪽이 4일보다 적어 비교하지 않은 것: "
        "운동한 날(운동 기록이 있는 날, 느린 걷기 제외) 0일 대 운동하지 않은 날(운동 기록이 없는 날) 4일."
    )


def test_condition_outcomes_with_different_nights_get_their_own_counts():
    period = DateRange(TODAY - days_before(9), TODAY)
    ds = sorted(back(10))
    # 운동한 날 밤 5일 중 1일은 수면 중 심박이 없다. 무리의 밤 수와 다른 값에만 그 밤 수를 붙인다
    nights = [night(d, sleep=480 if i % 2 else 420, hr=None if i == 1 else 60.0) for i, d in enumerate(ds)]
    days = [day(d - days_before(1), ends=(at(d - days_before(1), "18:00"),) if i % 2 else ()) for i, d in enumerate(ds)]
    text = factors_text(factors(data(nights, days), period, "exercise"), TODAY)
    assert (
        "운동한 날(운동 기록이 있는 날, 느린 걷기 제외) 5일 평균: 수면 시간 8시간(480분), 깊은 잠 1시간(60분), "
        "수면 효율 100%, 자다가 깬 시간 10분, 수면 중 심박 60bpm(4일) / 운동하지 않은 날(운동 기록이 없는 날) 5일 "
        "평균: 수면 시간 7시간(420분), 깊은 잠 1시간(60분), 수면 효율 88%, 자다가 깬 시간 10분, 수면 중 심박 60bpm."
    ) in text
    assert text.endswith(
        "운동한 날이 운동하지 않은 날보다 수면 시간이 1시간(60분) 길고, 깊은 잠은 차이가 없고, 수면 효율이 13%포인트 "
        "높고, 자다가 깬 시간은 차이가 없고, 수면 중 심박은 차이가 없었다."
    )


def test_outcome_without_any_record_is_left_out_of_condition_sentence():
    period = DateRange(TODAY - days_before(9), TODAY)
    ds = sorted(back(10))
    nights = [night(d, sleep=480 if i % 2 else 420, hr=None) for i, d in enumerate(ds)]
    days = [day(d - days_before(1), ends=(at(d - days_before(1), "18:00"),) if i % 2 else ()) for i, d in enumerate(ds)]
    text = factors_text(factors(data(nights, days), period, "exercise"), TODAY)
    # 수면 중 심박 기록이 있는 날이 없으면 두 무리 값에도 "0일 대 0일"에도 쓰지 않는다
    assert "수면 중 심박" not in text
    assert "자다가 깬 시간 10분 / 운동하지 않은 날" in text and text.endswith("자다가 깬 시간은 차이가 없었다.")


def test_median_conditions_write_the_values_that_split_the_days():
    # 걸음 1000·2001 의 중앙값 1500.5 → 1,501보 이상인 날. 낮 심박 60·70 의 중앙값 65 + 5 → 70.0bpm 이상인 날
    period = DateRange(TODAY - days_before(7), TODAY)
    ds = sorted(back(8))
    nights = [night(d, sleep=480 if i % 2 else 420) for i, d in enumerate(ds)]
    days = [
        day(d - days_before(1), steps=2001 if i % 2 else 1000, hr=70.0 if i % 2 else 60.0) for i, d in enumerate(ds)
    ]
    f = factors(data(nights, days), period)
    assert (f.steps_median, f.day_hr_median) == (1500.5, 65.0)
    text = factors_text(f, TODAY)
    assert (
        "1,501보 이상 걸은 날 다음 잠은 수면 시간이 평균 8시간(480분), 그보다 적게 걸은 날 다음 잠은 7시간(420분)으로 "
        "1시간(60분) 길고"
    ) in text
    assert (
        "워치를 충분히 찬 날 중 낮 심박이 이 기간 보통보다 5bpm 이상 높았던 날(70.0bpm 이상) 다음 잠은 수면 시간이 "
        "평균 8시간(480분), 그렇지 않은 날 다음 잠은 7시간(420분)으로 1시간(60분) 길고"
    ) in text


@pytest.mark.parametrize("days, split", [(7, "78.9bpm"), (14, "78.5bpm"), (28, "78.4bpm")])
def test_day_heart_rate_split_is_written_to_one_decimal(synth11, days, split):
    # synth-11 중앙값 73.9·73.5·73.35 + 5bpm. 하루 평균 심박에 소수가 있어 기준은 소수 첫째 자리까지 0.5 올림한다
    f = factors(synth11, DateRange(TODAY - days_before(days - 1), TODAY))
    assert f"낮 심박이 이 기간 보통보다 5bpm 이상 높았던 날({split} 이상, " in factors_text(f, TODAY)


def test_exercise_meaning_is_written_only_where_it_is_first_written():
    # 열흘 모두 운동해 운동 기록 조건은 비교하지 않고, 늦게 끝낸 날(21시)과 일찍 끝낸 날(18시)은 5일씩이다.
    # 뜻은 처음 쓰는 곳에만 붙이고, 처음 붙이는 뜻에만 느린 걷기 제외를 넣는다
    period = DateRange(TODAY - days_before(9), TODAY)
    ds = sorted(back(10))
    nights = [night(d, sleep=420 if i % 2 else 480) for i, d in enumerate(ds)]
    days = [
        day(d - days_before(1), ends=(at(d - days_before(1), "21:00" if i % 2 else "18:00"),)) for i, d in enumerate(ds)
    ]
    text = factors_text(factors(data(nights, days), period), TODAY)
    late = (
        "늦게 운동한 날(운동을 끝내고 3시간이 지나기 전에 잠든 날, 느린 걷기 제외) 5일 평균: 수면 시간 7시간(420분), "
        "깊은 잠 1시간(60분), 수면 효율 88%, 자다가 깬 시간 10분, 수면 중 심박 60bpm / 일찍 운동한 날(운동을 끝내고 "
        "3시간 이상 지나 잠든 날) 5일 평균: 수면 시간 8시간(480분), 깊은 잠 1시간(60분), 수면 효율 100%, 자다가 깬 "
        "시간 10분, 수면 중 심박 60bpm. 늦게 운동한 날이 일찍 운동한 날보다 수면 시간이 1시간(60분) 짧고"
    )
    exercise = "운동한 날(운동 기록이 있는 날) 10일 대 운동하지 않은 날(운동 기록이 없는 날) 0일"
    assert late in text and exercise in text
    assert text.count("느린 걷기 제외") == 1 and text.index(late) < text.index(exercise)
    assert text.count("(운동을 끝내고 3시간이 지나기 전에 잠든 날") == 1


def test_late_exercise_groups_are_named_by_time_from_exercise_end_to_sleep():
    # 늦게 운동한 날은 운동을 끝내고 3시간이 지나기 전에 잠든 날, 일찍 운동한 날은 3시간 이상 지나 잠든 날이다.
    # "잠들기 3시간 안에"·"3시간 전" 표현은 답에서 뜻이 거꾸로 바뀌어 쓰일 수 있어 쓰지 않는다
    for text in edge_texts():
        assert "잠들기 3시간" not in text and "3시간 전" not in text, text
    period = DateRange(TODAY - days_before(9), TODAY)
    ds = sorted(back(10))
    # 22시 잠든 밤에 20시·18시 운동이 끝나 늦은 운동 5일(2시간 전), 이른 운동 5일(4시간 전)
    nights = [night(d, "22:00", 8, deep=40 if i % 2 else 70) for i, d in enumerate(ds)]
    days = [
        day(d - days_before(1), ends=(at(d - days_before(1), "20:00" if i % 2 else "18:00"),)) for i, d in enumerate(ds)
    ]
    text = factors_text(factors(data(nights, days), period, "late_exercise"), TODAY)
    assert "늦게 운동한 날(운동을 끝내고 3시간이 지나기 전에 잠든 날, 느린 걷기 제외) 5일 평균: " in text
    assert " / 일찍 운동한 날(운동을 끝내고 3시간 이상 지나 잠든 날) 5일 평균: " in text
    # 방향은 다음 문장에서 두 무리 이름으로 한 번 더 쓴다
    assert "늦게 운동한 날이 일찍 운동한 날보다 수면 시간은 차이가 없고, 깊은 잠이 30분 짧고" in text


def test_factors_sleep_averages_are_the_reference_group_averages(synth11):
    # 수면 시간은 두 쪽의 평균과 차이를 쓴다. 평균·차이는 참고 구현 결과 파일의 값과 같다 (synth-11 기본 28일)
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))["output"]["factors"]["conditions"]
    sleep = {c["condition"]: c["sleep_minutes"] for c in reference}
    assert {k: (v["true_avg"], v["false_avg"], v["diff"]) for k, v in sleep.items() if v["diff"] is not None} == {
        "exercise": (438, 376, 61),
        "high_steps": (422, 366, 56),
        "short_prev_night": (367, 401, -34),
        "late_bedtime_streak": (308, 409, -101),
        "weekend": (471, 363, 107),
    }
    text = factors_text(factors(synth11), TODAY)
    for phrase in (
        "8일 평균: 수면 시간 7시간 18분(438분)",
        "20일 평균: 수면 시간 6시간 16분(376분)",
        "운동한 날이 운동하지 않은 날보다 수면 시간이 1시간 1분(61분) 길고",
        "평균 7시간 2분(422분), 그보다 적게 걸은 날 다음 잠은 6시간 6분(366분)으로 56분 길고",
        "평균 6시간 7분(367분), 그렇지 않은 날 다음 잠은 6시간 41분(401분)으로 34분 짧고",
        "평균 7시간 51분(471분), 평일에 깬 잠은 6시간 3분(363분)으로 1시간 47분(107분) 길고",
    ):
        assert phrase in text, phrase
    # 한쪽이 4일보다 적은 조건(연달아 늦게 잔 날 1일 대 20일)은 평균을 쓰지 않는다
    assert "308분" not in text


def test_spo2_average_range_is_written_with_slept_labels_and_device_fact():
    ds = sorted(back(4))
    nights = [night(d, spo2=v) for d, v in zip(ds, (95.04, 88.45, 97.0, 88.45), strict=True)]
    text = signals_text(signals(data(nights)), TODAY)
    # 0.5 올림으로 소수 첫째 자리까지 쓰고, 값이 같으면 먼저 깬 잠이다. 잠은 깬 날로 부른다
    assert (
        "잠마다의 평균 산소포화도(4일)는 가장 낮은 날 88.5%(10월 20일 수면), 가장 높은 날 97.0%(10월 21일 수면)다."
        in text
    )
    assert text.index("잠마다의 평균 산소포화도") < text.index(SPO2_DEVICE_FACT)


def test_spo2_average_range_for_one_night_and_without_low_count():
    # 90% 미만 기록 수가 없는 밤이어도 평균이 있으면 평균과 측정 안내를 쓴다
    one = [night(TODAY, low=None, spo2=93.0), *(night(d, low=None) for d in back(4, TODAY - days_before(1)))]
    text = signals_text(signals(data(one)), TODAY)
    # 어제 23시에 잠들어 오늘 깬 잠은 어젯밤이다
    assert "잠마다의 평균 산소포화도는 기록한 1일 93.0%(어젯밤)이다." in text
    assert SPO2_DEVICE_FACT in text and "산소포화도를 기록한 날이 없다" not in text
    none = signals_text(signals(data(night(d, low=None) for d in back(5))), TODAY)
    assert "평균 산소포화도" not in none and "산소포화도를 기록한 날이 없다." in none


def test_short_factors_text_has_asked_nights_longest_shortest_and_note(synth11):
    # 14일보다 짧은 기간은 그 기간의 깬 날별 수면 시간, 가장 오래 잔 날과 가장 짧게 잔 날(깬 날로 부른 잠과 수면 시간),
    # 생활 요인 비교를 계산하지 않은 사실만 쓰고 평균은 쓰지 않는다
    week = DateRange(TODAY - days_before(10), TODAY - days_before(4))
    text = short_factors_text(short_factors(synth11, week), TODAY, "지난주(10월 12일-18일)")
    assert text == (
        "지난주(10월 12일-18일) 날짜별 수면 시간(날짜는 깬 날 기준): 10월 12일 5시간 38분, 10월 13일 6시간 21분, "
        "10월 14일 5시간 17분, 10월 15일 6시간 13분, 10월 16일 2시간 59분, 10월 17일 9시간 26분, 10월 18일 8시간 49분. "
        "이 기간 가장 오래 잔 날은 10월 17일 수면 9시간 26분, 가장 짧게 잔 날은 10월 16일 수면 2시간 59분이다. "
        "생활 요인 비교(운동·걸음 등)는 14일 이상에서만 계산한다."
    )
    assert SHORT_FACTORS_NOTE == "생활 요인 비교(운동·걸음 등)는 14일 이상에서만 계산한다."
    assert "평균" not in text


def test_short_factors_longest_shortest_skip_unknown_minutes_and_ties_take_the_earlier_night():
    # 수면 시간을 모르는 날은 날짜별 줄에 그대로 밝히고 가장 오래·짧게 잔 날에서 뺀다. 값이 같으면 먼저 깬 날이다
    period = DateRange(TODAY - days_before(3), TODAY)
    unknown = replace(night(TODAY - days_before(3)), sleep_minutes=None)
    nights = [
        unknown,
        night(TODAY - days_before(2), sleep=300),
        night(TODAY - days_before(1), sleep=420),
        night(TODAY, sleep=300),
    ]
    text = short_factors_text(short_factors(data(nights), period), TODAY)
    assert text.startswith(
        "10월 19일-22일 날짜별 수면 시간(날짜는 깬 날 기준): 10월 19일 수면 시간 기록 없음, 10월 20일 5시간, "
    )
    assert "이 기간 가장 오래 잔 날은 10월 21일 수면 7시간, 가장 짧게 잔 날은 10월 20일 수면 5시간이다." in text
    one = short_factors_text(short_factors(data([unknown, night(TODAY, sleep=301)]), period), TODAY)
    # 오늘 깬 잠은 어제 잠들었으면 어젯밤이다
    assert "이 기간 수면 시간 기록이 있는 날은 어젯밤 5시간 1분 하루다." in one
    none = short_factors_text(short_factors(data([]), period), TODAY)
    assert none == (
        "10월 19일-22일 날짜별 수면 시간: 기록 없음. 이 기간에 수면 시간 기록이 있는 날이 없다. " + SHORT_FACTORS_NOTE
    )


def test_exercise_comparison_block_names_its_28_days_in_every_sentence(synth11):
    # 14일보다 짧은 기간에 따로 덧붙이는 운동 조건 비교. 머리에 기간을 쓰고 운동 조건 두 개만 쓴다.
    # 요청한 기간의 값으로 읽히지 않게 문장마다 최근 28일이라고 쓴다
    recent = DateRange(TODAY - days_before(27), TODAY)
    text = exercise_comparison_text(factors(synth11, recent), TODAY)
    assert text.startswith(
        "운동 조건 비교(최근 28일, 9월 25일-10월 22일): 요청한 기간은 14일보다 짧아 운동 조건만 최근 28일로 따로 "
        "비교했고, 원인이 아니라 함께 나타난 차이다. 최근 28일 중 운동한 날(운동 기록이 있는 날, 느린 걷기 제외) "
        "8일 평균: 수면 시간 7시간 18분(438분)"
    )
    assert "최근 28일에서는 운동한 날이 운동하지 않은 날보다 수면 시간이 1시간 1분(61분) 길고" in text
    assert text.endswith(
        "최근 28일 중 늦게 운동한 날(운동을 끝내고 3시간이 지나기 전에 잠든 날)은 0일, 일찍 운동한 날(운동을 끝내고 "
        "3시간 이상 지나 잠든 날)은 8일로 한쪽이 4일보다 적어 비교하지 않았다."
    )
    sentences = text.removesuffix(".").split(". ")
    assert len(sentences) == 4 and all("최근 28일" in s for s in sentences), sentences
    # 운동 조건이 아닌 조건과 가장 오래·짧게 잔 밤은 넣지 않는다. 지난주 주말 값으로 옮겨 쓰일 수 있는 주말·평일
    # 평균도 없다
    for word in ("걸은 날", "토·일", "평일", "주말", "전날", "낮 심박", "가장 오래", "가장 짧게"):
        assert word not in text, word


def test_extended_lead_names_the_asked_period_and_why_it_was_recalculated():
    recent = DateRange(TODAY - days_before(27), TODAY)
    two_weeks = DateRange(TODAY - days_before(13), TODAY)
    nights = [night(d) for d in back(14)]
    none = factors(data(nights, [day(d) for d in back(14, TODAY - days_before(1))]), two_weeks)
    assert extended_factors_lead(none, "최근 14일(10월 9일-22일)", recent, TODAY) == (
        "요청한 기간인 최근 14일(10월 9일-22일)에는 운동한 날이 없어 생활 요인 비교를 최근 28일(9월 25일-10월 22일)로 "
        "다시 계산했다."
    )
    # 18시에 끝낸 운동 이틀. 운동한 날 2일 대 12일, 늦게 운동한 날 0일 대 2일로 운동 조건 비교가 하나도 안 된다
    days = [day(d, ends=(at(d, "18:00"),) if i < 2 else ()) for i, d in enumerate(back(14, TODAY - days_before(1)))]
    few = factors(data(nights, days), two_weeks)
    assert extended_factors_lead(few, "최근 14일(10월 9일-22일)", recent, TODAY) == (
        "요청한 기간인 최근 14일(10월 9일-22일)은 운동한 날 2일, 운동하지 않은 날 12일로 운동 조건마다 양쪽 4일이 "
        "되지 않아 생활 요인 비교를 최근 28일(9월 25일-10월 22일)로 다시 계산했다."
    )


def test_good_and_bad_nights_need_four_nights_each():
    f = factors(data(night(d, sleep=400 + i) for i, d in enumerate(back(11))))
    assert factors_text(f, TODAY).endswith(
        "가장 오래 잔 날과 가장 짧게 잔 날은 각각 3일로 4일보다 적어 비교하지 않았다."
    )


def test_declining_line_not_judged_names_the_period_that_is_short_of_records():
    value = SleepVsBaseline(
        recent_avg=400,
        baseline_avg=480,
        diff=-80,
        recent_nights=2,
        baseline_nights=28,
        recent_reliable=False,
        baseline_reliable=True,
        declining=None,
    )
    assert declining_line(value, TODAY) == (
        "최근 14일(10월 9일-22일) 평균 수면은 6시간 40분(400분, 2일)으로 평소인 그 전 28일(9월 11일-10월 8일) "
        "평균 8시간(480분, 28일)보다 1시간 20분(80분) 짧다. "
        "평소보다 60분 이상 짧아 줄어드는 중인지는 기록이 모자라 판단하지 않았다(최근 14일 기록 2일이 3일보다 적음)."
    )


def test_declining_line_without_records():
    value = SleepVsBaseline(None, None, None, 0, 0, False, False, None)
    assert declining_line(value, TODAY) == (
        "최근 14일(10월 9일-22일)과 그 전 28일(9월 11일-10월 8일)에 수면 기록이 없다. "
        "평소보다 60분 이상 짧아 줄어드는 중인지는 기록이 모자라 판단하지 않았다"
        "(최근 14일 기록 0일이 3일보다 적음, 그 전 28일 기록 0일이 3일보다 적음)."
    )


def test_last_night_line_without_last_night_or_enough_week():
    value = LastNightVsWeek(
        sleep_minutes=LastNightValue(last_night=None, week_avg=None, diff=None, week_nights=3),
        sleep_hr_avg=LastNightValue(last_night=None, week_avg=None, diff=None, week_nights=3),
    )
    assert last_night_line(value, TODAY) == (
        "어젯밤(10월 22일에 깬 수면)은 기록이 없다. "
        "그 전 7일(10월 15일-21일)은 수면 시간이 있는 날이 3일로 4일보다 적어 평균을 내지 않았다."
    )


def test_last_night_line_without_heart_rate():
    nights = [night(TODAY, sleep=300, hr=None)]
    nights += [night(TODAY - days_before(k), sleep=420, hr=60.0 if k <= 3 else None) for k in range(1, 8)]
    line = last_night_line(last_night_vs_week(data(nights), TODAY), TODAY)
    assert line == (
        "어젯밤(10월 22일에 깬 수면)은 수면 5시간(300분)이고, 수면 중 심박 기록은 없다. "
        "그 전 7일(10월 15일-21일) 평균 수면은 7시간(420분, 7일)이고, "
        "수면 중 심박은 기록이 있는 날이 3일로 4일보다 적어 평균을 내지 않았다. "
        "어젯밤은 그 전 7일 평균보다 수면이 2시간(120분) 짧다."
    )


def test_last_night_line_says_no_record_when_week_has_none():
    nights = [night(TODAY, sleep=422, hr=None)]
    nights += [night(TODAY - days_before(k), sleep=360, hr=None) for k in range(1, 7)]
    line = last_night_line(last_night_vs_week(data(nights), TODAY), TODAY)
    assert "평균 수면은 6시간(360분, 6일)이고, 수면 중 심박 기록은 없다." in line
    line = last_night_line(last_night_vs_week(data([night(TODAY, sleep=422)]), TODAY), TODAY)
    assert "그 전 7일(10월 15일-21일)은 수면 기록이 없다." in line


def test_texts_follow_base_date_for_dates(synth11):
    base = TODAY - days_before(1)
    line = last_night_line(last_night_vs_week(synth11, base), base)
    # 기준일(10월 21일) 새벽 5시 31분에 잠들어 그날 깬 잠이라 새벽 6시 전에 잠든 "어젯밤"이다
    assert line.startswith("어젯밤(10월 21일에 깬 수면)")
    assert "그 전 7일(10월 14일-20일)" in line


@pytest.mark.parametrize(
    ("bed", "name"),
    [("18:00", "어젯밤"), ("23:00", "어젯밤"), ("00:30", "어젯밤"), ("05:59", "어젯밤"), ("06:00", "오늘 수면")],
)
def test_last_night_line_names_the_sleep_by_when_it_started(bed, name):
    # 오늘 깬 잠은 어제 저녁 6시부터 오늘 새벽 6시 전까지 잠들었으면 "어젯밤"(자정 넘어 잔 밤잠 포함), 오늘 아침
    # 6시 이후(교대 근무로 아침에) 잠들었으면 "오늘 수면"이다
    nights = [night(TODAY, bed=bed, sleep=300), *(night(d, sleep=420) for d in back(7, TODAY - days_before(1)))]
    line = last_night_line(last_night_vs_week(data(nights), TODAY), TODAY)
    assert line.startswith(f"{name}(10월 22일에 깬 수면)은 수면 5시간(300분)")
    assert line.endswith(f"{name}은 그 전 7일 평균보다 수면이 2시간(120분) 짧고 수면 중 심박은 같다.")
