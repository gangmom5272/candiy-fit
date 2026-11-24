"""수면 코칭 계산 (data/docs/calculation-rules.md 11장, 계산 세부는 11.9).

pattern 은 11.2 규칙성과 11.3 문제 유형, factors 는 11.4 조건별 비교, signals 는 11.5 이상 신호다.
sleep_vs_baseline 은 기준일까지 14일과 그 전 28일을 비교하는 11.3 줄어드는 중 값이고 pattern 도 이 값을 쓴다.
last_night_vs_week 는 어젯밤과 그 전 7일 평균을 비교하는 11.6 오늘 활동량 값이다.
포트 결과 대신 계산에 쓰는 값만 담은 입력을 받고, 주 수면만 쓴다. 반환 값은 반올림하지 않은 값이다.
판정은 반올림 전 값으로 하고, 반올림(0.5 올림)은 결과 문장을 만들 때 한다.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import pairwise
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from coach_ai.ports import ResultDay, ResultNight, ResultSession
from coach_ai.tools.periods import DEFAULT_TZ, DateRange

_KST = ZoneInfo(DEFAULT_TZ)
_DAY_MINUTES = 1440
_ONE_DAY = dt.timedelta(days=1)

# 질문에 기간이 없을 때의 기간 (깬 날짜 기준, 오늘에서 끝난다)
PATTERN_DAYS = 14
FACTORS_DAYS = 28
SIGNALS_DAYS = 14
# factors 는 이보다 짧은 기간이면 조건별 비교를 하지 않고 short_factors 로 그 기간의 수면 시간만 낸다.
# 11.4 의 기본 비교 기간은 28일이고, 14일이면 조건마다 4밤이 모자라는 일이 많다
FACTORS_MIN_DAYS = 14
# 본인 기준 기간. 분석 기간 시작 전 28일이다
BASELINE_DAYS = 28
# 줄어드는 중(11.3)의 최근 기간. 질문 기간과 상관없이 기준일까지 14일이다
DECLINING_RECENT_DAYS = 14
# 11.6 오늘 활동량의 어젯밤 비교 기간 (잠정). 기준일 전날까지 7일이고 어젯밤은 넣지 않는다
LAST_NIGHT_WEEK_DAYS = 7
# 11.2 최소 밤 수(9장 "유효한 주 = 4일")이자 11.4 enough_data 기준
MIN_NIGHTS = 4
MIN_WEEKDAY_NIGHTS = 3
MIN_WEEKEND_NIGHTS = 2
# 4.1 reliable 의 최소 기록 일수
RELIABLE_MIN_DAYS = 3

# 11.3 문제 유형 기준 (잠정)
SHORT_SLEEP_MINUTES = 420
LONG_SLEEP_MINUTES = 540
DECLINING_MINUTES = 60
IRREGULAR_MINUTES = 60
FRAGMENTED_AWAKE_MINUTES = 30
FRAGMENTED_EFFICIENCY_PCT = 85
LOW_DEEP_CHANGE_PCT = -20
LOW_DEEP_BASELINE_NIGHTS = 7

# 11.4 조건 기준 (잠정)
LATE_EXERCISE = dt.timedelta(hours=3)
SHORT_PREV_NIGHT_MINUTES = 60
LATE_BEDTIME_MINUTES = 60
HIGH_DAY_HR_BPM = 5

# 11.5 이상 신호 기준 (잠정)
SPO2_REPEAT_NIGHTS = 3
SPO2_TIMES_SHOWN = 5
SLEEP_HR_RECENT_DAYS = 7
SLEEP_HR_HIGH_BPM = 5
SHORT_STREAK_MINUTES = 300
SHORT_STREAK_NIGHTS = 3

Condition = Literal[
    "exercise",
    "late_exercise",
    "high_steps",
    "short_prev_night",
    "late_bedtime_streak",
    "high_day_hr",
    "weekend",
]
CONDITIONS: tuple[Condition, ...] = get_args(Condition)
# 운동으로 나눈 조건. 요청한 기간에 이 조건의 비교가 하나도 안 되면 처리 함수가 최근 28일로 운동 비교를 계산한다
EXERCISE_CONDITIONS: tuple[Condition, ...] = ("exercise", "late_exercise")
ProblemType = Literal["short", "long", "declining", "irregular", "fragmented", "low_deep"]
# nights_below_4: 값을 낼 밤이 모자람, not_reliable: 줄어드는 중의 두 기간 중 하나가 4.1 reliable 이 아님,
# baseline_below_7: 깊은 잠 본인 기준 밤이 7밤 미만
NotJudgedReason = Literal["nights_below_4", "not_reliable", "baseline_below_7"]


@dataclass(frozen=True, slots=True)
class NightRecord:
    """수면 한 번 (sleep_session). date 는 깬 날짜(KST)이고, 주 수면(is_main)만 계산에 쓴다."""

    date: dt.date
    start_at: dt.datetime
    end_at: dt.datetime
    # 잠든 시각부터 깬 시각까지의 분. 나뉜 수면 조각 사이 간격도 들어 있다
    session_minutes: int
    sleep_minutes: int | None
    awake_minutes: int | None
    deep_minutes: int | None
    sleep_hr_avg: float | None
    # 90% 미만 기록 수. None 이면 그 수면 중 산소포화도 기록이 없다
    spo2_low_count: int | None
    # 90% 미만 기록 시각. 기록 수가 있는데 None 이면 시각 칸이 생기기 전에 집계한 행이라 기록 수만 쓴다
    spo2_low_times: tuple[dt.datetime, ...] | None
    is_main: bool
    # 그 수면 중 산소포화도 평균(%). 측정이 없으면 None 이다
    spo2_avg: float | None = None

    def __post_init__(self) -> None:
        if any(t.tzinfo is None for t in (self.start_at, self.end_at, *(self.spo2_low_times or ()))):
            raise ValueError("수면 시각에 시간대 정보가 없다")
        if self.start_at >= self.end_at:
            raise ValueError("잠든 시각은 깬 시각보다 앞서야 한다")
        if self.end_at.astimezone(_KST).date() != self.date:
            raise ValueError("수면 date 는 깬 시각의 KST 날짜여야 한다")
        if self.spo2_low_times is not None and len(self.spo2_low_times) != self.spo2_low_count:
            raise ValueError("90% 미만 기록 시각 수가 기록 수와 다르다")


@dataclass(frozen=True, slots=True)
class DayRecord:
    """조건 날짜 D 하루의 낮 기록 (11.4)."""

    date: dt.date
    # 그날 활동 행(activity_daily)이 있는지. 걸음이 NULL 이거나 0보여도 행이 있으면 True 다
    has_activity_row: bool
    steps: int | None
    # 걸음 결측 (행 없음, NULL, 0보. 2.1)
    steps_missing: bool
    # 하루 평균 심박(heart_daily.hr_avg). 측정이 없으면 None 이다
    day_hr_avg: float | None
    # 충분히 착용한 날(9.1 is_well_worn)인지. None 은 모름이다: 포트에 착용 값이 없거나 9.1 에서 판단을 보류한 날.
    # 모르는 날은 착용이 짧은 날처럼 낮 심박 조건의 그룹과 중앙값에서 빠진다
    well_worn: bool | None
    # 그날 시작한 인정 운동(counts_as_exercise)의 끝난 시각
    exercise_ends: tuple[dt.datetime, ...] = ()

    def __post_init__(self) -> None:
        if not self.steps_missing and (not self.has_activity_row or self.steps is None or self.steps <= 0):
            raise ValueError("결측이 아닌 걸음은 활동 행이 있고 1 이상이어야 한다")
        if any(t.tzinfo is None for t in self.exercise_ends):
            raise ValueError("운동 끝난 시각에 시간대 정보가 없다")


@dataclass(frozen=True, slots=True)
class SleepCoachingInput:
    """계산 입력. today 는 질문한 날짜(KST)다."""

    today: dt.date
    # 연동 시작일 (1.3 linked_from). 기록이 하나도 없으면 None 이다
    linked_from: dt.date | None
    # 동기화 기준 시각 (1.3 synced_until)
    synced_until: dt.datetime | None
    nights: tuple[NightRecord, ...]
    days: tuple[DayRecord, ...] = ()

    def __post_init__(self) -> None:
        mains = [n.date for n in self.nights if n.is_main]
        if len(mains) != len(set(mains)):
            raise ValueError("깬 날짜 하나에 주 수면이 둘 이상이다")
        if len({d.date for d in self.days}) != len(self.days):
            raise ValueError("같은 날짜의 낮 기록이 둘 이상이다")
        if self.synced_until is not None and self.synced_until.tzinfo is None:
            raise ValueError("synced_until 에 시간대 정보가 없다")


# 행이 있지만 걸음 값이 결측인 사유 (2.1). 행이 없는 날(NO_RECORD)과 구분한다
_ROW_REASONS = ("NULL_VALUE", "ZERO_STEPS")


def day_records(
    steps: Sequence[ResultDay],
    heart: Sequence[ResultDay],
    exercise_ends: Mapping[dt.date, Sequence[dt.datetime]] | None = None,
) -> tuple[DayRecord, ...]:
    """걸음·심박 조회 결과의 날짜별 값을 조건 날짜의 낮 기록으로 바꾼다. 두 결과의 날짜가 같아야 한다.

    활동 행은 걸음 값이 있거나 결측 사유가 NULL_VALUE·ZERO_STEPS 인 날에 있다. 아직 안 들어옴·연동 전인 날은
    11.4 비교에서 빠지는 날이라 행이 없는 날로 둔다. 착용 값은 심박 결과의 is_well_worn 을 그대로 쓴다.
    null 은 모름(None)이고 충분히 착용한 날이나 착용이 짧은 날로 바꾸지 않는다.
    exercise_ends 는 날짜별 인정 운동의 끝난 시각이다.
    """
    if [d.date for d in steps] != [d.date for d in heart]:
        raise ValueError("걸음·심박 결과의 날짜가 다르다")
    ends = exercise_ends or {}
    records = []
    for s, h in zip(steps, heart, strict=True):
        row = (s.reason is None and s.value is not None) or s.reason in _ROW_REASONS
        records.append(
            DayRecord(
                date=s.date,
                has_activity_row=row,
                steps=None if not row or s.value is None else int(s.value),
                steps_missing=s.missing or not row,
                day_hr_avg=h.value if h.reason is None else None,
                well_worn=h.is_well_worn,
                exercise_ends=tuple(ends.get(s.date, ())),
            )
        )
    return tuple(records)


def night_records(nights: Sequence[ResultNight]) -> tuple[NightRecord, ...]:
    """수면 조회 결과의 주 수면 상세(detail.nights)를 계산 입력의 수면으로 바꾼다.

    session_minutes 는 연동 쪽 sleep_session.session_minutes 와 같은 계산(잠든 시각부터 깬 시각까지의 분을 반올림)이다.
    """
    return tuple(
        NightRecord(
            date=n.date,
            start_at=n.start_at,
            end_at=n.end_at,
            session_minutes=round((n.end_at - n.start_at).total_seconds() / 60),
            sleep_minutes=n.sleep_minutes,
            awake_minutes=n.awake_minutes,
            deep_minutes=n.deep_minutes,
            sleep_hr_avg=n.avg_heart_rate,
            spo2_low_count=n.spo2_low_count,
            spo2_low_times=n.spo2_low_times,
            is_main=True,
            spo2_avg=n.spo2_avg,
        )
        for n in nights
    )


def exercise_ends(sessions: Sequence[ResultSession]) -> dict[dt.date, list[dt.datetime]]:
    """운동 조회 결과의 세션 중 인정 운동의 끝난 시각을 시작한 날짜(KST)별로 모은다.

    끝난 시각은 시작 시각 + 운동 시간이다. 운동 시간은 끝 − 시작을 분으로 반올림한 값이라 30초 안쪽으로 다를 수 있다.
    """
    ends: dict[dt.date, list[dt.datetime]] = {}
    for s in sessions:
        if s.counts_as_exercise:
            ends.setdefault(s.date, []).append(s.start_at + dt.timedelta(minutes=s.duration_minutes))
    return ends


# 아래 출력 모델의 칸 이름은 수면 코칭 도구 정의 초안 3장을 따른다


@dataclass(frozen=True, slots=True)
class ClockStat:
    # 평균 시각은 KST 0시부터의 분(0 이상 1440 미만), 흔들림은 표본 표준편차(분)다
    avg: float | None
    sd_min: float | None
    nights: int


@dataclass(frozen=True, slots=True)
class WeekendGap:
    # 주말 평균 − 평일 평균 (분, 주말이 늦으면 +)
    min: float | None
    weekday_nights: int
    weekend_nights: int


@dataclass(frozen=True, slots=True)
class Average:
    value: float | None
    nights: int


@dataclass(frozen=True, slots=True)
class Spo2LowNights:
    # 90% 미만 기록이 있는 밤 수와 산소포화도가 기록된 밤 수
    low_nights: int
    recorded_nights: int


@dataclass(frozen=True, slots=True)
class SleepVsBaseline:
    """줄어드는 중(11.3)에 쓴 값. 기준일까지 14일과 그 전 28일을 비교하고, 해당이 없어도 넣는다 (11.9)."""

    recent_avg: float | None
    baseline_avg: float | None
    diff: float | None
    recent_nights: int
    baseline_nights: int
    recent_reliable: bool
    baseline_reliable: bool
    # 줄어드는 중 해당 여부. 두 기간 중 하나가 reliable 이 아니면 판정하지 않아 None 이다
    declining: bool | None


@dataclass(frozen=True, slots=True)
class DeepRatioVsBaseline:
    """깊은 잠 적음(11.3)에 쓴 값. change_pct 는 상대 변화(%)다 (11.9)."""

    recent_avg_pct: float | None
    baseline_avg_pct: float | None
    change_pct: float | None
    recent_nights: int
    baseline_nights: int


@dataclass(frozen=True, slots=True)
class LastNightValue:
    """어젯밤 값과 그 전 7일 평균. 그 전 7일에 값이 있는 밤이 4밤 미만이면 평균과 차이는 None 이다."""

    last_night: float | None
    week_avg: float | None
    # 어젯밤 − 그 전 7일 평균
    diff: float | None
    # 평균에 쓴 밤 수
    week_nights: int


@dataclass(frozen=True, slots=True)
class LastNightVsWeek:
    """오늘 활동량(11.6)에 쓰는 어젯밤 비교. 어젯밤은 기준일에 깬 주 수면이고 평균에는 넣지 않는다."""

    sleep_minutes: LastNightValue
    sleep_hr_avg: LastNightValue
    # 기준일에 깬 주 수면의 잠든 시각. 결과 문장에서 "어젯밤"과 "오늘 수면"을 가를 때 쓰고, 기록이 없으면 None 이다
    start_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ShortProblem:
    type: Literal["short"] = "short"
    value: float
    threshold: int = SHORT_SLEEP_MINUTES
    nights: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LongProblem:
    type: Literal["long"] = "long"
    value: float
    threshold: int = LONG_SLEEP_MINUTES
    nights: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DecliningProblem:
    type: Literal["declining"] = "declining"
    # 최근 평균 − 본인 기준 평균 (분)
    value: float
    baseline: float
    threshold: int = -DECLINING_MINUTES
    nights: int
    baseline_nights: int


@dataclass(frozen=True, slots=True, kw_only=True)
class IrregularProblem:
    type: Literal["irregular"] = "irregular"
    bedtime_sd_min: float | None
    midpoint_gap_min: float | None
    threshold: int = IRREGULAR_MINUTES
    nights: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FragmentedProblem:
    type: Literal["fragmented"] = "fragmented"
    awake_avg_min: float | None
    efficiency_avg_pct: float | None
    nights: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LowDeepProblem:
    type: Literal["low_deep"] = "low_deep"
    value: float
    baseline: float
    change_pct: float
    nights: int
    baseline_nights: int


Problem = ShortProblem | LongProblem | DecliningProblem | IrregularProblem | FragmentedProblem | LowDeepProblem


@dataclass(frozen=True, slots=True)
class NotJudged:
    """판정하지 못한 유형. type 이 all 이면 기간 전체가 4밤 미만이라 아무 유형도 판정하지 않았다."""

    type: ProblemType | Literal["all"]
    reason: NotJudgedReason
    nights: int | None = None
    baseline_nights: int | None = None


@dataclass(frozen=True, slots=True)
class PatternResult:
    analysis: Literal["pattern"] = field(default="pattern", init=False)
    period: DateRange
    baseline_period: DateRange
    nights: int
    enough_data: bool
    bedtime: ClockStat
    waketime: ClockStat
    midpoint_gap_min: WeekendGap
    waketime_gap_min: WeekendGap
    sleep_avg_min: Average
    efficiency_avg_pct: Average
    awake_avg_min: Average
    deep_ratio_avg_pct: Average
    spo2_low_nights: Spo2LowNights
    sleep_vs_baseline: SleepVsBaseline
    deep_ratio_vs_baseline: DeepRatioVsBaseline
    problems: tuple[Problem, ...]
    not_judged: tuple[NotJudged, ...]


@dataclass(frozen=True, slots=True)
class GroupCompare:
    """조건 참·거짓 그룹의 결과 하나. diff 는 참 − 거짓이고 한쪽이 0밤이면 None 이다."""

    true_avg: float | None
    true_nights: int
    false_avg: float | None
    false_nights: int
    diff: float | None
    enough_data: bool


@dataclass(frozen=True, slots=True)
class ConditionCompare:
    condition: Condition
    # 조건 날짜 중 어느 그룹에도 넣지 않은 날 수
    excluded_days: int
    sleep_minutes: GroupCompare
    deep_minutes: GroupCompare
    efficiency_pct: GroupCompare
    awake_minutes: GroupCompare
    sleep_hr_avg: GroupCompare


@dataclass(frozen=True, slots=True)
class NightGroup:
    """잘 잔 밤 또는 못 잔 밤. 비율·평균은 그 밤의 전날(D) 값이고, bedtime_avg 는 그 밤에 잠든 시각의 평균이다."""

    nights: int
    # 그룹 밤의 가장 짧은·긴 수면 시간 (분)
    sleep_range: tuple[int, int] | None
    exercise_day_pct: float | None
    late_exercise_pct: float | None
    steps_avg: float | None
    prev_sleep_avg: float | None
    bedtime_avg: float | None
    weekend_pct: float | None
    enough_data: bool


@dataclass(frozen=True, slots=True)
class GoodBadNights:
    good: NightGroup
    bad: NightGroup


@dataclass(frozen=True, slots=True)
class FactorsResult:
    analysis: Literal["factors"] = field(default="factors", init=False)
    period: DateRange
    nights: int
    enough_data: bool
    conditions: tuple[ConditionCompare, ...]
    # 조건 하나만 비교하면 None 이다
    good_bad_nights: GoodBadNights | None
    # 걸음 많은 날·낮 심박 높은 날을 가른 비교 기간 중앙값 (11.9). 값이 있는 조건 날짜가 없으면 None 이다
    steps_median: float | None
    day_hr_median: float | None


@dataclass(frozen=True, slots=True)
class ShortFactorsResult:
    """FACTORS_MIN_DAYS 보다 짧은 기간의 factors. 조건별 비교 대신 그 기간에 깬 주 수면의 밤별 수면 시간과
    가장 오래 잔 밤·가장 짧게 잔 밤이다. 다른 기간의 값은 들어가지 않는다."""

    period: DateRange
    # (깬 날짜, 수면 분). 깬 날짜 순이고 수면 분을 모르면 None 이다
    nights: tuple[tuple[dt.date, int | None], ...]
    # 수면 분을 아는 밤 가운데 가장 오래·짧게 잔 밤 (깬 날짜, 수면 분). 값이 같으면 먼저 깬 밤이고, 없으면 None 이다
    longest: tuple[dt.date, int] | None
    shortest: tuple[dt.date, int] | None
    # 수면 분을 아는 밤 수
    known_nights: int
    # (깬 날짜, 잠든 시각). 결과 문장에서 오늘 깬 잠을 "어젯밤"·"오늘 수면"으로 부를 때 쓴다
    starts: tuple[tuple[dt.date, dt.datetime], ...] = ()


@dataclass(frozen=True, slots=True)
class Spo2LowRepeat:
    hit: bool
    low_nights: int
    low_records: int
    low_dates: tuple[dt.date, ...]
    # 이른 순 5개. 90% 미만 기록이 있는 밤 중 시각이 없는 밤이 있으면 None 이고 기록 수만 쓴다
    times: tuple[dt.datetime, ...] | None
    # times 와 같은 순서로, 각 시각이 기록된 잠의 잠든 시각
    slept_at: tuple[dt.datetime, ...] | None
    more: int | None
    recorded_nights: int
    # times 와 같은 순서로, 각 시각이 기록된 잠의 깬 날짜. 결과 문장에서 그 잠을 깬 날로 부를 때 쓴다
    woke_on: tuple[dt.date, ...] | None = None


@dataclass(frozen=True, slots=True)
class NightValue:
    """잠 하나의 값. date 는 깬 날짜이고 start_at 은 잠든 시각이다."""

    value: float
    date: dt.date
    start_at: dt.datetime


@dataclass(frozen=True, slots=True)
class Spo2AvgRange:
    """밤별 산소포화도 평균(주 수면의 spo2_avg)이 가장 낮은 밤과 가장 높은 밤. 값이 같으면 먼저 깬 밤이다.

    nights 는 평균 값이 있는 밤 수다. 값이 있는 밤이 없으면 lowest·highest 는 None 이다.
    """

    lowest: NightValue | None
    highest: NightValue | None
    nights: int


@dataclass(frozen=True, slots=True)
class SleepHrHigh:
    hit: bool
    recent_avg: float | None
    recent_nights: int
    baseline_avg: float | None
    baseline_nights: int
    diff: float | None


@dataclass(frozen=True, slots=True)
class ShortSleepStreak:
    hit: bool
    # 가장 긴 연속 밤 수와 그 밤들의 깬 날짜. 길이가 같으면 먼저 시작한 연속이다
    longest: int
    dates: tuple[dt.date, ...]


@dataclass(frozen=True, slots=True)
class SignalsResult:
    analysis: Literal["signals"] = field(default="signals", init=False)
    period: DateRange
    # 수면 중 심박 기준선 기간. 최근 7일 앞의 28일이다
    baseline_period: DateRange
    nights: int
    enough_data: bool
    spo2_low_repeat: Spo2LowRepeat
    sleep_hr_high: SleepHrHigh
    short_sleep_streak: ShortSleepStreak
    spo2_avg_range: Spo2AvgRange


def sleep_vs_baseline(data: SleepCoachingInput, base_date: dt.date) -> SleepVsBaseline:
    """기준일까지 14일과 그 전 28일의 평균 수면 시간 비교 (11.3 줄어드는 중).

    두 기간은 질문 기간이 아니라 기준일로 정한다. 평균은 4.1 기간 비교처럼 밤 수가 적어도 내고,
    줄어드는 중은 두 기간이 모두 4.1 reliable 일 때만 판정한다.
    """
    if base_date > data.today:
        raise ValueError("기준일은 오늘을 넘지 않아야 한다")
    recent = DateRange(base_date - dt.timedelta(days=DECLINING_RECENT_DAYS - 1), base_date)
    baseline = _before(recent.start, BASELINE_DAYS)
    mains = _main_nights(data)
    recent_avg, recent_n = _mean(n.sleep_minutes for n in _within(mains, recent))
    base_avg, base_n = _mean(n.sleep_minutes for n in _within(mains, baseline))
    recent_reliable = _reliable(recent_n, _completed_days(data.linked_from, recent))
    baseline_reliable = _reliable(base_n, _completed_days(data.linked_from, baseline))
    diff = _minus(recent_avg, base_avg)
    declining = None
    if recent_reliable and baseline_reliable and diff is not None:
        declining = diff <= -DECLINING_MINUTES
    return SleepVsBaseline(
        recent_avg=_float(recent_avg),
        baseline_avg=_float(base_avg),
        diff=_float(diff),
        recent_nights=recent_n,
        baseline_nights=base_n,
        recent_reliable=recent_reliable,
        baseline_reliable=baseline_reliable,
        declining=declining,
    )


def last_night_vs_week(data: SleepCoachingInput, base_date: dt.date) -> LastNightVsWeek:
    """기준일에 깬 주 수면(어젯밤)과 기준일 전날까지 7일의 주 수면 평균 비교 (11.6 오늘 활동량).

    수면 시간과 수면 중 심박을 같은 방식으로 비교하고, 평균에는 어젯밤을 넣지 않는다.
    """
    if base_date > data.today:
        raise ValueError("기준일은 오늘을 넘지 않아야 한다")
    mains = _main_nights(data)
    last = mains.get(base_date)
    week = _within(mains, _before(base_date, LAST_NIGHT_WEEK_DAYS))
    return LastNightVsWeek(
        sleep_minutes=_last_night_value(None if last is None else last.sleep_minutes, [n.sleep_minutes for n in week]),
        sleep_hr_avg=_last_night_value(None if last is None else last.sleep_hr_avg, [n.sleep_hr_avg for n in week]),
        start_at=None if last is None else last.start_at,
    )


def pattern(data: SleepCoachingInput, period: DateRange | None = None) -> PatternResult:
    """11.2 규칙성과 11.3 문제 유형. period 가 없으면 오늘까지 14일이다.

    줄어드는 중은 기간 끝을 기준일로 한 sleep_vs_baseline 값으로 판정한다.
    """
    period = _period(data, period, PATTERN_DAYS)
    baseline = _before(period.start, BASELINE_DAYS)
    mains = _main_nights(data)
    nights = _within(mains, period)
    base_nights = _within(mains, baseline)
    enough = len(nights) >= MIN_NIGHTS
    vs_baseline = sleep_vs_baseline(data, period.end)

    bedtime = _clock_stat([_clock_minutes(n.start_at) for n in nights])
    waketime = _clock_stat([_clock_minutes(n.end_at) for n in nights])
    # 가운데 시각 = 잠든 시각 + 세션 길이 ÷ 2 (11.1)
    midpoint_gap = _weekend_gap(nights, lambda n: _clock_minutes(n.start_at) + Fraction(n.session_minutes, 2))
    waketime_gap = _weekend_gap(nights, lambda n: _clock_minutes(n.end_at))
    sleep, sleep_n = _average(n.sleep_minutes for n in nights)
    efficiency, efficiency_n = _average(_efficiency(n) for n in nights)
    awake, awake_n = _average(n.awake_minutes for n in nights)
    deep, deep_n = _average(_deep_ratio(n) for n in nights)
    base_deep, base_deep_n = _mean(_deep_ratio(n) for n in base_nights)
    # 깊은 잠 비율의 상대 변화 (%포인트가 아니다, 11.9)
    deep_change = None if deep is None or not base_deep else (deep - base_deep) / base_deep * 100

    problems: list[Problem] = []
    not_judged: list[NotJudged] = []
    if not enough:
        not_judged.append(NotJudged(type="all", reason="nights_below_4", nights=len(nights)))
    else:
        # 평균 수면 시간이 없으면 짧음·긴 수면 모두 판정하지 않는다. not_judged 에는 short 하나로 둔다
        if sleep is None:
            not_judged.append(NotJudged(type="short", reason="nights_below_4", nights=sleep_n))
        elif sleep < SHORT_SLEEP_MINUTES:
            problems.append(ShortProblem(value=float(sleep), nights=sleep_n))
        elif sleep >= LONG_SLEEP_MINUTES:
            problems.append(LongProblem(value=float(sleep), nights=sleep_n))

        if vs_baseline.declining is None:
            not_judged.append(
                NotJudged(
                    type="declining",
                    reason="not_reliable",
                    nights=vs_baseline.recent_nights,
                    baseline_nights=vs_baseline.baseline_nights,
                )
            )
        elif vs_baseline.declining and vs_baseline.diff is not None and vs_baseline.baseline_avg is not None:
            problems.append(
                DecliningProblem(
                    value=vs_baseline.diff,
                    baseline=vs_baseline.baseline_avg,
                    nights=vs_baseline.recent_nights,
                    baseline_nights=vs_baseline.baseline_nights,
                )
            )

        # 4밤 이상이면 잠든 시각 흔들림은 항상 있다. 평일·주말 차이가 없으면 흔들림으로만 판정한다
        if (bedtime.sd_min is not None and bedtime.sd_min >= IRREGULAR_MINUTES) or (
            midpoint_gap.min is not None and abs(midpoint_gap.min) >= IRREGULAR_MINUTES
        ):
            problems.append(
                IrregularProblem(bedtime_sd_min=bedtime.sd_min, midpoint_gap_min=midpoint_gap.min, nights=len(nights))
            )

        if awake is None and efficiency is None:
            not_judged.append(NotJudged(type="fragmented", reason="nights_below_4", nights=awake_n))
        elif (awake is not None and awake > FRAGMENTED_AWAKE_MINUTES) or (
            efficiency is not None and efficiency < FRAGMENTED_EFFICIENCY_PCT
        ):
            problems.append(
                FragmentedProblem(awake_avg_min=_float(awake), efficiency_avg_pct=_float(efficiency), nights=awake_n)
            )

        if base_deep_n < LOW_DEEP_BASELINE_NIGHTS:
            not_judged.append(
                NotJudged(type="low_deep", reason="baseline_below_7", nights=deep_n, baseline_nights=base_deep_n)
            )
        elif deep is None:
            not_judged.append(
                NotJudged(type="low_deep", reason="nights_below_4", nights=deep_n, baseline_nights=base_deep_n)
            )
        elif deep_change is not None and deep_change <= LOW_DEEP_CHANGE_PCT:
            problems.append(
                LowDeepProblem(
                    value=float(deep),
                    baseline=float(base_deep),
                    change_pct=float(deep_change),
                    nights=deep_n,
                    baseline_nights=base_deep_n,
                )
            )

    return PatternResult(
        period=period,
        baseline_period=baseline,
        nights=len(nights),
        enough_data=enough,
        bedtime=bedtime,
        waketime=waketime,
        midpoint_gap_min=midpoint_gap,
        waketime_gap_min=waketime_gap,
        sleep_avg_min=Average(value=_float(sleep), nights=sleep_n),
        efficiency_avg_pct=Average(value=_float(efficiency), nights=efficiency_n),
        awake_avg_min=Average(value=_float(awake), nights=awake_n),
        deep_ratio_avg_pct=Average(value=_float(deep), nights=deep_n),
        spo2_low_nights=Spo2LowNights(
            low_nights=sum(1 for n in nights if n.spo2_low_count),
            recorded_nights=sum(1 for n in nights if n.spo2_low_count is not None),
        ),
        sleep_vs_baseline=vs_baseline,
        deep_ratio_vs_baseline=DeepRatioVsBaseline(
            recent_avg_pct=_float(deep),
            baseline_avg_pct=_float(base_deep),
            change_pct=_float(deep_change),
            recent_nights=deep_n,
            baseline_nights=base_deep_n,
        ),
        problems=tuple(problems),
        not_judged=tuple(not_judged),
    )


def factors(
    data: SleepCoachingInput, period: DateRange | None = None, condition: Condition | None = None
) -> FactorsResult:
    """11.4 조건별 비교. period 가 없으면 오늘까지 28일이고, condition 이 없으면 일곱 조건과 잘 잔 밤·못 잔 밤이다."""
    if condition is not None and condition not in CONDITIONS:
        raise ValueError(f"알 수 없는 조건: {condition}")
    period = _period(data, period, FACTORS_DAYS)
    mains = _main_nights(data)
    nights = _within(mains, period)
    days = {d.date: d for d in data.days}
    synced_day = None if data.synced_until is None else data.synced_until.astimezone(_KST).date()

    # 조건 날짜 D 는 분석 기간 각 깬 날의 전날이고, 그날 밤 잠은 D+1 에 깬 주 수면이다
    condition_days = [d - _ONE_DAY for d in period.days()]
    compared = [d for d in condition_days if _comparable(days.get(d), data.linked_from, synced_day)]
    # 수면 시간·잠든 시각 평균은 분석 기간의 주 수면으로, 걸음·낮 심박 중앙값은 조건 날짜 D 의 값으로 낸다 (11.9)
    sleep_avg, _ = _mean(n.sleep_minutes for n in nights)
    bed_avg = _clock_mean([_clock_minutes(n.start_at) for n in nights])
    steps_median = _median(days[d].steps for d in compared if not days[d].steps_missing)
    hr_median = _median(
        days[d].day_hr_avg for d in compared if days[d].well_worn is True and days[d].day_hr_avg is not None
    )

    def late_bed(night: NightRecord | None) -> bool | None:
        if night is None or bed_avg is None:
            return None
        return _clock_diff(_clock_minutes(night.start_at), bed_avg) >= LATE_BEDTIME_MINUTES

    def holds(name: Condition, d: dt.date) -> bool | None:
        """D 의 조건 참·거짓. 어느 쪽에도 넣을 수 없으면 None 이다."""
        day = days[d]
        match name:
            case "exercise":
                return bool(day.exercise_ends)
            case "late_exercise":
                night = mains.get(d + _ONE_DAY)
                if not day.exercise_ends or night is None:
                    return None
                return _late_exercise(day.exercise_ends, night)
            case "high_steps":
                if day.steps is None or day.steps_missing or steps_median is None:
                    return None
                return day.steps >= steps_median
            case "short_prev_night":
                prev = mains.get(d)
                if prev is None or prev.sleep_minutes is None or sleep_avg is None:
                    return None
                return sleep_avg - prev.sleep_minutes >= SHORT_PREV_NIGHT_MINUTES
            case "late_bedtime_streak":
                # 하나만 늦은 날은 제외한다
                late, late_before = late_bed(mains.get(d)), late_bed(mains.get(d - _ONE_DAY))
                return late if late is not None and late == late_before else None
            case "high_day_hr":
                if day.well_worn is not True or day.day_hr_avg is None or hr_median is None:
                    return None
                return _exact(day.day_hr_avg) - hr_median >= HIGH_DAY_HR_BPM
            case "weekend":
                return (d + _ONE_DAY).weekday() >= 5
        raise ValueError(f"알 수 없는 조건: {name}")

    compares = []
    for name in (condition,) if condition is not None else CONDITIONS:
        groups: dict[bool, list[NightRecord]] = {True: [], False: []}
        for d in compared:
            value = holds(name, d)
            night = mains.get(d + _ONE_DAY)
            if value is not None and night is not None:
                groups[value].append(night)
        compares.append(
            ConditionCompare(
                condition=name,
                excluded_days=len(condition_days) - len(groups[True]) - len(groups[False]),
                **{key: _compare(groups[True], groups[False], value_of) for key, value_of in _OUTCOMES.items()},
            )
        )

    return FactorsResult(
        period=period,
        nights=len(nights),
        enough_data=len(nights) >= MIN_NIGHTS,
        conditions=tuple(compares),
        good_bad_nights=None if condition is not None else _good_bad(nights, mains, days),
        steps_median=_float(steps_median),
        day_hr_median=_float(hr_median),
    )


def compares_exercise(result: FactorsResult) -> bool:
    """운동 조건(운동한 날, 늦은 운동) 가운데 양쪽 모두 4밤 이상인 결과가 하나라도 있는지 반환한다."""
    return any(
        getattr(c, key).enough_data
        for c in result.conditions
        if c.condition in EXERCISE_CONDITIONS
        for key in _OUTCOMES
    )


def short_factors(data: SleepCoachingInput, period: DateRange) -> ShortFactorsResult:
    """14일보다 짧은 기간의 factors. 그 기간에 깬 주 수면의 밤별 수면 시간과 가장 오래·짧게 잔 밤이다."""
    nights = _within(_main_nights(data), period)
    known = [(n.date, n.sleep_minutes) for n in nights if n.sleep_minutes is not None]
    return ShortFactorsResult(
        period=period,
        nights=tuple((n.date, n.sleep_minutes) for n in nights),
        longest=min(known, key=lambda x: (-x[1], x[0])) if known else None,
        shortest=min(known, key=lambda x: (x[1], x[0])) if known else None,
        known_nights=len(known),
        starts=tuple((n.date, n.start_at) for n in nights),
    )


def signals(data: SleepCoachingInput, period: DateRange | None = None) -> SignalsResult:
    """11.5 이상 신호. period 가 없으면 오늘까지 14일이다.

    수면 중 심박은 기간 끝까지 7일과 그 앞 28일(기준선)을 비교한다.
    """
    period = _period(data, period, SIGNALS_DAYS)
    mains = _main_nights(data)
    nights = _within(mains, period)
    recent = DateRange(period.end - dt.timedelta(days=SLEEP_HR_RECENT_DAYS - 1), period.end)
    baseline = _before(recent.start, BASELINE_DAYS)

    low = [n for n in nights if n.spo2_low_count]
    records = sum(n.spo2_low_count or 0 for n in low)
    times = slept_at = woke_on = None
    if all(n.spo2_low_times is not None for n in low):
        shown = sorted((t, n.start_at, n.date) for n in low for t in n.spo2_low_times or ())[:SPO2_TIMES_SHOWN]
        times, slept_at, woke_on = (tuple(x[i] for x in shown) for i in range(3))
    recent_hr, recent_n = _mean(n.sleep_hr_avg for n in _within(mains, recent))
    base_hr, base_n = _mean(n.sleep_hr_avg for n in _within(mains, baseline))
    hr_diff = _minus(recent_hr, base_hr)
    streak = _longest_short_streak(mains, period)
    measured = [n for n in nights if n.spo2_avg is not None]
    lowest = min(measured, key=lambda n: (n.spo2_avg or 0, n.date), default=None)
    highest = min(measured, key=lambda n: (-(n.spo2_avg or 0), n.date), default=None)

    return SignalsResult(
        period=period,
        baseline_period=baseline,
        nights=len(nights),
        enough_data=len(nights) >= MIN_NIGHTS,
        spo2_low_repeat=Spo2LowRepeat(
            hit=len(low) >= SPO2_REPEAT_NIGHTS,
            low_nights=len(low),
            low_records=records,
            low_dates=tuple(n.date for n in low),
            times=times,
            slept_at=slept_at,
            more=None if times is None else records - len(times),
            recorded_nights=sum(1 for n in nights if n.spo2_low_count is not None),
            woke_on=woke_on,
        ),
        sleep_hr_high=SleepHrHigh(
            hit=hr_diff is not None and hr_diff > SLEEP_HR_HIGH_BPM,
            recent_avg=_float(recent_hr),
            recent_nights=recent_n,
            baseline_avg=_float(base_hr),
            baseline_nights=base_n,
            diff=_float(hr_diff),
        ),
        short_sleep_streak=ShortSleepStreak(
            hit=len(streak) >= SHORT_STREAK_NIGHTS, longest=len(streak), dates=tuple(streak)
        ),
        spo2_avg_range=Spo2AvgRange(lowest=_night_value(lowest), highest=_night_value(highest), nights=len(measured)),
    )


def _night_value(night: NightRecord | None) -> NightValue | None:
    if night is None or night.spo2_avg is None:
        return None
    return NightValue(value=night.spo2_avg, date=night.date, start_at=night.start_at)


def _period(data: SleepCoachingInput, period: DateRange | None, days: int) -> DateRange:
    if period is None:
        return DateRange(data.today - dt.timedelta(days=days - 1), data.today)
    if not period.start <= period.end <= data.today:
        raise ValueError("기간은 start <= end <= 오늘이어야 한다")
    return period


def _before(start: dt.date, days: int) -> DateRange:
    """start 바로 앞의 days 일. 끝은 start 전날이다."""
    return DateRange(start - dt.timedelta(days=days), start - _ONE_DAY)


def _main_nights(data: SleepCoachingInput) -> dict[dt.date, NightRecord]:
    return {n.date: n for n in data.nights if n.is_main}


def _within(mains: dict[dt.date, NightRecord], period: DateRange) -> list[NightRecord]:
    """기간 안에 깬 주 수면을 깬 날짜 순으로 반환한다."""
    return [mains[d] for d in period.days() if d in mains]


def _exact(value: Fraction | float | int) -> Fraction:
    # 저장값은 소수 자리가 정해진 수라 문자열로 읽어 이진 소수 오차 없이 계산한다
    return Fraction(str(value)) if isinstance(value, float) else Fraction(value)


def _float(value: Fraction | None) -> float | None:
    return None if value is None else float(value)


def _minus(a: Fraction | None, b: Fraction | None) -> Fraction | None:
    return None if a is None or b is None else a - b


def _mean(values: Iterable[Fraction | float | int | None]) -> tuple[Fraction | None, int]:
    """값이 있는 것만의 평균과 그 수."""
    present = [_exact(v) for v in values if v is not None]
    if not present:
        return None, 0
    return sum(present, Fraction(0)) / len(present), len(present)


def _average(values: Iterable[Fraction | float | int | None]) -> tuple[Fraction | None, int]:
    """11.2 기간 값과 11.6 어젯밤 비교의 평균. 값이 있는 밤이 4밤 미만이면 평균은 None 이고 밤 수는 그대로 둔다."""
    avg, n = _mean(values)
    return (avg if n >= MIN_NIGHTS else None), n


def _last_night_value(last: float | int | None, week: Iterable[float | int | None]) -> LastNightValue:
    week_avg, week_n = _average(week)
    last_exact = None if last is None else _exact(last)
    return LastNightValue(
        last_night=_float(last_exact),
        week_avg=_float(week_avg),
        diff=_float(_minus(last_exact, week_avg)),
        week_nights=week_n,
    )


def _median(values: Iterable[float | int | None]) -> Fraction | None:
    # 짝수 개면 가운데 두 값의 평균이다
    present = [_exact(v) for v in values if v is not None]
    return statistics.median(present) if present else None


def _efficiency(night: NightRecord) -> Fraction | None:
    if night.sleep_minutes is None or not night.session_minutes:
        return None
    return Fraction(100 * night.sleep_minutes, night.session_minutes)


def _deep_ratio(night: NightRecord) -> Fraction | None:
    if night.deep_minutes is None or not night.sleep_minutes:
        return None
    return Fraction(100 * night.deep_minutes, night.sleep_minutes)


def _completed_days(linked_from: dt.date | None, period: DateRange) -> int:
    """기간 중 연동 시작일부터의 날 수. 수면은 오늘 깬 수면도 완성된 값이라 오늘을 센다 (1.2)."""
    if linked_from is None:
        return 0
    return max(0, (period.end - max(period.start, linked_from)).days + 1)


def _reliable(days_with_data: int, completed_days: int) -> bool:
    # 4.1: 기록 3일 이상이고 완성된 날의 절반 이상
    return days_with_data >= RELIABLE_MIN_DAYS and days_with_data * 2 >= completed_days


def _clock_minutes(t: dt.datetime) -> Fraction:
    """KST 0시부터의 분. 초 아래 자리까지 정확한 분수로 둔다."""
    k = t.astimezone(_KST)
    return Fraction(k.hour * 60 + k.minute) + Fraction(k.second * 1_000_000 + k.microsecond, 60_000_000)


def _unwrap(minutes: Sequence[Fraction]) -> list[Fraction]:
    """24시간 원에서 가장 넓게 빈 구간을 잘라 자정을 넘는 시각을 이어 붙인다 (11.2).

    값과 자르는 기준이 같은 정확한 분수라 경계 값이 하루 뒤로 넘어가지 않는다 (11.9).
    """
    on_circle = [m % _DAY_MINUTES for m in minutes]
    points = sorted(set(on_circle))
    # 각 점 앞의 빈 구간. 첫 점 앞은 마지막 점에서 자정을 넘어온 구간이다
    gaps = [points[0] + _DAY_MINUTES - points[-1], *(b - a for a, b in pairwise(points))]
    cut = points[gaps.index(max(gaps))]
    return [m if m >= cut else m + _DAY_MINUTES for m in on_circle]


def _clock_mean(minutes: Sequence[Fraction]) -> Fraction | None:
    return statistics.mean(_unwrap(minutes)) % _DAY_MINUTES if minutes else None


def _clock_diff(a: Fraction, b: Fraction) -> Fraction:
    """시각 a 가 b 보다 늦은 분. 두 시각을 가까운 쪽으로 이어 −720 이상 720 미만이 된다."""
    return (a - b + _DAY_MINUTES // 2) % _DAY_MINUTES - _DAY_MINUTES // 2


def _clock_stat(minutes: Sequence[Fraction]) -> ClockStat:
    if len(minutes) < MIN_NIGHTS:
        return ClockStat(avg=None, sd_min=None, nights=len(minutes))
    unwrapped = _unwrap(minutes)
    return ClockStat(
        avg=float(statistics.mean(unwrapped) % _DAY_MINUTES),
        sd_min=statistics.stdev(unwrapped),
        nights=len(minutes),
    )


def _weekend_gap(nights: Sequence[NightRecord], minute_of: Callable[[NightRecord], Fraction]) -> WeekendGap:
    """주말(깬 날짜가 토·일) 평균 − 평일 평균. 기간 전체 밤을 한 번에 이어 붙인 뒤 그룹 평균을 낸다 (11.9)."""
    weekday = [n for n in nights if n.date.weekday() < 5]
    weekend = [n for n in nights if n.date.weekday() >= 5]
    gap = None
    if len(weekday) >= MIN_WEEKDAY_NIGHTS and len(weekend) >= MIN_WEEKEND_NIGHTS:
        unwrapped = _unwrap([minute_of(n) for n in (*weekday, *weekend)])
        gap = float(statistics.mean(unwrapped[len(weekday) :]) - statistics.mean(unwrapped[: len(weekday)]))
    return WeekendGap(min=gap, weekday_nights=len(weekday), weekend_nights=len(weekend))


def _comparable(day: DayRecord | None, linked_from: dt.date | None, synced_day: dt.date | None) -> bool:
    """4.2 비교 대상 날짜. 연동 전, 아직 안 들어옴(동기화 기준일과 같거나 이후), 활동 행이 없는 날은 뺀다.

    D 는 깬 날의 전날이라 오늘이 되지 않는다.
    """
    return (
        day is not None
        and day.has_activity_row
        and linked_from is not None
        and day.date >= linked_from
        and synced_day is not None
        and day.date < synced_day
    )


def _late_exercise(ends: Iterable[dt.datetime], night: NightRecord) -> bool:
    """인정 운동 중 하나라도 그날 밤 잠든 시각 전 3시간 안에 끝났는지 (11.4)."""
    return any(end >= night.start_at - LATE_EXERCISE for end in ends)


_OUTCOMES: dict[str, Callable[[NightRecord], Fraction | float | int | None]] = {
    "sleep_minutes": lambda n: n.sleep_minutes,
    "deep_minutes": lambda n: n.deep_minutes,
    "efficiency_pct": _efficiency,
    "awake_minutes": lambda n: n.awake_minutes,
    "sleep_hr_avg": lambda n: n.sleep_hr_avg,
}


def _compare(
    true_nights: Sequence[NightRecord],
    false_nights: Sequence[NightRecord],
    value_of: Callable[[NightRecord], Fraction | float | int | None],
) -> GroupCompare:
    """결과 하나의 그룹별 평균. 값이 없는 밤은 그 결과에서만 뺀다."""
    t, t_n = _mean(value_of(n) for n in true_nights)
    f, f_n = _mean(value_of(n) for n in false_nights)
    return GroupCompare(
        true_avg=_float(t),
        true_nights=t_n,
        false_avg=_float(f),
        false_nights=f_n,
        diff=_float(_minus(t, f)),
        enough_data=t_n >= MIN_NIGHTS and f_n >= MIN_NIGHTS,
    )


def _good_bad(
    nights: Sequence[NightRecord], mains: dict[dt.date, NightRecord], days: dict[dt.date, DayRecord]
) -> GoodBadNights:
    """수면 시간으로 세 등분한 위·아래 3분의 1. 같은 수면 시간이면 먼저 깬 밤을 앞에 둔다 (11.9)."""
    ranked = [n for n in nights if n.sleep_minutes is not None]
    k = len(ranked) // 3
    good = sorted(ranked, key=lambda n: (-(n.sleep_minutes or 0), n.date))[:k]
    bad = sorted(ranked, key=lambda n: (n.sleep_minutes or 0, n.date))[:k]
    return GoodBadNights(good=_night_group(good, mains, days), bad=_night_group(bad, mains, days))


def _night_group(
    group: Sequence[NightRecord], mains: dict[dt.date, NightRecord], days: dict[dt.date, DayRecord]
) -> NightGroup:
    """그룹 밤마다 그 전날 D 의 값을 모은다. 비율의 분모는 그룹 밤 수다."""
    befores = [days.get(n.date - _ONE_DAY) for n in group]
    exercised = sum(1 for day in befores if day is not None and day.exercise_ends)
    late = sum(
        1
        for n, day in zip(group, befores, strict=True)
        if day is not None and day.exercise_ends and _late_exercise(day.exercise_ends, n)
    )
    steps, _ = _mean(day.steps for day in befores if day is not None and not day.steps_missing)
    prev_sleep, _ = _mean(mains[n.date - _ONE_DAY].sleep_minutes for n in group if n.date - _ONE_DAY in mains)
    sleeps = [n.sleep_minutes for n in group if n.sleep_minutes is not None]
    return NightGroup(
        nights=len(group),
        sleep_range=(min(sleeps), max(sleeps)) if sleeps else None,
        exercise_day_pct=_float(_pct(exercised, len(group))),
        late_exercise_pct=_float(_pct(late, len(group))),
        steps_avg=_float(steps),
        prev_sleep_avg=_float(prev_sleep),
        bedtime_avg=_float(_clock_mean([_clock_minutes(n.start_at) for n in group])),
        weekend_pct=_float(_pct(sum(1 for n in group if n.date.weekday() >= 5), len(group))),
        enough_data=len(group) >= MIN_NIGHTS,
    )


def _pct(count: int, total: int) -> Fraction | None:
    return Fraction(100 * count, total) if total else None


def _longest_short_streak(mains: dict[dt.date, NightRecord], period: DateRange) -> list[dt.date]:
    """5시간 미만 주 수면이 깬 날짜로 이어진 가장 긴 연속. 기록이 없는 날에서 끊긴다 (11.9)."""
    longest: list[dt.date] = []
    run: list[dt.date] = []
    for d in period.days():
        night = mains.get(d)
        if night is not None and night.sleep_minutes is not None and night.sleep_minutes < SHORT_STREAK_MINUTES:
            run.append(d)
            if len(run) > len(longest):
                longest = list(run)
        else:
            run = []
    return longest
