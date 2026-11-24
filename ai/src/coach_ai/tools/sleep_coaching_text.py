"""수면 코칭 계산 결과를 도구 결과 문장으로 바꾼다 (calculation-rules.md 11.7, 계산 세부 11.9).

tools/sleep_coaching.py 의 반올림하지 않은 결과와 기준일을 받아 사실 문장을 반환한다. 지시 문장은 쓰지 않는다.
판정은 계산 모듈이 반올림 전 값으로 한 결과를 그대로 쓰고, 표시 반올림(0.5 올림)은 값마다 한 번만 한다.
차이도 반올림 전 값끼리 뺀 값을 반올림한다. 판정에는 기준값을, 값에는 날 수를 함께 쓴다.
분은 "5시간 56분(356분)", 평균 시각은 KST HH:MM, 특정 시각은 시간대 말을 붙인 시각("새벽 3시 20분")이다.
bpm·비율(%)·걸음은 정수이고 낮 심박 조건의 기준만 소수 첫째 자리다.
문장은 답에 그대로 옮겨도 자연스러운 낱말로 쓴다. 11장 용어(본인 기준, 흔들림, 인정 운동 등)는 코드 이름과
문서에만 쓰고, 문장 끝에는 지시로 읽히는 "~않는다" 를 쓰지 않는다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from coach_ai.tools.periods import DEFAULT_TZ, DateRange, range_text
from coach_ai.tools.results import NO_SLEEP_MINUTES, format_clock_word, format_minutes, format_sleep_name
from coach_ai.tools.rounding import round_half_up
from coach_ai.tools.sleep_coaching import (
    BASELINE_DAYS,
    DECLINING_MINUTES,
    DECLINING_RECENT_DAYS,
    EXERCISE_CONDITIONS,
    FACTORS_MIN_DAYS,
    FRAGMENTED_AWAKE_MINUTES,
    FRAGMENTED_EFFICIENCY_PCT,
    HIGH_DAY_HR_BPM,
    IRREGULAR_MINUTES,
    LAST_NIGHT_WEEK_DAYS,
    LATE_BEDTIME_MINUTES,
    LATE_EXERCISE,
    LONG_SLEEP_MINUTES,
    LOW_DEEP_BASELINE_NIGHTS,
    LOW_DEEP_CHANGE_PCT,
    MIN_NIGHTS,
    MIN_WEEKDAY_NIGHTS,
    MIN_WEEKEND_NIGHTS,
    RELIABLE_MIN_DAYS,
    SHORT_PREV_NIGHT_MINUTES,
    SHORT_SLEEP_MINUTES,
    SHORT_STREAK_MINUTES,
    SHORT_STREAK_NIGHTS,
    SLEEP_HR_HIGH_BPM,
    SLEEP_HR_RECENT_DAYS,
    SPO2_REPEAT_NIGHTS,
    ClockStat,
    Condition,
    ConditionCompare,
    FactorsResult,
    GoodBadNights,
    GroupCompare,
    LastNightVsWeek,
    NotJudged,
    PatternResult,
    ProblemType,
    ShortFactorsResult,
    SignalsResult,
    SleepVsBaseline,
    WeekendGap,
)

_KST = ZoneInfo(DEFAULT_TZ)
_DAY_MINUTES = 1440
_ONE_DAY = dt.timedelta(days=1)
_LATE_EXERCISE_HOURS = int(LATE_EXERCISE.total_seconds() // 3600)

# 수면 기록 결과의 산소포화도 안내와 같은 사실 문장이다
SPO2_DEVICE_FACT = "워치 측정은 의료기기 측정이 아니다."
# 14일보다 짧은 기간의 factors 결과 끝 문장. 조건별 비교를 하지 않은 사실만 쓴다
SHORT_FACTORS_NOTE = f"생활 요인 비교(운동·걸음 등)는 {FACTORS_MIN_DAYS}일 이상에서만 계산한다."

# 문제 유형(11.3)을 이어 쓰는 말 ("최근 14일(…) 수면은 짧고, …"). 앞 항목은 첫 말(~고)을, 마지막 항목은 둘째 말을 쓴다
PROBLEM_WORDS: dict[ProblemType, tuple[str, str]] = {
    "short": ("짧고", "짧다"),
    "long": ("길고", "길다"),
    "declining": ("평소보다 줄어드는 중이고", "평소보다 줄어드는 중이다"),
    "irregular": ("수면 시간대가 불규칙하고", "수면 시간대가 불규칙하다"),
    "fragmented": ("자다가 자주 깨고", "자다가 자주 깬다"),
    "low_deep": ("깊은 잠이 평소보다 적고", "깊은 잠이 평소보다 적다"),
}
# 기록이 모자라 판단하지 않은 유형. 평균 수면 시간이 없으면 not_judged 의 short 하나가 짧음·긴 수면 둘을 뜻한다
_NOT_JUDGED_NAMES: dict[ProblemType, str] = {
    "short": "수면이 짧거나 긴지",
    "long": "수면이 짧거나 긴지",
    "declining": "평소보다 줄어드는 중인지",
    "irregular": "수면 시간대가 불규칙한지",
    "fragmented": "자다가 자주 깨는지",
    "low_deep": "깊은 잠이 평소보다 적은지",
}

# 늘어난 쪽, 줄어든 쪽, 반올림해 차이가 0 인 쪽의 말. 여러 차이를 이을 때 앞 항목은 첫 말(~고)을, 마지막 항목은
# 둘째 말을 쓴다. 지난 잠들을 나눈 비교(11.4)는 지난 일로, 어젯밤 비교(11.6)는 지금 값으로 쓴다
_Words = tuple[tuple[str, str], tuple[str, str], tuple[str, str]]
_LONGER: _Words = (("길고", "길었다"), ("짧고", "짧았다"), ("차이가 없고", "차이가 없었다"))
_HIGHER: _Words = (("높고", "높았다"), ("낮고", "낮았다"), ("차이가 없고", "차이가 없었다"))
_MORE: _Words = (("많고", "많았다"), ("적고", "적었다"), ("차이가 없고", "차이가 없었다"))
_LATER: _Words = (("늦고", "늦었다"), ("빠르고", "빨랐다"), ("차이가 없고", "차이가 없었다"))
_LONGER_NOW: _Words = (("길고", "길다"), ("짧고", "짧다"), ("같고", "같다"))
_HIGHER_NOW: _Words = (("높고", "높다"), ("낮고", "낮다"), ("같고", "같다"))
# 가운데 시각(11.1, 잠든 시각 + 잠든 시각부터 깬 시각까지의 반)을 결과 문장에서 부르는 "자는 시간대" 의 뜻
_MIDPOINT_MEANING = "잠든 시각과 깬 시각의 가운데"
# 낮 심박 높은 날(11.4). 분석 기간의 중앙값을 "이 기간 보통" 으로 써서 평소(앞선 28일)와 구분한다
_HIGH_DAY_HR = f"낮 심박이 이 기간 보통보다 {HIGH_DAY_HR_BPM}bpm 이상 높았던 날"
# 11.4 결과 다섯 개: (칸, 이름, 단위, 말)
_OUTCOMES: tuple[tuple[str, str, str, _Words], ...] = (
    ("sleep_minutes", "수면 시간", "분", _LONGER),
    ("deep_minutes", "깊은 잠", "분", _LONGER),
    ("efficiency_pct", "수면 효율", "%포인트", _HIGHER),
    ("awake_minutes", "자다가 깬 시간", "분", _LONGER),
    ("sleep_hr_avg", "수면 중 심박", "bpm", _HIGHER),
)

_State = Literal["hit", "miss", "skip"]


def declining_line(value: SleepVsBaseline, base_date: dt.date) -> str:
    """줄어드는 중(11.3)의 판정 값. 기준일까지 14일과 그 전 28일의 평균, 차이, 판단을 쓴다."""
    state: _State = "skip" if value.declining is None else ("hit" if value.declining else "miss")
    return _declining(value, base_date, base_date, state)


def last_night_line(value: LastNightVsWeek, base_date: dt.date) -> str:
    """기준일에 깬 주 수면과 그 전 7일 평균의 수면 시간·수면 중 심박, 차이, 날 수를 쓴다 (11.6).

    기준일에 깬 수면의 이름은 format_sleep_name 이 정한다 (전날 저녁 6시~기준일 새벽 6시에 잠들었으면 "어젯밤",
    기준일 아침 6시 이후면 "오늘 수면").
    """
    sleep, hr = value.sleep_minutes, value.sleep_hr_avg
    name = format_sleep_name(base_date, value.start_at, base_date)
    head = f"{name}({range_text(DateRange(base_date, base_date), base_date)}에 깬 수면)"
    last_sleep = None if sleep.last_night is None else _minutes(sleep.last_night)
    last_hr = None if hr.last_night is None else _number(hr.last_night, "bpm")
    if last_sleep is None and last_hr is None:
        lines = [f"{head}은 기록이 없다."]
    elif last_sleep is None:
        lines = [f"{head}은 수면 시간 기록이 없고, 수면 중 심박은 {last_hr}이다."]
    elif last_hr is None:
        lines = [f"{head}은 수면 {last_sleep}이고, 수면 중 심박 기록은 없다."]
    else:
        lines = [f"{head}은 수면 {last_sleep}, 수면 중 심박 {last_hr}이다."]

    week = f"그 전 {LAST_NIGHT_WEEK_DAYS}일({range_text(_before(base_date, LAST_NIGHT_WEEK_DAYS), base_date)})"
    hr_avg = None if hr.week_avg is None else _number(hr.week_avg, "bpm", hr.week_nights)
    if sleep.week_avg is None:
        if sleep.week_nights:
            lines.append(f"{week}은 {_no_average('수면 시간이', sleep.week_nights)}.")
        else:
            lines.append(f"{week}은 수면 기록이 없다.")
        if hr_avg is not None:
            lines.append(f"그 전 {LAST_NIGHT_WEEK_DAYS}일 평균 수면 중 심박은 {hr_avg}이다.")
    elif hr_avg is not None:
        lines.append(f"{week} 평균은 수면 {_minutes(sleep.week_avg, sleep.week_nights)}, 수면 중 심박 {hr_avg}이다.")
    else:
        if hr.week_nights:
            hr_text = f"수면 중 심박은 {_no_average('기록이', hr.week_nights)}"
        else:
            hr_text = "수면 중 심박 기록은 없다"
        lines.append(f"{week} 평균 수면은 {_minutes(sleep.week_avg, sleep.week_nights)}이고, {hr_text}.")

    gaps = [
        (subject, diff, unit, words)
        for subject, diff, unit, words in (
            ("수면", sleep.diff, "분", _LONGER_NOW),
            ("수면 중 심박", hr.diff, "bpm", _HIGHER_NOW),
        )
        if diff is not None
    ]
    if gaps:
        parts = [
            _change(subject, "이", "은", diff, unit, words, i == len(gaps) - 1)
            for i, (subject, diff, unit, words) in enumerate(gaps)
        ]
        lines.append(f"{name}은 그 전 {LAST_NIGHT_WEEK_DAYS}일 평균보다 {' '.join(parts)}.")
    return " ".join(lines)


def pattern_text(result: PatternResult, base_date: dt.date, label: str | None = None) -> str:
    """pattern(11.2 규칙성, 11.3 문제 유형) 결과 문장.

    판단(해당하는 유형, 또는 뚜렷한 문제 없음)과 기록이 모자라 판단하지 않은 유형을 맨 앞에 두고, 유형마다 판단에
    쓴 값과 기준을 11.3 표 순서로 쓴다. 평소와 비교한 값(줄어드는 중, 깊은 잠 적음)은 해당이 없어도 쓰고, 마지막은
    90% 미만 기록이 있는 날 수다. 기간 전체가 4일 미만이면 판단 없이 평소와 비교한 값과 90% 미만 날 수만 쓴다.
    label 은 질문한 기간의 이름과 날짜다 (_head).
    """
    p = result
    vs = p.sleep_vs_baseline
    head = _head(p.period, p.nights, base_date, label)
    if not p.enough_data:
        lines = [
            f"{head}은 기록이 {MIN_NIGHTS}일보다 적어 판단하지 않았다.",
            _declining(vs, p.period.end, base_date, None),
            _low_deep(p, base_date, None, None),
            _spo2_nights(p),
        ]
        return " ".join(lines)

    found = {x.type for x in p.problems}
    skipped = {j.type: j for j in p.not_judged}
    if p.problems:
        words = [PROBLEM_WORDS[x.type] for x in p.problems]
        lines = [f"{head}은 {', '.join([*(w[0] for w in words[:-1]), words[-1][1]])}."]
    elif skipped:
        lines = [f"{head}에서 판단한 것 중에는 뚜렷한 문제가 없다."]
    else:
        lines = [f"{head}에서 뚜렷한 문제는 없다."]
    if skipped:
        names = ", ".join(_NOT_JUDGED_NAMES[t] for t in skipped if t != "all")
        lines.append(f"기록이 모자라 판단하지 않은 것: {names}.")

    def state(kind: ProblemType) -> _State:
        return "hit" if kind in found else "skip" if kind in skipped else "miss"

    lines += [
        _amount(p, found),
        _declining(vs, p.period.end, base_date, state("declining")),
        _irregular(p, found),
        _waketime(p),
        _fragmented(p, state("fragmented")),
        _low_deep(p, base_date, state("low_deep"), skipped.get("low_deep")),
        _spo2_nights(p),
    ]
    return " ".join(lines)


def _amount(p: PatternResult, found: set[ProblemType]) -> str:
    short, long = format_minutes(SHORT_SLEEP_MINUTES), format_minutes(LONG_SLEEP_MINUTES)
    avg = p.sleep_avg_min
    if avg.value is None:
        return (
            f"수면 시간이 있는 날이 {avg.nights}일로 {MIN_NIGHTS}일보다 적어 평균 수면 시간을 내지 않았고, "
            f"수면이 {short} 미만으로 짧은지 {long} 이상으로 긴지 판단하지 않았다."
        )
    value = f"평균 수면 시간은 {_minutes(avg.value, avg.nights)}으로"
    # 짧음과 긴 수면은 함께 해당할 수 없어, 하나에 해당하면 다른 쪽 기준은 쓰지 않는다
    if "short" in found:
        return f"{value} {short} 미만이라 짧다."
    if "long" in found:
        return f"{value} {long} 이상이라 길다."
    return f"{value} {short} 이상 {long} 미만이라 짧거나 길지는 않았다."


def _declining(v: SleepVsBaseline, end: dt.date, base_date: dt.date, state: _State | None) -> str:
    """기간 끝까지 14일과 그 전 28일(평소)의 평균 수면 비교. state 가 None 이면 판단 없이 값만 쓴다."""
    recent = DateRange(end - dt.timedelta(days=DECLINING_RECENT_DAYS - 1), end)
    recent_name = f"{'최근 ' if end == base_date else ''}{DECLINING_RECENT_DAYS}일"
    recent_label = f"{recent_name}({range_text(recent, base_date)})"
    base_label = f"그 전 {BASELINE_DAYS}일({range_text(_before(recent.start, BASELINE_DAYS), base_date)})"
    if v.recent_avg is None and v.baseline_avg is None:
        values = f"{recent_label}과 {base_label}에 수면 기록이 없다."
    elif v.recent_avg is None:
        values = (
            f"{recent_label}에는 수면 기록이 없고, 평소인 {base_label} 평균 수면은 "
            f"{_minutes(v.baseline_avg, v.baseline_nights)}이다."
        )
    elif v.baseline_avg is None or v.diff is None:
        values = (
            f"{recent_label} 평균 수면은 {_minutes(v.recent_avg, v.recent_nights)}이고, "
            f"평소인 {base_label}에는 수면 기록이 없다."
        )
    else:
        values = (
            f"{recent_label} 평균 수면은 {_minutes(v.recent_avg, v.recent_nights)}으로 평소인 {base_label} 평균 "
            f"{_minutes(v.baseline_avg, v.baseline_nights)}{_than(v.diff, '분', ('길다', '짧다'))}."
        )
    if state is None:
        return values
    if state == "skip":
        short_of = [
            f"{label} 기록 {nights}일이 "
            + (f"{RELIABLE_MIN_DAYS}일보다 적음" if nights < RELIABLE_MIN_DAYS else "연동 뒤 날 수의 절반보다 적음")
            for label, nights, reliable in (
                (recent_name, v.recent_nights, v.recent_reliable),
                (f"그 전 {BASELINE_DAYS}일", v.baseline_nights, v.baseline_reliable),
            )
            if not reliable
        ]
        return (
            f"{values} 평소보다 {DECLINING_MINUTES}분 이상 짧아 줄어드는 중인지는 기록이 모자라 판단하지 않았다"
            f"({', '.join(short_of)})."
        )
    if state == "hit":
        return f"{values} 평소보다 {DECLINING_MINUTES}분 이상 짧아 수면이 줄어드는 중이다."
    return f"{values} 평소보다 {DECLINING_MINUTES}분 이상 짧지 않아 줄어드는 중인 상태는 아니다."


def _irregular(p: PatternResult, found: set[ProblemType]) -> str:
    # 자는 시간대는 처음 나오는 이 곳에만 뜻을 붙인다. 뜻이 없으면 답에서 이 값을 잠든 시각 차이로 쓸 수 있다
    midpoint = _weekend_gap(f"자는 시간대({_MIDPOINT_MEANING})", "가", p.midpoint_gap_min)
    values = f"{_clock_values('잠든 시각', p.bedtime)}. {midpoint}."
    limit = f"{IRREGULAR_MINUTES}분"
    if "irregular" in found:
        verdict = (
            f"잠든 시각이 날마다 보통 {limit} 이상 달라지거나 주말·평일 자는 시간대가 {limit} 이상 차이 나서 "
            "수면 시간대가 불규칙하다."
        )
    elif p.midpoint_gap_min.min is None:
        # 주말·평일 차이가 없으면 계산 모듈은 잠든 시각 흔들림으로만 판정한다
        verdict = f"잠든 시각이 날마다 달라지는 폭이 {limit}보다 작아 수면 시간대가 불규칙하지는 않았다."
    else:
        verdict = (
            f"잠든 시각이 날마다 달라지는 폭과 주말·평일 자는 시간대 차이가 모두 {limit}보다 작아 "
            "수면 시간대가 불규칙하지는 않았다."
        )
    return f"{values} {verdict}"


def _waketime(p: PatternResult) -> str:
    return f"{_clock_values('깬 시각', p.waketime)}. {_weekend_gap('깬 시각', '이', p.waketime_gap_min)}."


def _fragmented(p: PatternResult, state: _State) -> str:
    awake, efficiency = p.awake_avg_min, p.efficiency_avg_pct
    first, _ = _avg_item(
        "자다가 깬 시간", None if awake.value is None else _minutes(awake.value, awake.nights), awake.nights
    )
    _, last = _avg_item(
        "수면 효율",
        None if efficiency.value is None else _number(efficiency.value, "%", efficiency.nights),
        efficiency.nights,
    )
    values = f"{first}, {last}."
    awake_limit, efficiency_limit = f"{FRAGMENTED_AWAKE_MINUTES}분", f"{FRAGMENTED_EFFICIENCY_PCT}%"
    if state == "skip":
        return (
            f"{values} 자다가 깬 시간이 평균 {awake_limit}을 넘거나 수면 효율이 {efficiency_limit}보다 낮아 "
            "자다가 자주 깨는지는 기록이 모자라 판단하지 않았다."
        )
    if state == "hit":
        return (
            f"{values} 자다가 깬 시간이 평균 {awake_limit}을 넘거나 수면 효율이 {efficiency_limit}보다 낮아 "
            "자다가 자주 깬다."
        )
    # 해당이 없으면 값이 있는 쪽은 모두 기준 안이다
    kept = [
        text
        for value, text in (
            (awake.value, f"자다가 깬 시간이 평균 {awake_limit} 이하"),
            (efficiency.value, f"수면 효율이 {efficiency_limit} 이상"),
        )
        if value is not None
    ]
    reason = "이고 ".join(kept)
    return f"{values} {reason}{'라' if reason.endswith('이하') else '이라'} 자다가 자주 깨지는 않았다."


def _low_deep(p: PatternResult, base_date: dt.date, state: _State | None, skipped: NotJudged | None) -> str:
    d = p.deep_ratio_vs_baseline
    base = f"평소({range_text(p.baseline_period, base_date)})"
    recent = None if d.recent_avg_pct is None else _number(d.recent_avg_pct, "%", d.recent_nights)
    baseline = None if d.baseline_avg_pct is None else _number(d.baseline_avg_pct, "%", d.baseline_nights)
    if d.change_pct is not None and recent is not None and baseline is not None:
        values = f"깊은 잠 비율은 평균 {recent}로 {base} {baseline}{_than(d.change_pct, '%', ('높다', '낮다'), '와')}."
    else:
        first, _ = _avg_item("깊은 잠 비율", recent, d.recent_nights)
        rest = f"{base}에는 깊은 잠 기록이 없다" if baseline is None else f"{base} 깊은 잠 비율은 {baseline}이다"
        values = f"{first}, {rest}."
    if state is None:
        return values
    limit = f"{-LOW_DEEP_CHANGE_PCT}%"
    if state == "skip" and skipped is not None:
        if skipped.reason == "baseline_below_7":
            why = f"평소 깊은 잠 기록이 {skipped.baseline_nights}일로 {LOW_DEEP_BASELINE_NIGHTS}일보다 적어"
        else:
            why = f"깊은 잠 기록이 있는 날이 {skipped.nights}일로 {MIN_NIGHTS}일보다 적어"
        return f"{values} 깊은 잠 비율이 평소보다 {limit} 이상 낮은지는 {why} 판단하지 않았다."
    if state == "hit":
        return f"{values} 평소보다 {limit} 이상 낮아 깊은 잠이 적다."
    return f"{values} 평소보다 {limit} 이상 낮지는 않아 깊은 잠이 적은 상태는 아니다."


def _spo2_nights(p: PatternResult) -> str:
    s = p.spo2_low_nights
    if not s.recorded_nights:
        return "산소포화도를 기록한 날이 없다."
    return f"산소포화도를 기록한 {s.recorded_nights}일 중 90% 미만으로 기록된 날은 {s.low_nights}일이다."


def signals_text(result: SignalsResult, base_date: dt.date, label: str | None = None) -> str:
    """signals(11.5 이상 신호) 결과 문장.

    산소포화도를 기록한 날 수, 90% 미만으로 기록된 날 수, 기록 횟수, 시각(이른 순 5개와 "외 N회")을 먼저 쓴다.
    시각은 그 시각이 기록된 잠의 이름(깬 날, 어젯밤, 오늘 수면)과 시간대 말을 붙여 쓰고, 90% 미만 기록이 있는 잠 중
    시각이 없는 잠이 있으면 횟수만 쓴다. 이어서 잠마다의 산소포화도 평균이 가장 낮은 날과 가장 높은 날을 쓴다.
    산소포화도 값을 쓰면 워치 측정이 의료기기 측정이 아니라는 사실을 넣는다. 수면 중 심박과 짧은 수면 연속은 값과
    기준, 판단을 쓴다. label 은 질문한 기간의 이름과 날짜다 (_head).
    """
    s = result
    r = s.spo2_low_repeat
    head = _head(s.period, s.nights, base_date, label)
    lines = []
    where = f"{head}에서 "
    if not s.enough_data:
        lines.append(f"{head}은 기록이 {MIN_NIGHTS}일보다 적다.")
        where = ""
    average = _spo2_avg_range(s, base_date)
    if not r.recorded_nights and average is None:
        lines.append(f"{where}산소포화도를 기록한 날이 없다.")
    else:
        if r.recorded_nights:
            times = (
                f"({low_times_text(r.times, r.slept_at, r.more or 0, r.woke_on, base_date)})"
                if r.times and r.slept_at is not None and r.woke_on is not None
                else ""
            )
            lines += [
                f"{where}산소포화도를 기록한 {r.recorded_nights}일 중 90% 미만으로 기록된 날은 {r.low_nights}일, "
                f"기록은 {r.low_records}회{times}다.",
                f"90% 미만이 {SPO2_REPEAT_NIGHTS}일 이상 되풀이됐다."
                if r.hit
                else f"90% 미만이 {SPO2_REPEAT_NIGHTS}일 이상 되풀이되지는 않았다.",
            ]
        if average is not None:
            lines.append(average)
        lines.append(SPO2_DEVICE_FACT)
    lines += [_sleep_hr(s, base_date), _short_streak(s, base_date)]
    return " ".join(lines)


def _spo2_avg_range(s: SignalsResult, base_date: dt.date) -> str | None:
    """잠마다의 산소포화도 평균이 가장 낮은 날과 가장 높은 날. 평균 값이 있는 잠이 없으면 None 이다."""
    a = s.spo2_avg_range
    if a.lowest is None or a.highest is None:
        return None
    low = f"{_pct1(a.lowest.value)}%({format_sleep_name(a.lowest.date, a.lowest.start_at, base_date)})"
    if a.nights == 1:
        return f"잠마다의 평균 산소포화도는 기록한 1일 {low}이다."
    high = f"{_pct1(a.highest.value)}%({format_sleep_name(a.highest.date, a.highest.start_at, base_date)})"
    return f"잠마다의 평균 산소포화도({a.nights}일)는 가장 낮은 날 {low}, 가장 높은 날 {high}다."


def low_times_text(
    times: Sequence[dt.datetime],
    slept_at: Sequence[dt.datetime],
    more: int,
    woke_on: Sequence[dt.date],
    today: dt.date,
) -> str:
    """90% 미만 기록 시각을 잠마다 묶는다 ("깬 날 기준 10월 13일 수면 새벽 1시·1시 30분, 어젯밤 새벽 1시 20분 외 2회").

    slept_at·woke_on 은 times 와 같은 순서로 각 시각이 기록된 잠의 잠든 시각과 깬 날짜이고, more 는 보여 주지 않은
    기록 수다. 잠은 format_sleep_name 으로, 시각은 시간대 말을 붙여 쓴다. 같은 잠의 둘째 시각부터는 앞 시각과 시간대
    말이 같으면 뺀다. 수면 기록 결과의 spo2_low 문장과 이상 신호 결과 문장이 함께 쓴다.
    """
    groups: list[tuple[str, list[str]]] = []
    last_word = ""
    for moment, start, woke in zip(times, slept_at, woke_on, strict=True):
        label, clock = format_sleep_name(woke, start, today), format_clock_word(moment)
        word, _, rest = clock.partition(" ")
        if groups and groups[-1][0] == label:
            groups[-1][1].append(rest if word == last_word else clock)
        else:
            # "어젯밤 밤 11시"처럼 겹치지 않게 어젯밤 뒤의 "밤"은 뺀다
            groups.append((label, [rest if label == "어젯밤" and word == "밤" else clock]))
        last_word = word
    listed = ", ".join(f"{label} {'·'.join(clocks)}" for label, clocks in groups)
    listed = f"깬 날 기준 {listed}"
    return f"{listed} 외 {more}회" if more else listed


def _sleep_hr(s: SignalsResult, base_date: dt.date) -> str:
    h = s.sleep_hr_high
    recent = DateRange(s.period.end - dt.timedelta(days=SLEEP_HR_RECENT_DAYS - 1), s.period.end)
    recent_label = (
        f"{'최근 ' if recent.end == base_date else ''}{SLEEP_HR_RECENT_DAYS}일({range_text(recent, base_date)})"
    )
    base_label = f"평소({range_text(s.baseline_period, base_date)})"
    if h.recent_avg is None and h.baseline_avg is None:
        values = f"{recent_label}과 {base_label}에 수면 중 심박 기록이 없다."
    elif h.recent_avg is None:
        values = (
            f"{recent_label}에는 수면 중 심박 기록이 없고, {base_label} 평균은 "
            f"{_number(h.baseline_avg, 'bpm', h.baseline_nights)}이다."
        )
    elif h.baseline_avg is None or h.diff is None:
        values = (
            f"{recent_label} 수면 중 심박은 평균 {_number(h.recent_avg, 'bpm', h.recent_nights)}이고, "
            f"{base_label}에는 기록이 없다."
        )
    else:
        values = (
            f"{recent_label} 수면 중 심박은 평균 {_number(h.recent_avg, 'bpm', h.recent_nights)}으로 {base_label} 평균 "
            f"{_number(h.baseline_avg, 'bpm', h.baseline_nights)}{_than(h.diff, 'bpm', ('높다', '낮다'))}."
        )
    if h.diff is None:
        return (
            f"{values} 수면 중 심박이 평소보다 {SLEEP_HR_HIGH_BPM}bpm 넘게 높은 상태가 이어지는지는 "
            "기록이 모자라 판단하지 않았다."
        )
    if h.hit:
        return f"{values} 평소보다 {SLEEP_HR_HIGH_BPM}bpm 넘게 높아 수면 중 심박이 계속 높은 상태다."
    return f"{values} 평소보다 {SLEEP_HR_HIGH_BPM}bpm 넘게 높지는 않아 수면 중 심박이 계속 높은 상태는 아니다."


def _short_streak(s: SignalsResult, base_date: dt.date) -> str:
    st = s.short_sleep_streak
    limit = format_minutes(SHORT_STREAK_MINUTES)
    if not st.longest:
        return f"{limit} 미만으로 잔 날이 없어 {SHORT_STREAK_NIGHTS}일 연달아 이어지지는 않았다."
    dates = range_text(DateRange(st.dates[0], st.dates[-1]), base_date)
    longest = f"가장 길게 이어진 것은 {st.longest}일, {dates} 수면"
    if st.hit:
        return f"{limit} 미만으로 잔 날이 {SHORT_STREAK_NIGHTS}일 이상 연달아 이어졌다({longest})."
    return f"{limit} 미만으로 잔 날이 {SHORT_STREAK_NIGHTS}일 연달아 이어지지는 않았다({longest})."


# 운동 조건(11.4)의 두 무리: (사용자가 쓰는 말, 뜻). "잠들기 3시간 안에 운동을 끝낸 날"은 답에서 "잠들기 3시간
# 전까지 마친 날"처럼 뜻이 거꾸로 바뀌어 쓰일 수 있어, 운동을 끝낸 때에서 잠든 때까지로 쓴다
_EXERCISE_GROUPS: dict[Condition, tuple[tuple[str, str], tuple[str, str]]] = {
    "exercise": (("운동한 날", "운동 기록이 있는 날"), ("운동하지 않은 날", "운동 기록이 없는 날")),
    "late_exercise": (
        ("늦게 운동한 날", f"운동을 끝내고 {_LATE_EXERCISE_HOURS}시간이 지나기 전에 잠든 날"),
        ("일찍 운동한 날", f"운동을 끝내고 {_LATE_EXERCISE_HOURS}시간 이상 지나 잠든 날"),
    ),
}


class _Names:
    """운동 조건 두 무리를 가리키는 말. 처음 쓸 때만 뜻을 괄호로 붙이고, 처음 붙이는 뜻에 느린 걷기 제외를 넣는다."""

    def __init__(self) -> None:
        self._written: set[str] = set()

    def __call__(self, name: str, meaning: str) -> str:
        if name in self._written:
            return name
        note = meaning if self._written else f"{meaning}, 느린 걷기 제외"
        self._written.add(name)
        return f"{name}({note})"


def factors_text(result: FactorsResult, base_date: dt.date, label: str | None = None) -> str:
    """factors(11.4 조건별 비교) 결과 문장. 함께 나타난 차이만 쓰고 인과로 쓰지 않는다.

    양쪽 모두 4일 이상인 결과만 차이를 쓰고, 그런 결과가 없는 조건은 이름과 날 수만 쓴다. 운동 조건은 두 무리를
    모두 사용자가 쓰는 말과 뜻으로 쓰고 끝에 방향을 한 번 더 쓴다. 다른 조건은 "~한 날 다음 잠에는 ~ 날 다음 잠보다
    ~했다(N일 대 M일)" 꼴이다. 가장 오래 잔 날·가장 짧게 잔 날도 두 그룹이 모두 4일 이상일 때만 값을 쓴다.
    문장은 나오는 순서대로 만들어 운동 조건의 뜻은 처음 쓰는 곳에서만 붙인다. label 은 질문한 기간의 이름과 날짜다.
    """
    f = result
    names = _Names()
    lines = [
        f"아래 비교는 {_head(f.period, f.nights, base_date, label)}을 잠들기 전 낮에 있었던 일로 나눠 본 것이고, "
        "원인이 아니라 함께 나타난 차이다."
    ]
    outcomes = [(c, _compared_outcomes(c)) for c in f.conditions]
    for c, compared in outcomes:
        if not compared:
            continue
        if c.condition in EXERCISE_CONDITIONS:
            lines.append(_exercise_condition(c, compared, names))
        else:
            lines.append(_condition(c, compared, f))
    not_compared = []
    for c, compared in outcomes:
        if compared:
            continue
        true_nights, false_nights = _group_nights(c)
        if c.condition in EXERCISE_CONDITIONS:
            (true_name, true_meaning), (false_name, false_meaning) = _EXERCISE_GROUPS[c.condition]
            true_label, false_label = names(true_name, true_meaning), names(false_name, false_meaning)
            not_compared.append(f"{true_label} {true_nights}일 대 {false_label} {false_nights}일")
            continue
        name, note = _condition_name(c, f)
        counts = f"{true_nights}일 대 {false_nights}일"
        not_compared.append(f"{name}({note}, {counts})" if note else f"{name}({counts})")
    if not_compared:
        lines.append(f"한쪽이 {MIN_NIGHTS}일보다 적어 비교하지 않은 것: {', '.join(not_compared)}.")
    if f.good_bad_nights is not None:
        lines.append(_good_bad(f.good_bad_nights))
    return " ".join(lines)


def exercise_comparison_text(result: FactorsResult, base_date: dt.date) -> str:
    """14일보다 짧은 기간의 factors 에 따로 덧붙이는 운동 조건 비교. result 는 최근 28일의 factors 결과다.

    머리에 기간을 쓰고("운동 조건 비교(최근 28일, 10월 22일-11월 18일): …"), 운동 조건 두 개만 두 무리 문장으로 쓴다.
    요청한 기간의 값으로 읽히지 않게 문장마다 최근 28일이라고 쓴다.
    """
    f = result
    scope = f"최근 {(f.period.end - f.period.start).days + 1}일"
    names = _Names()
    lines = [
        f"운동 조건 비교({scope}, {range_text(f.period, base_date)}): 요청한 기간은 {FACTORS_MIN_DAYS}일보다 짧아 "
        f"운동 조건만 {scope}로 따로 비교했고, 원인이 아니라 함께 나타난 차이다."
    ]
    for c in f.conditions:
        if c.condition not in EXERCISE_CONDITIONS:
            continue
        compared = _compared_outcomes(c)
        if compared:
            lines.append(_exercise_condition(c, compared, names, scope))
            continue
        (true_name, true_meaning), (false_name, false_meaning) = _EXERCISE_GROUPS[c.condition]
        true_nights, false_nights = _group_nights(c)
        lines.append(
            f"{scope} 중 {names(true_name, true_meaning)}은 {true_nights}일, {names(false_name, false_meaning)}은 "
            f"{false_nights}일로 한쪽이 {MIN_NIGHTS}일보다 적어 비교하지 않았다."
        )
    return " ".join(lines)


def extended_factors_lead(asked: FactorsResult, asked_label: str, period: DateRange, base_date: dt.date) -> str:
    """14일 이상인 요청 기간에 운동 조건 비교가 하나도 안 돼 생활 요인 비교를 period 로 다시 계산했다는 첫 문장.

    asked 는 요청한 기간의 factors 결과이고 asked_label 은 그 기간의 이름과 날짜다. 이유는 운동한 날 수로 쓴다.
    """
    exercise = next(c for c in asked.conditions if c.condition == "exercise")
    true_nights, false_nights = _group_nights(exercise)
    scope = f"최근 {(period.end - period.start).days + 1}일({range_text(period, base_date)})"
    if true_nights:
        why = (
            f"요청한 기간인 {asked_label}은 운동한 날 {true_nights}일, 운동하지 않은 날 {false_nights}일로 운동 "
            f"조건마다 양쪽 {MIN_NIGHTS}일이 되지 않아"
        )
    else:
        why = f"요청한 기간인 {asked_label}에는 운동한 날이 없어"
    return f"{why} 생활 요인 비교를 {scope}로 다시 계산했다."


def short_factors_text(result: ShortFactorsResult, base_date: dt.date, label: str | None = None) -> str:
    """14일보다 짧은 기간의 factors 결과 문장. 평균이나 다른 기간의 숫자는 쓰지 않는다.

    질문한 기간의 이름(label, 없으면 날짜)과 깬 날별 수면 시간, 그 기간에 가장 오래 잔 날과 가장 짧게 잔 날(잠 이름과
    수면 시간), 생활 요인 비교를 계산하지 않은 사실을 차례로 쓴다.
    """
    r = result
    lines = [_asked_nights(label or range_text(r.period, base_date), r.nights, base_date)]
    lines.append(_longest_shortest(r, base_date))
    lines.append(SHORT_FACTORS_NOTE)
    return " ".join(lines)


def _longest_shortest(r: ShortFactorsResult, base_date: dt.date) -> str:
    """그 기간에 가장 오래 잔 날과 가장 짧게 잔 날 ("…은 10월 18일 수면 11시간 10분, 가장 짧게 잔 날은 …")."""
    starts = dict(r.starts)

    def night(value: tuple[dt.date, int]) -> str:
        d, m = value
        return f"{format_sleep_name(d, starts.get(d), base_date)} {format_minutes(m)}"

    if r.longest is None or r.shortest is None:
        return "이 기간에 수면 시간 기록이 있는 날이 없다."
    if r.known_nights == 1:
        return f"이 기간 수면 시간 기록이 있는 날은 {night(r.longest)} 하루다."
    return f"이 기간 가장 오래 잔 날은 {night(r.longest)}, 가장 짧게 잔 날은 {night(r.shortest)}이다."


def _asked_nights(label: str, nights: Sequence[tuple[dt.date, int | None]], base_date: dt.date) -> str:
    """질문한 기간의 날짜별 수면 시간 한 줄 ("지난주(10월 12일-18일) 날짜별 수면 시간(날짜는 깬 날 기준): 10월 12일
    5시간 38분, …").

    날짜는 기본 조회처럼 깬 날짜로 쓰고, 수면 시간은 그대로 쓴다. 지난 주말처럼 특정 날을 묻는 질문에 그날의 값으로
    답할 수 있게 맨 앞에 둔다.
    """
    if not nights:
        return f"{label} 날짜별 수면 시간: 기록 없음."
    items = ", ".join(
        f"{range_text(DateRange(d, d), base_date)} {NO_SLEEP_MINUTES if m is None else format_minutes(m)}"
        for d, m in nights
    )
    return f"{label} 날짜별 수면 시간(날짜는 깬 날 기준): {items}."


_Outcome = tuple[str, str, str, _Words, GroupCompare]


def _compared_outcomes(c: ConditionCompare) -> list[_Outcome]:
    """양쪽 4일 이상인 결과 (칸, 이름, 단위, 말, 값)."""
    return [(key, name, unit, words, g) for key, name, unit, words in _OUTCOMES if (g := getattr(c, key)).enough_data]


def _exercise_condition(c: ConditionCompare, compared: list[_Outcome], names: _Names, scope: str | None = None) -> str:
    """양쪽 4일 이상인 결과가 있는 운동 조건 하나의 문장.

    두 무리를 모두 사용자가 쓰는 말과 뜻, 날 수, 결과마다의 평균으로 쓰고 다음 문장에 방향(차이)을 한 번 더 쓴다.
    "늦게 운동한 날(…) 8일 평균: 수면 시간 7시간 24분(444분), 깊은 잠 52분 / 일찍 운동한 날(…) 5일 평균: 수면 시간
    7시간 8분(428분), 깊은 잠 1시간 21분(81분). 늦게 운동한 날이 일찍 운동한 날보다 수면 시간이 16분 길고, 깊은 잠이
    29분 짧았다." 형식이다. 무리의 날 수는 결과 가운데 가장 많은 날 수이고, 그와 다른 값에는 그 날 수를 붙인다.
    scope 가 있으면 문장마다 그 기간("최근 28일")을 쓴다.
    """
    (true_name, true_meaning), (false_name, false_meaning) = _EXERCISE_GROUPS[c.condition]
    true_nights = max(g.true_nights for *_, g in compared)
    false_nights = max(g.false_nights for *_, g in compared)

    def averages(true_side: bool, group_nights: int) -> str:
        items = []
        for _, name, unit, _, g in compared:
            avg, nights = (g.true_avg, g.true_nights) if true_side else (g.false_avg, g.false_nights)
            shown = None if nights == group_nights else nights
            if unit == "분":
                value = _minutes(_present(avg), shown)
            else:
                # 수면 효율은 차이만 %포인트이고 평균은 % 다
                value = _number(_present(avg), "%" if unit == "%포인트" else unit, shown)
            items.append(f"{name} {value}")
        return ", ".join(items)

    true_label, false_label = names(true_name, true_meaning), names(false_name, false_meaning)
    lines = [
        f"{f'{scope} 중 ' if scope else ''}{true_label} {true_nights}일 평균: {averages(True, true_nights)} / "
        f"{false_label} {false_nights}일 평균: {averages(False, false_nights)}."
    ]
    parts = [
        _change(name, "이", "은", _diff(g), unit, words, i == len(compared) - 1)
        for i, (_, name, unit, words, g) in enumerate(compared)
    ]
    lines.append(f"{f'{scope}에서는 ' if scope else ''}{true_name}이 {false_name}보다 {', '.join(parts)}.")
    # 양쪽 모두 기록이 없는 결과는 쓰지 않는다
    rest = [
        f"{name}({g.true_nights}일 대 {g.false_nights}일)"
        for key, name, _, _ in _OUTCOMES
        if not (g := getattr(c, key)).enough_data and (g.true_nights or g.false_nights)
    ]
    if rest:
        lines.append(
            f"{f'{scope}에서 ' if scope else ''}한쪽이 {MIN_NIGHTS}일보다 적어 비교하지 않은 것: {', '.join(rest)}."
        )
    return " ".join(lines)


def _condition(c: ConditionCompare, compared: list[_Outcome], f: FactorsResult) -> str:
    """운동 조건이 아닌 조건 가운데 양쪽 4일 이상인 결과가 있는 조건 하나의 문장.

    수면 시간을 비교하면 두 쪽의 평균과 차이를 먼저 쓰고("~ 다음 잠은 수면 시간이 평균 …, ~ 다음 잠은 …으로 30분
    길었고"), 다른 결과는 차이만 잇는다. 수면 시간을 비교하지 않으면 "~ 다음 잠에는 ~ 다음 잠보다" 로 차이만 쓴다.
    평균은 계산 모듈이 차이를 낼 때 구한 두 쪽의 평균이고, 차이는 반올림 전 평균끼리 뺀 값이라 반올림한 두 평균의
    차와 1분 다를 수 있다.
    """
    counts = {(g.true_nights, g.false_nights) for *_, g in compared}
    each = len(counts) > 1

    def nights(g: GroupCompare) -> str:
        return f"({g.true_nights}일 대 {g.false_nights}일)" if each else ""

    true_side, false_side = _condition_sides(c, f)
    rest = compared
    if compared[0][0] == "sleep_minutes":
        g = compared[0][4]
        rest = compared[1:]
        amount = _amount_word(_diff(g), "분", _LONGER, not rest)
        text = (
            f"{true_side}은 수면 시간이 평균 {_minutes(_present(g.true_avg))}, {false_side}은 "
            f"{_minutes(_present(g.false_avg))}으로 {amount}{nights(g)}"
        )
        if rest:
            text += ", "
    else:
        text = f"{true_side}에는 {false_side}보다 "
    parts = [
        _change(name, "이", "은", _diff(g), unit, words, i == len(rest) - 1) + nights(g)
        for i, (_, name, unit, words, g) in enumerate(rest)
    ]
    text += ", ".join(parts)
    if not each:
        [(true_nights, false_nights)] = counts
        text += f"({true_nights}일 대 {false_nights}일)"
    # 양쪽 모두 기록이 없는 결과는 쓰지 않는다
    rest = [
        f"{name}({g.true_nights}일 대 {g.false_nights}일)"
        for key, name, _, _ in _OUTCOMES
        if not (g := getattr(c, key)).enough_data and (g.true_nights or g.false_nights)
    ]
    if rest:
        text += f". 한쪽이 {MIN_NIGHTS}일보다 적어 비교하지 않은 것: {', '.join(rest)}"
    return f"{text}."


def _condition_sides(c: ConditionCompare, f: FactorsResult) -> tuple[str, str]:
    """운동 조건이 아닌 조건이 참인 잠과 거짓인 잠 (11.4 표). 조건 날 D 의 다음 잠은 D+1 에 깬 주 수면이다.

    "{참}은 …, {거짓}은 …" 이나 "{참}에는 {거짓}보다" 로 잇는다. 운동 조건은 _EXERCISE_GROUPS 다.
    """
    match c.condition:
        case "high_steps":
            split = _steps_split(f)
            return (f"{split} 이상 걸은 날" if split else "걸음이 많은 날") + " 다음 잠", "그보다 적게 걸은 날 다음 잠"
        case "short_prev_night":
            return (
                f"앞선 수면이 기간 평균보다 {SHORT_PREV_NIGHT_MINUTES}분 이상 짧았던 날 다음 잠",
                "그렇지 않은 날 다음 잠",
            )
        case "late_bedtime_streak":
            return (
                f"이틀 연달아 기간 평균보다 {LATE_BEDTIME_MINUTES}분 이상 늦게 잠든 날 다음 잠",
                "이틀 모두 그렇지 않은 날 다음 잠",
            )
        case "high_day_hr":
            split = _day_hr_split(f)
            high = f"{_HIGH_DAY_HR}({split} 이상)" if split else _HIGH_DAY_HR
            return f"워치를 충분히 찬 날 중 {high} 다음 잠", "그렇지 않은 날 다음 잠"
        case "weekend":
            return "토·일에 깬 잠", "평일에 깬 잠"
    raise ValueError(f"알 수 없는 조건: {c.condition}")


def _condition_name(c: ConditionCompare, f: FactorsResult) -> tuple[str, str | None]:
    """비교하지 않은 조건(운동 조건 밖)을 날 수와 함께 쓸 때의 짧은 이름과, 날 수와 같은 괄호에 넣을 기준 숫자."""
    match c.condition:
        case "high_steps":
            split = _steps_split(f)
            return (f"{split} 이상 걸은 날" if split else "걸음이 많은 날"), None
        case "short_prev_night":
            return "전날 짧게 잔 날", None
        case "late_bedtime_streak":
            return "이틀 연달아 늦게 잔 날", None
        case "high_day_hr":
            split = _day_hr_split(f)
            return _HIGH_DAY_HR, (f"{split} 이상" if split else None)
        case "weekend":
            return "주말", None
    raise ValueError(f"알 수 없는 조건: {c.condition}")


def _steps_split(f: FactorsResult) -> str | None:
    """걸음 많은 날을 가른 걸음 수.

    걸음은 정수이고 중앙값은 정수이거나 .5 라, 0.5 올림 값 이상인 날이 중앙값 이상인 날이다.
    """
    return None if f.steps_median is None else f"{_round(f.steps_median):,}보"


def _day_hr_split(f: FactorsResult) -> str | None:
    """낮 심박 높은 날을 가른 하루 평균 심박 (이 기간 중앙값 + 5bpm).

    하루 평균 심박에 소수가 있어 소수 첫째 자리까지 0.5 올림해 쓴다. 중앙값을 십진 표기로 바꿔 더해 이진 오차로
    78.05 가 78.0 이 되는 일을 방지한다.
    """
    if f.day_hr_median is None:
        return None
    return f"{round_half_up(Decimal(str(f.day_hr_median)) + HIGH_DAY_HR_BPM, 1)}bpm"


def _diff(g: GroupCompare) -> float:
    if g.diff is None:
        raise ValueError("양쪽 4일 이상인 결과에는 차이가 있다")
    return g.diff


def _present(average: float | None) -> float:
    if average is None:
        raise ValueError("양쪽 4일 이상인 결과에는 두 쪽의 평균이 있다")
    return average


def _group_nights(c: ConditionCompare) -> tuple[int, int]:
    """조건 참·거짓 그룹의 날 수. 결과마다 값이 없는 날을 빼므로 가장 많은 쪽을 쓴다."""
    groups: list[GroupCompare] = [getattr(c, key) for key, *_ in _OUTCOMES]
    return max(g.true_nights for g in groups), max(g.false_nights for g in groups)


def _good_bad(gb: GoodBadNights) -> str:
    """가장 오래 잔 날과 가장 짧게 잔 날의 그 전날 값. 운동 조건의 뜻은 앞 문장에 있어 짧은 이름만 쓴다."""
    good, bad = gb.good, gb.bad
    if not (good.enough_data and bad.enough_data):
        # 두 그룹은 늘 같은 날 수다
        return f"가장 오래 잔 날과 가장 짧게 잔 날은 각각 {good.nights}일로 {MIN_NIGHTS}일보다 적어 비교하지 않았다."
    longest, shortest = f"가장 오래 잔 {good.nights}일", f"가장 짧게 잔 {bad.nights}일"
    lines = [f"{longest}은 {_minute_range(good.sleep_range)}, {shortest}은 {_minute_range(bad.sleep_range)} 잤다."]
    changes: list[tuple[str, str, str, float, str, _Words]] = []
    if good.steps_avg is not None and bad.steps_avg is not None:
        changes.append(("전날 걸음 수", "가", "는", good.steps_avg - bad.steps_avg, "걸음", _MORE))
    if good.prev_sleep_avg is not None and bad.prev_sleep_avg is not None:
        changes.append(("앞선 수면", "이", "은", good.prev_sleep_avg - bad.prev_sleep_avg, "분", _LONGER))
    if good.bedtime_avg is not None and bad.bedtime_avg is not None:
        later = (good.bedtime_avg - bad.bedtime_avg + _DAY_MINUTES / 2) % _DAY_MINUTES - _DAY_MINUTES / 2
        changes.append(("그 잠의 잠든 시각", "이", "은", later, "분", _LATER))
    if changes:
        parts = [
            _change(subject, has, topic, diff, unit, words, i == len(changes) - 1, prefix="평균 ")
            for i, (subject, has, topic, diff, unit, words) in enumerate(changes)
        ]
        lines.append(f"{longest}은 {shortest}보다 {', '.join(parts)}.")
    ratios = []
    if good.exercise_day_pct is not None and bad.exercise_day_pct is not None:
        ratios.append(("전날 운동한 날", good.exercise_day_pct, bad.exercise_day_pct))
    if good.late_exercise_pct is not None and bad.late_exercise_pct is not None:
        ratios.append(("전날 늦게 운동한 날", good.late_exercise_pct, bad.late_exercise_pct))
    if good.weekend_pct is not None and bad.weekend_pct is not None:
        ratios.append(("토·일에 깬 날", good.weekend_pct, bad.weekend_pct))
    if ratios:
        listed = ", ".join(f"{label} {_number(a, '%')} 대 {_number(b, '%')}" for label, a, b in ratios)
        lines.append(f"{longest} 대 {shortest}의 비율은 {listed}다.")
    return " ".join(lines)


def _head(period: DateRange, nights: int, base_date: dt.date, label: str | None = None) -> str:
    """분석 기간의 첫 말 ("최근 14일(10월 9일-22일) 수면"). 기록이 없는 날이 있으면 기록한 날 수를 넣는다.

    label(질문한 기간의 이름과 날짜, "이번 주(10월 19일-22일)")이 있으면 그 이름을 쓴다. 없으면 기준일에 끝나는 2일
    이상의 기간은 "최근 N일(날짜)" 로, 다른 기간은 날짜로 쓴다. 기간의 날은 깬 날짜이고 날마다 주 수면은 하나라
    기간의 날 수와 기록한 날 수가 같으면 빠진 날이 없다 ("최근 14일(10월 9일-22일, 기록 12일) 수면").
    """
    days = (period.end - period.start).days + 1
    note = f"기록 {nights}일" if nights != days else None
    if label is not None:
        if note is None:
            return f"{label} 수면"
        return f"{label[:-1]}, {note}) 수면" if label.endswith(")") else f"{label}({note}) 수면"
    dates = range_text(period, base_date)
    recent = period.end == base_date and days > 1
    notes = [dates] if recent else []
    if note is not None:
        notes.append(note)
    name = f"최근 {days}일" if recent else dates
    return f"{name}({', '.join(notes)}) 수면" if notes else f"{name} 수면"


def _round(value: float) -> int:
    return int(round_half_up(value))


def _minutes(value: float, nights: int | None = None) -> str:
    """분을 "5시간 56분(356분)" 으로 쓰고 nights 가 있으면 "5시간 56분(356분, 14일)" 이다.

    60분 미만은 "24분" 이고 nights 가 있으면 "24분(14일)" 이다.
    """
    m = _round(value)
    notes = [f"{m}분"] if m >= 60 else []
    if nights is not None:
        notes.append(f"{nights}일")
    text = format_minutes(m)
    return f"{text}({', '.join(notes)})" if notes else text


def _minute_range(sleep_range: tuple[int, int] | None) -> str:
    if sleep_range is None:
        return "수면 시간 기록 없이"
    low, high = sleep_range
    if high < 60:
        return f"{low}~{high}분"
    return f"{format_minutes(low)}~{format_minutes(high)}({low}~{high}분)"


def _number(value: float, unit: str, nights: int | None = None) -> str:
    text = f"{_round(value)}{unit}"
    return text if nights is None else f"{text}({nights}일)"


def _pct1(value: float) -> str:
    """잠마다의 산소포화도 평균을 소수 첫째 자리까지 0.5 올림해 쓴다 ("84.4")."""
    return str(round_half_up(Decimal(str(value)), 1))


def _clock(minutes: float) -> str:
    m = _round(minutes) % _DAY_MINUTES
    return f"{m // 60:02d}:{m % 60:02d}"


def _clock_values(name: str, stat: ClockStat) -> str:
    """시각 평균과 흔들림(표본 표준편차)을 쓴다.

    "잠든 시각은 평균 01:37이고 날마다 보통 1시간 56분(116분) 안팎으로 달라진다(14일)" 형식이다. 표준편차라 날마다
    평균 몇 분씩 차이 난다고 쓰지 않는다.
    """
    if stat.avg is None or stat.sd_min is None:
        return _avg_item(name, None, stat.nights)[1]
    spread = f"날마다 보통 {_minutes(stat.sd_min)} 안팎으로 달라진다({stat.nights}일)"
    return f"{name}은 평균 {_clock(stat.avg)}이고 {spread}"


def _weekend_gap(name: str, subject: str, gap: WeekendGap) -> str:
    """주말 평균 − 평일 평균. subject 는 name 뒤에 붙는 조사(이·가)다."""
    counts = f"평일 {gap.weekday_nights}일, 주말 {gap.weekend_nights}일"
    if gap.min is None:
        need = f"평일 {MIN_WEEKDAY_NIGHTS}일·주말 {MIN_WEEKEND_NIGHTS}일 이상일 때만 내서"
        return f"주말·평일 {name} 차이는 {need} 값이 없다({counts})"
    m = _round(gap.min)
    if not m:
        return f"주말·평일 {name} 차이는 0분이다({counts})"
    return f"주말 {name}{subject} 평일보다 {_minutes(abs(m))} {'늦다' if m > 0 else '빠르다'}({counts})"


def _avg_item(name: str, value: str | None, nights: int) -> tuple[str, str]:
    """평균 값 하나를 이어 쓸 때의 말 (앞 항목, 마지막 항목). 평균이 없으면 값이 있는 날 수나 기록이 없다는 사실이다.

    0 으로 읽히지 않게 평균이 없다고 쓴다. name 은 받침으로 끝나 "은" 을 붙인다.
    """
    if value is not None:
        return f"{name}은 평균 {value}이고", f"{name}은 평균 {value}이다"
    if nights:
        stem = f"{name}은 기록이 있는 날이 {nights}일로 {MIN_NIGHTS}일보다 적어 평균을 내지 않았"
        return f"{stem}고", f"{stem}다"
    return f"{name}은 기록이 없고", f"{name}은 기록이 없다"


def _no_average(subject: str, nights: int) -> str:
    return f"{subject} 있는 날이 {nights}일로 {MIN_NIGHTS}일보다 적어 평균을 내지 않았다"


def _than(diff: float, unit: str, words: tuple[str, str], same: str = "과") -> str:
    """앞에 쓴 값과의 차이를 "보다 1시간 7분(67분) 짧다" 로 쓴다. 반올림해 0 이면 "과 차이가 없다(0분)" 이다.

    same 은 반올림해 0 일 때 앞 값 뒤에 붙는 조사(과·와)다.
    """
    m = _round(diff)
    if not m:
        return f"{same} 차이가 없다(0{unit})"
    amount = _minutes(abs(m)) if unit == "분" else f"{abs(m)}{unit}"
    return f"보다 {amount} {words[0] if m > 0 else words[1]}"


def _change(
    subject: str, has: str, topic: str, diff: float, unit: str, words: _Words, last: bool, *, prefix: str = ""
) -> str:
    """여러 차이를 한 문장으로 이을 때의 한 항목 ("수면 시간이 30분 길고", 마지막은 "수면 중 심박이 3bpm 낮았다").

    has 는 차이가 있을 때, topic 은 반올림해 0 일 때 subject 뒤에 붙는 조사다 ("수면 효율은 차이가 없고").
    """
    if not _round(diff):
        return f"{subject}{topic} {words[2][last]}"
    return f"{subject}{has} {prefix}{_amount_word(diff, unit, words, last)}"


def _amount_word(diff: float, unit: str, words: _Words, last: bool) -> str:
    """차이 하나의 양과 말 ("30분 길었고", 마지막은 "30분 길었다"). 반올림해 0 이면 "차이가 없었다" 형식이다."""
    m = _round(diff)
    up, down, same = words
    if not m:
        return same[last]
    amount = _minutes(abs(m)) if unit == "분" else f"{abs(m):,}{unit}"
    return f"{amount} {(up if m > 0 else down)[last]}"


def _before(start: dt.date, days: int) -> DateRange:
    """start 바로 앞의 days 일."""
    return DateRange(start - dt.timedelta(days=days), start - _ONE_DAY)
