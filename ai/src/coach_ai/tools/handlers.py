from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

from coach_ai.agent.evidence import with_detail
from coach_ai.agent.executor import EvidenceDraft, ExtraPeriod, ToolHandler, ToolOutcome, ToolRequest
from coach_ai.ports import (
    HealthDataNotReadyError,
    HealthDataPort,
    HealthDataUnavailableError,
    LifestyleScoreResult,
    MetricResult,
    MissingReason,
    ResultContractError,
    ResultDay,
    ResultNight,
    ResultSession,
    ResultSleepSession,
    ResultValue,
    ScoreActivity,
    ScoreHeart,
    ScoreSleep,
    SleepSessionsResult,
)
from coach_ai.tools import daily_message, exercise_activity
from coach_ai.tools.outcomes import detail_failed
from coach_ai.tools.periods import (
    DEFAULT_TZ,
    MAX_LIST_DAYS,
    DateRange,
    PeriodError,
    earliest_start,
    period_label,
    previous_period,
    range_expression,
    range_text,
    resolve_period,
    split_range,
)
from coach_ai.tools.results import (
    BASES,
    BASIS_NOT_TODAY_NOTE,
    EXERCISE_MAX_POINTS,
    MEASURED_DURING,
    NO_SLEEP_MINUTES,
    SCORE_GRADE_LABELS,
    STEPS_MAX_POINTS,
    UNITS,
    ClockRange,
    DailyTrend,
    DatedDuration,
    DatedSleepMeasureValue,
    DatedValue,
    Duration,
    ExerciseSessionItem,
    ExerciseSessions,
    ExerciseTotal,
    HealthOverview,
    InProgress,
    LifestyleScore,
    MetricSummary,
    MissingLabel,
    PeriodAverage,
    PeriodComparison,
    PeriodSpan,
    ScoreAreaResult,
    ScoreGradeLabel,
    ScoreInProgress,
    ScoreTotalResult,
    SleepAnalysisResult,
    SleepMeasureSummary,
    SleepMeasureValue,
    SleepNight,
    SleepRecords,
    SleepScoreAreaResult,
    SleepSummary,
    TrendPoint,
    Unit,
    Value,
    add_not_today_note,
    format_basis_time,
    format_calories,
    format_distance,
    format_in_progress,
    format_minutes,
    format_relative_nights,
    format_sleep_window,
    format_slept,
    format_value,
    is_before_today,
    oxygen_shown,
)
from coach_ai.tools.schemas import (
    OVERVIEW_METRICS,
    PERIODS,
    PUBLIC_METRICS,
    ComparePeriods,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetLifestyleScore,
    GetMetricSummary,
    GetSleepRecords,
    Metric,
    Period,
    SleepAnalysis,
)
from coach_ai.tools.sleep_coaching import (
    BASELINE_DAYS,
    DECLINING_RECENT_DAYS,
    FACTORS_DAYS,
    FACTORS_MIN_DAYS,
    PATTERN_DAYS,
    SIGNALS_DAYS,
    SLEEP_HR_RECENT_DAYS,
    SPO2_TIMES_SHOWN,
    SleepCoachingInput,
    compares_exercise,
    day_records,
    exercise_ends,
    factors,
    last_night_vs_week,
    night_records,
    pattern,
    short_factors,
    signals,
    sleep_vs_baseline,
)
from coach_ai.tools.sleep_coaching_text import (
    declining_line,
    exercise_comparison_text,
    extended_factors_lead,
    factors_text,
    last_night_line,
    low_times_text,
    pattern_text,
    short_factors_text,
    signals_text,
)
from coach_ai.tools.sleep_score import SLEEP_SCORE_RULE, sleep_targets

# 수면 시각과 진행 중 값의 기준 시각은 집계 테이블 날짜와 같은 한국 시간으로 표시한다
_KST = ZoneInfo(DEFAULT_TZ)

PERIOD_LABELS: dict[Period, str] = {
    "today": "오늘",
    "yesterday": "어제",
    "last_7_days": "지난 7일",
    "last_14_days": "지난 14일",
    "last_30_days": "지난 30일",
    "this_week": "이번 주",
    "last_week": "지난주",
    "week_before_last": "지지난주",
    "this_month": "이번 달",
    "last_month": "지난달",
}
METRIC_LABELS: dict[Metric, str] = {
    "steps": "걸음 수",
    "heart_rate": "심박",
    "oxygen_saturation": "수면 중 산소포화도",
    "sleep": "수면",
    "exercise": "운동 시간",
}
# 기간 요약에서 missing_days 로 세는 사유는 모두 기록 없음, unsynced_days 로 세는 사유는 동기화 전으로 표시한다
MISSING_LABELS: dict[MissingReason, MissingLabel] = {
    "NO_RECORD": "기록 없음",
    "NULL_VALUE": "기록 없음",
    "ZERO_STEPS": "기록 없음",
    "UNSYNCED": "동기화 전",
    "BEFORE_LINK": "연동 전",
}
# 기간 비교의 reliable 기준(calculation-rules.md 4장)과 변화율 자릿수. 실데이터를 보고 조정하는 값이라 여기서만 정한다
RELIABLE_MIN_DAYS = 3
RELIABLE_MIN_SHARE = Fraction(1, 2)
CHANGE_RATE_PLACES = Decimal("0.1")
_SHARE_TEXT = "절반" if RELIABLE_MIN_SHARE == Fraction(1, 2) else f"{float(RELIABLE_MIN_SHARE):.0%}"
RELIABLE_NOTE = (
    "기록이 적은 기간이 있어 비교 결과를 단정하기 어렵다. "
    f"두 기간 모두 기록이 {RELIABLE_MIN_DAYS}일 이상이고 완성된 날의 {_SHARE_TEXT} 이상이어야 reliable 이 true 다."
)
IN_PROGRESS_NOTE = (
    "오늘은 진행 중이라 하루 평균이 없어 차이를 계산하지 않았다. "
    "current 의 오늘 진행 값(in_progress)과 baseline 의 평균을 각각 안내한다."
)
# 날짜마다 값을 전달하는 도구가 31일을 넘는 기간을 받았을 때 결과 문장에 쓰는 한도와 기간 요약의 사실.
# 기간 요약은 같은 기간을 조회하지만 날짜마다의 값이 없다
LIST_LIMIT_FACTS: dict[str, tuple[str, str]] = {
    "get_daily_trend": (
        f"날짜별 값은 한 번에 {MAX_LIST_DAYS}일까지 조회된다.",
        "기간 요약(get_metric_summary)은 이 기간 전체를 조회하지만 날짜별 값은 없다.",
    ),
    "get_sleep_records": (
        f"날짜별 수면 기록은 한 번에 {MAX_LIST_DAYS}일까지 조회된다.",
        "기간 요약(get_metric_summary)은 이 기간 전체의 수면 시간을 조회하지만 날짜별 값은 없다.",
    ),
    "get_exercise_sessions": (
        f"운동 기록은 한 번에 {MAX_LIST_DAYS}일까지 조회된다.",
        "기간 요약(get_metric_summary, metric=exercise)은 이 기간 전체의 운동 시간을 조회하지만 "
        "종류별·세션별 기록은 없다.",
    ),
}
# 31일을 넘는 기간을 나눠 결과 문장에 넣는 기간의 최대 일수(7주). 31일 밤별 수면 기록 결과 하나가 4,000토큰을
# 넘어, 수면 기록이 가장 큰 골든셋 고정 데이터(synth-11)로 두 조각을 함께 조회하면 51일부터(다른 날 대화 안내가
# 없으면 52일부터) 이전 대화 요약까지 빼야 하고 59일부터는 이전 대화를 모두 빼도 호출 하나의 입력 상한을 넘는다
LIST_SPLIT_MAX_DAYS = 49
# 운동 세션 조회 결과에 담는 최근 세션 수. 하루 한 번 운동하는 사람의 한 주 기록을 모두 담는 개수다
SESSION_LIST_LIMIT = 8
NO_SESSION_RECORD = "기간 안에 시작한 운동 기록 없음"
# 일별 활동 거리(activity_daily.exercise_distance_m)는 미인정 느린 걷기도 더해 세션 거리와 기준이 다르다.
# 거리 안내 칸만으로는 답변에서 기준이 빠져 하루 이동 거리처럼 읽혀, 거리 표시마다 기준을 붙인다
DISTANCE_BASIS = "운동 기록 기준"
DISTANCE_NOTE = "운동으로 인정된 세션 중 거리 기록이 있는 세션(distance_sessions)의 거리만 더했다."
# 세션 칼로리는 운동 중 값이고 하루 전체 소모 칼로리는 기록에 없다. 세션 거리도 하루 동안 이동한 거리가 아니다
CALORIE_DISTANCE_NOTE = (
    "칼로리와 거리는 운동 세션에 기록된 값과 그 합이고, 하루 전체 소모 칼로리나 하루 동안 이동한 거리가 아니다."
)
# 주 수면 기록이 이 개수 이하면 수면 기록마다 수면 중 심박·산소포화도를 넣고, 넘으면 수면 기록마다 넣는 대신
# 기간 요약(measure_summary)을 넣는다. 수면 기록마다 넣으면 한 개에 약 100토큰이라 긴 기간의 결과가 커진다
SLEEP_RECORD_MEASURE_LIMIT = 7
# 산소포화도 값이 보이는 결과에 넣는 사실 문장 (calculation-rules.md 7.5 "워치 측정은 의료용보다 오차가 있다")
SPO2_DEVICE_FACT = "워치 측정은 의료기기 측정이 아니다."
# 어젯밤·그저께 밤 날짜를 넣는 기간. 오늘부터 이 날수 전까지의 날짜가 하나라도 기간에 있으면 넣는다
RELATIVE_NIGHT_DAYS = 2
NON_MAIN_NOTE = "주 수면이 아닌 잠이고 summary 에 들어가지 않는다."
NO_NON_MAIN = "기간 안에 주 수면이 아닌 잠 기록이 없다."
# 수면 기록 결과에서 값이 없으면 칸을 빼는 항목
_SLEEP_OPTIONAL = (
    "relative_nights",
    "spo2_low",
    "spo2_note",
    "sleep_time_note",
    "measure_summary",
    "non_main_sleeps",
    "non_main_note",
    "sleep_vs_baseline",
    "last_night_vs_week",
)
# 수면 시간(sleep_minutes)을 재는 방식. 연동 쪽 집계는 조각마다 실제로 잔 시간(삼성 헬스 sleep_duration_total, 없으면
# 잠든 단계 길이의 합)을 더하고, 잠든 시각은 워치가 잠든 것으로 기록한 시각이다 (calculation-rules.md 7.5, 11.1)
SLEEP_TIME_NOTE = (
    "수면 시간은 잠든 시각(워치가 잠든 것으로 기록한 시각)부터 깬 시각 사이에 실제로 잔 시간이고, "
    "중간에 깬 시간과 잠들기 전·깬 뒤 누워 있던 시간은 빠져 있다. "
    "이는 집계 기준이며 사용자가 말한 시간과 다른 원인을 확인한 것은 아니다. "
    "차이의 원인은 확인할 수 없으며, 이전 답변의 동기화 지연·워치 인식 오류 추측은 근거가 없어 반복하지 않는다. "
    "사용자가 잤다고 말한 시간과 다르면 첫 문장은 해당 날짜의 실제 수면 시간으로 시작하고 이 집계 기준을 설명한다. "
    "피로의 원인이나 사용자 시간과 다른 이유를 수면 패턴만으로 추정하지 않는다."
)
# 기본 조회에 두 줄(오늘 기준 줄어드는 중 판정 값, 어젯밤 대 그 전 7일)을 붙이는 기간의 최대 일수.
# 기간이 오늘이나 어제로 끝날 때만 붙인다. pattern 결과의 어젯밤 줄은 일수와 관계없이 끝 날짜만 확인한다
LINES_MAX_DAYS = 14
# 수면 코칭 분석(calculation-rules.md 11장)의 이름과, 질문에 기간이 없을 때의 분석 기간 (11.2, 11.4, 11.5)
ANALYSIS_NAMES: dict[SleepAnalysis, str] = {
    "pattern": "수면 패턴 판정",
    "signals": "이상 신호",
    "factors": "조건별 비교",
}
ANALYSIS_DAYS: dict[SleepAnalysis, int] = {"pattern": PATTERN_DAYS, "signals": SIGNALS_DAYS, "factors": FACTORS_DAYS}
# 분석은 주 수면만 쓴다 (11장). 주 수면이 아닌 잠 조회를 함께 받으면 조회하지 않고 이 사실을 넣는다
ANALYSIS_MAIN_ONLY = "분석에는 낮잠 같은 짧은 잠을 넣지 않았다."
# 기간 요약(measure_summary)의 값의 뜻. 수면 중 평균 심박이 그날의 평균 심박이나 표본 전체의 평균이 아니라는 것을
# 결과 안에서 알 수 있게 한다
MEASURE_SUMMARY_NOTE = (
    "수면 중 평균 심박(avg_heart_rate)과 산소포화도 평균(spo2_avg)은 수면마다 기록된 값의 평균을 다시 평균한 값이다. "
    "최저·최고(min_heart_rate, max_heart_rate, spo2_min)는 수면 기록 중 가장 낮은·높은 값과 그 수면 기록의 날짜다. "
    "records 는 그 값이 있는 수면 기록 수다."
)
# 지표 요약·날짜별 조회의 심박·산소포화도 값을 어떤 측정으로 계산했는지와 자는 동안 잰 값이 있는 결과. 자는 동안의
# 값을 묻는 질문에 하루 값으로 답하는 일을 방지한다. 산소포화도는 주 수면 중에만 재서 하루 전체 값이 아니고, 수면
# 기록이 8개 이상인 기간 요약에는 산소포화도 최고가 없어 최저값만 적는다. 수면 기록의 날짜가 깬 날이라는 문장은
# 밤에 잔 수면을 그날 날짜의 기록으로 읽은 답을 방지한다
_WAKE_DATE_FACT = "수면 기록의 날짜는 잠에서 깬 날이다."
SLEEP_VALUES_NOTES: dict[Metric, str] = {
    "heart_rate": (
        "이 결과의 심박 값은 하루 전체 측정으로 계산한 값이고, "
        f"자는 동안 잰 최저·최고 심박은 수면 기록(get_sleep_records) 결과에 있다. {_WAKE_DATE_FACT}"
    ),
    "oxygen_saturation": (
        "이 결과의 산소포화도 값은 날마다 주 수면 중에 잰 값의 평균이고, "
        f"자는 동안 잰 최저값과 90% 미만 측정은 수면 기록(get_sleep_records) 결과에 있다. {_WAKE_DATE_FACT}"
    ),
}
# 근력 운동과 웨이트 트레이닝처럼 기준표에서 서로 다른 종류를 한 운동으로 합친 답을 방지한다.
# 도구 설명이 아니라 결과에 넣어 세션 조회를 부른 뒤에만 전달된다
TYPE_NOTE = (
    "종류(types)는 서로 다른 운동이다. "
    "질문한 이름과 같은 이름의 종류가 있으면 그 종류의 값만 답한다. "
    "여러 종류에 걸치는 이름(예: 헬스)은 결과에 해당하는 종류가 둘 이상이면 "
    "종류별로 나눠 말하고 어느 것인지 묻는다. "
    "종류 값을 더한 숫자를 새로 만들지 않고, 전체 합계가 필요하면 total 값을 쓴다."
)
# 합계 문구에만 있는 기록 일수와 오늘 기준 시각은 답변에서 빠지기 쉽고, 종류별 값으로 답하면 더 자주 빠진다.
# 붙일 문구를 그대로 넣어 같은 문장에 쓰게 한다
ANSWER_NOTE = (
    "횟수·시간·거리·칼로리를 말할 때는 total.display 의 기록 기준({basis})을 같은 문장에 붙인다. "
    "종류별 값으로 답할 때도 같다."
)
# 점수 결과에 넣는 문장. 지시하는 문장은 답에 그대로 나올 수 있어 사실만 적는다. 점수 규칙 문서
# (score-rules.md 4.1, 6.2)의 내용이고 조건이 맞을 때만 들어간다
SCORE_TOTAL_NOTE = "점수가 있는 영역의 평균이며 등급이 없다"
SCORE_PARTIAL_NOTE = "기록이 없는 영역은 평균에서 빠졌다"
SCORE_IN_PROGRESS_NOTE = "하루가 끝나지 않아 동기화될 때마다 다시 계산되고, 걷거나 운동하면 활동 점수가 오를 수 있다"
SCORE_REASON_NOTE = (
    "각 영역 점수의 이유는 그 영역의 used 값이다. 영역을 말하지 않은 질문이면 등급이 가장 낮은 영역이 주된 이유다"
)
SCORE_CARE_NOTE = "관리 등급은 질병이 있거나 치료가 필요하다는 뜻이 아니다"
SCORE_REST_DAY_NOTE = "운동하지 않은 날이 있는 것은 정상이다"
SCORE_LOWER_HEART_NOTE = (
    "평소보다 낮은 수면 중 심박은 감점 대상이 아니고, 낮아진 값은 건강이 좋아지거나 나빠졌다는 근거가 아니다"
)
SCORE_CHECK_WEAR_NOTE = "수면 중 심박이 평소보다 많이 낮아 감점 대상이 아니고, 워치 착용 상태 확인 안내 대상이다"


def _span(period: str, metric: Metric, today: dt.date, *, tool: str | None = None) -> PeriodSpan:
    """기간 표현의 날짜 구간. 조회할 수 없는 기간이면 PeriodError 를 발생시킨다.

    날짜마다 값을 전달하는 도구(LIST_LIMIT_FACTS)는 31일까지만 받는다.
    """
    r = resolve_period(period, today)
    if tool in LIST_LIMIT_FACTS and (r.end - r.start).days + 1 > MAX_LIST_DAYS:
        raise PeriodError("too_long", _too_long_note(tool, period, r, today))
    return PeriodSpan(
        period=period, start=r.start, end=r.end, basis=BASES[metric], display=period_label(period, r, today)
    )


def _too_long_note(tool: str, period: str, r: DateRange, today: dt.date) -> str:
    """31일을 넘는 기간의 결과 문장. 한도, 질문한 기간과 날짜, 7주까지면 나눈 기간, 기간 요약의 사실을 쓴다."""
    limit, summary = LIST_LIMIT_FACTS[tool]
    days = (r.end - r.start).days + 1
    asked = (period_label(period, r, today) or range_text(r, today)).replace(" (", "(")
    sentences = [limit, f"{asked} 기간은 {days}일이라 조회하지 않았다."]
    if days <= LIST_SPLIT_MAX_DAYS:
        expressions = ", ".join(range_expression(part, today) for part in split_range(r, MAX_LIST_DAYS))
        sentences.append(f"{MAX_LIST_DAYS}일 이하로 나누면 {expressions} 로 조회된다.")
    return " ".join([*sentences, summary])


def _label(span: PeriodSpan) -> str:
    """근거 summary 의 기간 문구. 이름 기간은 지금 문구, 새 표현은 종류와 실제 날짜다."""
    return span.display or PERIOD_LABELS[span.period]


def _unavailable(tool: str, field: str, period: str, error: PeriodError) -> ToolOutcome:
    """기간을 조회하지 않고 안내만 반환한다. 근거는 없다."""
    return ToolOutcome("unavailable_period", {"tool": tool, field: period, "message": error.message})


def _quantize(unit: Unit, value: Decimal) -> Decimal:
    # 걸음 수와 분은 정수, 심박과 산소포화도는 소수 첫째 자리까지 쓴다
    places = Decimal(1) if unit in ("steps", "min") else Decimal("0.1")
    return value.quantize(places, ROUND_HALF_UP)


def _round(unit: Unit, value: float) -> float:
    # 포트 값은 반올림만 하고 다시 계산하지 않는다
    # 공개 평균은 이미 소수 둘째 자리라 같은 ROUND_HALF_UP 으로 줄인다 (부동소수 round 는 72.25 를 72.2 로 내린다)
    return float(_quantize(unit, Decimal(str(value))))


def _value(unit: Unit, value: float) -> Value:
    v = _round(unit, value)
    return Value(value=v, display=format_value(unit, v))


def _dated(unit: Unit, point: ResultValue | None) -> DatedValue | None:
    if point is None:
        return None
    v = _round(unit, point.value)
    return DatedValue(value=v, display=format_value(unit, v), date=point.date)


async def _lookup(
    req: ToolRequest, metric: Metric, span: PeriodSpan, port: HealthDataPort | None = None
) -> MetricResult:
    """port 가 없으면 req.port 로 조회한다. 결과의 지표·기간·날짜가 요청과 다르면 ResultContractError 다."""
    result = await (port or req.port).get_metric_result(req.user_id, metric, span.start, span.end)
    dates = [span.start + dt.timedelta(days=i) for i in range((span.end - span.start).days + 1)]
    if (
        result.metric != PUBLIC_METRICS[metric]
        or (result.period.start, result.period.end) != (span.start, span.end)
        or [d.date for d in result.days] != dates
    ):
        raise ResultContractError("조회 결과의 지표·기간이 요청과 다르다")
    return result


@dataclass(frozen=True, slots=True)
class _Coverage:
    missing_days: list[dt.date]
    unsynced_days: list[dt.date]
    # 기록 일수 표시 문자열. 근거 summary 의 괄호 안에도 같은 문자열을 쓴다
    text: str


def _coverage(result: MetricResult, metric: Metric) -> _Coverage:
    """원형의 결측 구분과 일수로 LLM 결과의 결측 목록과 기록 일수 표시를 만든다.

    결측 구분은 연동 함수가 정한 원형 값을 그대로 쓰고 다시 나누지 않는다.
    오늘 진행 중인 날은 통계 대상이 아니라 결측 목록에서 빼고 진행 중 값으로 따로 안내한다.
    """
    s = result.summary
    ongoing = {d.date for d in result.days if d.is_in_progress}
    missing = [d for d in s.missing_days if d not in ongoing]
    unsynced = [d for d in s.unsynced_days if d not in ongoing]
    if s.linked_from is None:
        return _Coverage(missing, unsynced, "연동 후 받은 기록 없음")
    if metric == "exercise":
        # 운동 0분인 날도 기록 일수에 들어가서 운동한 날 수로 읽히지 않게 적는다
        text = f"{s.completed_days}일 중 활동 기록이 있는 날 {s.days_with_data}일"
    else:
        text = f"{s.completed_days}일 중 {s.days_with_data}일 기록"
    # 연동 전 날짜는 completed_days 에서 빠지므로 기간보다 일수가 적은 이유를 밝힌다
    if any(d.reason == "BEFORE_LINK" for d in result.days):
        text = f"연동일 {s.linked_from.month}/{s.linked_from.day} 이후 {text}"
    if unsynced:
        text += f", 동기화 전 {len(unsynced)}일"
    return _Coverage(missing, unsynced, text)


def _in_progress(row: MetricResult, metric: Metric, today: dt.date) -> InProgress | None:
    if row.in_progress is None:
        return None
    # 오늘 0보도 결측이다. 저장된 0은 근거 snapshot 에만 둔다
    raw = None if row.in_progress.missing else row.in_progress.value
    value = _round(UNITS[metric], raw) if raw is not None else None
    # 운동의 원형 기준 시각은 마지막 운동 기록 시각이라 기록이 어디까지 들어왔는지 나타내지 않는다.
    # 운동은 동기화 기준 시각을 쓰고, 마지막 운동 기록 시각은 근거 snapshot 에만 둔다
    basis = row.data_status.synced_until if metric == "exercise" else row.in_progress.as_of
    # 원형 시각은 UTC 다. LLM 결과에는 표시 문자열과 같은 한국 시간으로 전달한다
    as_of = None if basis is None else basis.astimezone(_KST)
    return InProgress(
        date=row.in_progress.date,
        value=value,
        display=format_in_progress(metric, value, as_of, today),
        as_of=as_of,
    )


def _line(metric: Metric, head: str, coverage: str, completed_days: int, in_progress: InProgress | None) -> str:
    """기간 요약과 일별 추이가 같은 규칙으로 만드는 근거 summary 문구."""
    if completed_days == 0 and in_progress is not None:
        # 오늘만 묻는 경우처럼 완성된 날이 없으면 진행 중 값만 적는다
        return f"{METRIC_LABELS[metric]} {in_progress.display}"
    line = f"{head} ({coverage})"
    return line if in_progress is None else f"{line}, {in_progress.display}"


async def _lookup_all(req: ToolRequest, lookups: Sequence[tuple[Metric, PeriodSpan]]) -> list[MetricResult]:
    """조회를 동시에 실행한다. 하나가 실패하면 나머지 조회를 취소하고 그 예외를 그대로 발생시킨다."""
    tasks = [asyncio.create_task(_lookup(req, metric, span)) for metric, span in lookups]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _summary(
    metric: Metric, span: PeriodSpan, row: MetricResult, today: dt.date, sleep_values_note: str | None = None
) -> tuple[MetricSummary, str]:
    """한 지표·기간의 요약 결과와 근거 summary 문구. 기간 요약·기간 비교·전체 요약이 같은 값을 쓴다.

    sleep_values_note 는 기간 요약 도구만 넘긴다."""
    s = row.summary
    coverage = _coverage(row, metric)
    unit = UNITS[metric]
    in_progress = _in_progress(row, metric, today)
    heart = metric == "heart_rate"
    result = MetricSummary(
        metric=metric,
        period=span,
        unit=unit,
        measured_during=MEASURED_DURING.get(metric),
        avg=_value(unit, s.avg) if s.avg is not None else None,
        lowest_day=_dated(unit, s.daily_min),
        highest_day=_dated(unit, s.daily_max),
        # 도구 설명에 측정 최저·최고는 심박에만 있다고 적혀 있어 다른 지표는 비운다
        measured_min=_dated(unit, row.detail.measured_min) if heart else None,
        measured_max=_dated(unit, row.detail.measured_max) if heart else None,
        total=_value(unit, s.total) if s.total is not None else None,
        days_with_data=s.days_with_data,
        completed_days=s.completed_days,
        missing_days=coverage.missing_days,
        unsynced_days=coverage.unsynced_days,
        linked_from=s.linked_from,
        coverage=coverage.text,
        in_progress=in_progress,
        sleep_values_note=sleep_values_note,
    )
    label = f"{_label(span)} {METRIC_LABELS[metric]}"
    head = f"{label} 평균 {result.avg.display}" if result.avg else f"{label} 기록 없음"
    return result, _line(metric, head, result.coverage, s.completed_days, in_progress)


async def get_metric_summary(inp: GetMetricSummary, req: ToolRequest) -> ToolOutcome:
    try:
        span = _span(inp.period, inp.metric, req.today)
    except PeriodError as e:
        return _unavailable("get_metric_summary", "period", inp.period, e)
    row = await _lookup(req, inp.metric, span)
    result, line = _summary(inp.metric, span, row, req.today, SLEEP_VALUES_NOTES.get(inp.metric))
    draft = EvidenceDraft("get_metric_summary", PUBLIC_METRICS[inp.metric], span, line, row.snapshot)
    payload = result.model_dump(mode="json")
    if inp.metric == "exercise":
        payload = exercise_activity.extend(payload, row, daily=False)
    return ToolOutcome("ok", payload, (draft,))


def _point(unit: Unit, day: ResultDay) -> TrendPoint:
    # 결측인 날은 저장된 0보가 있어도 값을 전달하지 않는다. 앱 근거 화면도 결측인 날은 값을 표시하지 않는다
    value = None if day.missing or day.value is None else _round(unit, day.value)
    return TrendPoint(
        date=day.date,
        value=value,
        display=None if value is None else format_value(unit, value),
        missing=None if day.reason is None else MISSING_LABELS[day.reason],
        in_progress=True if day.is_in_progress else None,
    )


async def get_daily_trend(inp: GetDailyTrend, req: ToolRequest) -> ToolOutcome:
    try:
        span = _span(inp.period, inp.metric, req.today, tool="get_daily_trend")
    except PeriodError as e:
        return _unavailable("get_daily_trend", "period", inp.period, e)
    row = await _lookup(req, inp.metric, span)
    s = row.summary
    coverage = _coverage(row, inp.metric)
    unit = UNITS[inp.metric]
    in_progress = _in_progress(row, inp.metric, req.today)
    heart = inp.metric == "heart_rate"
    result = DailyTrend(
        metric=inp.metric,
        period=span,
        unit=unit,
        measured_during=MEASURED_DURING.get(inp.metric),
        points=[_point(unit, day) for day in row.days],
        # 기간 요약과 같이 도구 설명에 측정 최저·최고가 심박에만 있다고 적혀 있어 다른 지표는 비운다
        measured_min=_dated(unit, row.detail.measured_min) if heart else None,
        measured_max=_dated(unit, row.detail.measured_max) if heart else None,
        days_with_data=s.days_with_data,
        coverage=coverage.text if s.linked_from is None else None,
        in_progress=in_progress,
        sleep_values_note=SLEEP_VALUES_NOTES.get(inp.metric),
    )
    head = f"{_label(span)} {METRIC_LABELS[inp.metric]} 날짜별 기록"
    line = _line(inp.metric, head, coverage.text, s.completed_days, in_progress)
    draft = EvidenceDraft("get_daily_trend", PUBLIC_METRICS[inp.metric], span, line, row.snapshot)
    # 날짜마다 비어 있는 항목이 많아 null 인 항목을 빼고 전달한다
    payload = result.model_dump(mode="json", exclude_none=True)
    if inp.metric == "exercise":
        payload = exercise_activity.extend(payload, row, daily=True)
    return ToolOutcome("ok", payload, (draft,))


def _clock(value: dt.datetime) -> dt.time:
    return value.astimezone(_KST).time()


async def _with_sessions(req: ToolRequest, span: PeriodSpan) -> tuple[MetricResult, SleepSessionsResult | None]:
    """주 수면 원형과 전체 수면 세션을 동시에 조회한다. 세션 조회만 실패하면(조회 실패, 준비 중) 세션은 None 이다.

    주 수면 조회가 실패하면 세션 조회를 취소하고 그 예외를 그대로 발생시킨다. 세션 조회의 한도 초과·대상 없음과
    그 밖의 예외는 바꾸지 않아 게이트웨이가 호출 전체의 결과로 처리한다.
    """
    task = asyncio.create_task(req.port.get_sleep_sessions(req.user_id, span.start, span.end))
    try:
        row = await _lookup(req, "sleep", span)
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    try:
        found = await task
    except (HealthDataUnavailableError, HealthDataNotReadyError):
        return row, None
    if (found.period.start, found.period.end) != (span.start, span.end) or any(
        not span.start <= s.date <= span.end for s in found.sessions
    ):
        raise ResultContractError("수면 세션 결과의 기간이 요청과 다르다")
    return row, found


async def get_sleep_records(inp: GetSleepRecords, req: ToolRequest) -> ToolOutcome:
    if inp.analysis is not None:
        return await _sleep_analysis(inp, inp.analysis, req)
    if inp.period is None:
        raise ValueError("analysis 가 없는 수면 기록 조회에 period 가 없다")
    try:
        span = _span(inp.period, "sleep", req.today, tool="get_sleep_records")
    except PeriodError as e:
        return _unavailable("get_sleep_records", "period", inp.period, e)
    lines_task = asyncio.create_task(_basic_lines(req)) if _shows_lines(span, req.today) else None
    try:
        if inp.include_non_main:
            row, sessions = await _with_sessions(req, span)
        else:
            row, sessions = await _lookup(req, "sleep", span), None
    except BaseException:
        if lines_task is not None:
            lines_task.cancel()
            await asyncio.gather(lines_task, return_exceptions=True)
        raise
    lines = None if lines_task is None else await lines_task
    # 원형의 nights 는 주 수면만 담는다. 낮잠은 밤별 기록과 시각 범위에 들어가지 않는다
    mains = row.detail.nights
    each = len(mains) <= SLEEP_RECORD_MEASURE_LIMIT
    nights = [_sleep_night(n, with_measures=each) for n in mains]

    def duration(point: ResultValue | None) -> DatedDuration | None:
        if point is None:
            return None
        minutes = int(_round("min", point.value))
        return DatedDuration(minutes=minutes, display=format_minutes(minutes), date=point.date)

    s = row.summary
    coverage = _coverage(row, "sleep")
    avg = int(_round("min", s.avg)) if s.avg is not None else None
    summary = SleepSummary(
        avg_total=Duration(minutes=avg, display=format_minutes(avg)) if avg is not None else None,
        shortest=duration(s.daily_min),
        longest=duration(s.daily_max),
        bedtime=ClockRange.from_times([(n.date, _clock(n.start_at)) for n in mains]) if mains else None,
        wake=ClockRange.from_times([(n.date, _clock(n.end_at)) for n in mains]) if mains else None,
        days_with_data=s.days_with_data,
        completed_days=s.completed_days,
        missing_days=coverage.missing_days,
        unsynced_days=coverage.unsynced_days,
        linked_from=s.linked_from,
        coverage=coverage.text,
    )
    measure = None if each else _measure_summary(mains)
    low = _spo2_low(mains, req.today)
    shown = oxygen_shown(nights, measure, low)
    non_main = None if sessions is None else [_non_main_sleep(s) for s in sessions.sessions if not s.is_main]
    recent = span.end >= req.today - dt.timedelta(days=RELATIVE_NIGHT_DAYS)
    # 깬 날별 잠든 시각. 결과의 수면 기록에 없는 날은 두 줄을 계산한 보조 조회의 값을 써 두 줄과 같은 이름으로 부른다
    starts = ({} if lines is None else lines.starts) | {n.date: n.start_at for n in mains}
    before = starts.get(req.today - dt.timedelta(days=1))
    result = SleepRecords(
        period=span,
        relative_nights=format_relative_nights(req.today, starts.get(req.today), before) if recent else None,
        spo2_low=low,
        spo2_note=SPO2_DEVICE_FACT if shown else None,
        sleep_time_note=SLEEP_TIME_NOTE if nights else None,
        nights=nights,
        summary=summary,
        measure_summary=measure,
        non_main_sleeps=non_main,
        non_main_note=None if non_main is None else (NON_MAIN_NOTE if non_main else NO_NON_MAIN),
        sleep_vs_baseline=None if lines is None else lines.sleep_vs_baseline,
        last_night_vs_week=None if lines is None else lines.last_night_vs_week,
    )
    label = _label(span)
    line = f"{label} 평균 수면 {summary.avg_total.display}" if summary.avg_total else f"{label} 수면 기록 없음"
    line += f" ({summary.coverage})"
    if summary.bedtime is not None:
        line += f", 평균 취침 {summary.bedtime.average}"
    draft = EvidenceDraft("get_sleep_records", PUBLIC_METRICS["sleep"], span, line, row.snapshot)
    empty = {name for name in _SLEEP_OPTIONAL if getattr(result, name) is None}
    outcome = ToolOutcome("ok", result.model_dump(mode="json", exclude=empty or None), (draft,))
    if not inp.include_non_main:
        return outcome
    if sessions is None or non_main is None:
        return detail_failed(outcome)
    # 기본 근거는 지금과 같은 주 수면 원형이고, 전체 수면 세션은 kind 근거로 하나 더 만든다
    text = f"{label} 수면 세션 {len(sessions.sessions)}개 (주 수면이 아닌 잠 {len(non_main)}개)"
    return ToolOutcome("ok", outcome.payload, with_detail(draft, "sleep_sessions", sessions.snapshot, summary=text))


def _ends_today_or_yesterday(end: dt.date, today: dt.date) -> bool:
    return end >= today - dt.timedelta(days=1)


def _shows_lines(span: PeriodSpan, today: dt.date) -> bool:
    """기본 조회에 두 줄을 붙이는 기간인지. 오늘이나 어제로 끝나고 LINES_MAX_DAYS 일 이하다."""
    return _ends_today_or_yesterday(span.end, today) and (span.end - span.start).days < LINES_MAX_DAYS


@dataclass(frozen=True, slots=True)
class _BasicLines:
    sleep_vs_baseline: str
    last_night_vs_week: str
    # 보조 조회한 주 수면의 깬 날별 잠든 시각. 수면 기록 결과의 안내 문장(relative_nights)이 쓴다
    starts: dict[dt.date, dt.datetime]


async def _basic_lines(req: ToolRequest) -> _BasicLines | None:
    """오늘 기준 줄어드는 중 판정 값과 어젯밤 대 그 전 7일 문장 (calculation-rules.md 11.3, 11.6).

    오늘까지 42일(최근 14일과 그 전 28일)의 주 수면을 보조 조회로 읽어 계산한다. 기간과 관계없이 오늘이 기준일이라
    어젯밤은 오늘 깬 주 수면이다. 보조 조회가 실패하면(조회 실패, 준비 중) None 이고 기본 결과는 그대로 나간다.
    """
    span = _span(f"recent_days:{DECLINING_RECENT_DAYS + BASELINE_DAYS}", "sleep", req.today)
    try:
        row = await _lookup(req, "sleep", span, port=req.aux_port)
    except (HealthDataUnavailableError, HealthDataNotReadyError):
        return None
    data = _coaching_input(req.today, [row])
    return _BasicLines(
        declining_line(sleep_vs_baseline(data, req.today), req.today),
        last_night_line(last_night_vs_week(data, req.today), req.today),
        {n.date: n.start_at for n in row.detail.nights},
    )


def _coaching_input(
    today: dt.date, sleeps: Sequence[MetricResult], day_rows: Sequence[MetricResult] = ()
) -> SleepCoachingInput:
    """수면 조회 결과와 낮 기록(걸음·심박·운동 순서, factors 만)을 수면 코칭 계산 입력으로 바꾼다.

    수면 조회 결과가 여럿이면 주 수면을 깬 날짜 순으로 합친다. 연동 시작일과 동기화 기준 시각은 회원 단위 값이라 첫
    수면 결과의 값을 쓴다. 계산 입력의 검사(깬 날짜, 시간대 등)에 맞지 않는 결과는 연동 구현의 결함이라
    ResultContractError 다.
    """
    try:
        days = ()
        if day_rows:
            steps, heart, exercise = day_rows
            days = day_records(steps.days, heart.days, exercise_ends(exercise.exercise_sessions()))
        nights = {n.date: n for sleep in sleeps for n in sleep.detail.nights}
        return SleepCoachingInput(
            today=today,
            linked_from=sleeps[0].summary.linked_from,
            synced_until=sleeps[0].data_status.synced_until,
            nights=night_records([nights[d] for d in sorted(nights)]),
            days=days,
        )
    except ValueError as e:
        raise ResultContractError("조회 결과를 수면 코칭 계산 입력으로 바꿀 수 없다") from e


def _analysis_lookups(kind: SleepAnalysis, r: DateRange) -> tuple[DateRange, DateRange | None]:
    """분석 기간 r 의 계산에 쓰는 주 수면 조회 기간과 낮 기록(걸음·심박·운동) 조회 기간. 낮 기록은 factors 만 쓴다."""
    match kind:
        case "pattern":
            # 본인 기준은 기간 시작 전 28일이고, 줄어드는 중은 기간 끝까지 14일과 그 전 28일이다
            back = DECLINING_RECENT_DAYS - 1 + BASELINE_DAYS
            start = min(r.start - dt.timedelta(days=BASELINE_DAYS), r.end - dt.timedelta(days=back))
            return DateRange(start, r.end), None
        case "signals":
            # 수면 중 심박은 기간 끝까지 7일과 그 앞 28일(기준선)을 비교한다
            back = SLEEP_HR_RECENT_DAYS - 1 + BASELINE_DAYS
            return DateRange(min(r.start, r.end - dt.timedelta(days=back)), r.end), None
        case "factors":
            # 조건 날짜 D 는 깬 날 전날이고, 전날 짧게 잔 날·연달아 늦게 잔 날은 D 와 D−1 에 깬 주 수면을 쓴다
            day = dt.timedelta(days=1)
            return DateRange(r.start - 2 * day, r.end), DateRange(r.start - day, r.end - day)
    raise ValueError(f"알 수 없는 분석: {kind}")


def _compared_ranges(kind: SleepAnalysis, r: DateRange) -> list[DateRange]:
    """결과 글이 분석 기간 r 과 비교한 앞선 기간 중 근거로 내보내는 주 수면 기간.

    pattern 은 기간 앞 28일(본인 기준, 결과 글의 평소)이다. signals 의 수면 중 심박 기준선(기간 끝까지 7일 앞의
    28일)은 분석 기간 앞에서 끝날 때만 넣는다. 분석 기간과 겹치는 기준선은 근거 카드에 비교로 보이지 않고, 다른 분석의
    근거와 함께 오면 같은 지표의 기간이 셋 이상이 되어 앱이 하나로 합쳐 보인다. factors 는 앞선 기간과 비교하지 않는다.
    """
    day = dt.timedelta(days=1)
    match kind:
        case "pattern":
            return [DateRange(r.start - BASELINE_DAYS * day, r.start - day)]
        case "signals":
            recent_start = r.end - (SLEEP_HR_RECENT_DAYS - 1) * day
            baseline = DateRange(recent_start - BASELINE_DAYS * day, recent_start - day)
            return [baseline] if baseline.end < r.start else []
    return []


def _uncovered(r: DateRange, parts: Sequence[DateRange]) -> list[DateRange]:
    """r 의 날짜 중 parts 가 덮지 않는 날짜를 이어진 기간으로 묶어 날짜 순으로 반환한다."""
    covered = {d for part in parts for d in part.days()}
    pieces: list[DateRange] = []
    for d in r.days():
        if d in covered:
            continue
        if pieces and pieces[-1].end + dt.timedelta(days=1) == d:
            pieces[-1] = DateRange(pieces[-1].start, d)
        else:
            pieces.append(DateRange(d, d))
    return pieces


def _lookup_span(r: DateRange, metric: Metric, today: dt.date) -> PeriodSpan:
    """계산에 쓰는 조회 기간의 PeriodSpan. 기간 표현은 연도를 붙인 날짜 범위다."""
    return _span(f"dates:{r.start.isoformat()}..{r.end.isoformat()}", metric, today)


def _asked_label(span: PeriodSpan, today: dt.date) -> str:
    """질문한 기간의 이름과 날짜 ("지난주(10월 12일-18일)", "오늘(10월 22일)"). 이름 기간은 이름 뒤에 날짜를 붙이고,
    날짜로 지정한 기간은 날짜만 쓴다."""
    if span.display is None:
        return f"{PERIOD_LABELS[span.period]}({range_text(DateRange(span.start, span.end), today)})"
    return span.display.replace(" (", "(")


def _analysis_too_old(earliest: dt.date) -> str:
    return (
        f"수면 분석은 기간 앞의 평소와 비교할 기록까지 조회해 {earliest.year}년 {earliest.month}월 {earliest.day}일"
        "(12개월 전)보다 앞선 기록이 필요한 기간은 분석할 수 없어요. "
        "이 기간은 분석할 수 없다고 말하고 더 최근 기간을 제안해 주세요"
    )


async def _sleep_analysis(inp: GetSleepRecords, kind: SleepAnalysis, req: ToolRequest) -> ToolOutcome:
    """수면 코칭 분석. 계산은 tools/sleep_coaching.py, 결과 문장은 tools/sleep_coaching_text.py 가 한다.

    분석 기간은 질문의 기간이고, 없으면 분석마다의 기본 기간(오늘까지 14일·28일)이다. 본인 기준과 비교하는 앞선 기간은
    같은 호출에서 함께 조회한다. 근거는 분석 기간의 주 수면 조회와 결과 글이 비교한 앞선 기간의 주 수면 조회이고 지표
    원형 그대로다 (_compared_ranges). 그 밖에 계산에 쓰는 날짜는 근거 없이 조회한다.
    factors 는 _factors_analysis 가 한다.
    pattern 은 기간이 오늘이나 어제로 끝나면 기본 조회와 같은 수면 시간 문장과 어젯밤 대 그 전 7일 문장을 넣는다.
    기준일이 오늘이라 오늘 깬 수면까지 같은 호출에서 조회하고, 판정은 기간 안의 수면으로만 한다.
    """
    expression = inp.period or f"recent_days:{ANALYSIS_DAYS[kind]}"
    try:
        span = _span(expression, "sleep", req.today)
    except PeriodError as e:
        return _unavailable("get_sleep_records", "period", expression, e)
    if kind == "factors":
        return await _factors_analysis(inp, expression, span, req)
    period = DateRange(span.start, span.end)
    sleep_range, _ = _analysis_lookups(kind, period)
    last_night = kind == "pattern" and _ends_today_or_yesterday(period.end, req.today)
    if last_night:
        sleep_range = DateRange(sleep_range.start, req.today)
    too_old = _analysis_too_old_outcome(sleep_range, expression, req.today)
    if too_old is not None:
        return too_old
    spans = [span, *(_lookup_span(r, "sleep", req.today) for r in _compared_ranges(kind, period))]
    analysis = await _analysis_data(req, sleep_range, spans, None)
    data = analysis.data
    label = _asked_label(span, req.today)
    text = (
        pattern_text(pattern(data, period), req.today, label)
        if kind == "pattern"
        else signals_text(signals(data, period), req.today, label)
    )
    result = SleepAnalysisResult(
        analysis=kind,
        text=text,
        period=span,
        non_main_note=ANALYSIS_MAIN_ONLY if inp.include_non_main else None,
        sleep_time_note=SLEEP_TIME_NOTE if last_night else None,
        last_night_vs_week=last_night_line(last_night_vs_week(data, req.today), req.today) if last_night else None,
    )
    main, rest = _analysis_evidence(kind, analysis)
    return ToolOutcome("ok", result.model_dump(mode="json", exclude_none=True), (main,), event_only_evidence=rest)


async def _factors_analysis(inp: GetSleepRecords, expression: str, span: PeriodSpan, req: ToolRequest) -> ToolOutcome:
    """생활 요인 비교(11.4). 요청한 기간에 운동 조건 비교가 하나도 안 되면 운동 비교에 최근 28일을 쓴다.

    - 14일 이상: 요청한 기간으로 계산하고, 운동 조건(운동한 날, 늦은 운동) 비교가 하나도 안 되면 최근 28일로 다시
      계산한다. 최근 28일에서 운동 조건 비교가 되면 그 결과를 쓰고 첫 문장에 다시 계산한 이유를 쓴다. 최근 28일에서도
      안 되거나 요청한 기간이 최근 28일을 모두 덮으면 요청한 기간의 결과를 그대로 쓴다.
    - 14일보다 짧은 기간: 조건별 비교를 하지 않고 그 기간의 날짜별 수면 시간, 가장 오래·짧게 잔 날, 생활 요인 비교는
      14일 이상에서만 계산한다는 문장을 쓴다. 이번 질문(이어진 대화면 바로 앞 사용자 질문까지)에 운동을 뜻하는 말이
      있고(req.asks_exercise) 그 기간이 최근 28일 안이면, 최근 28일의 운동 조건 비교를 exercise_comparison 에 따로
      덧붙인다. 조회는 최근 28일 범위로 두 값을 함께 계산한다. 그 밖에는 그 기간의 주 수면만 조회하고 덧붙이지 않는다.
    인자의 기간 밖으로 계산에 쓴 최근 28일은 extra_period 로 반환해 호출 로그와 실행 기록에 인자의 기간과 함께
    기록되게 한다. 근거는 결과 글의 기간(다시 계산했으면 최근 28일)의 주 수면 조회와 계산에 쓴 낮 기록(걸음·심박·운동)
    조회이고, 낮 기록은 근거 이벤트로만 보낸다. 덧붙였으면 주 수면 근거는 요청한 기간이고 낮 기록은 최근 28일의 조건
    날짜다. 조건 날짜 때문에 함께 읽는 앞 이틀과 요청한 기간 밖의 주 수면은 계산에만 쓴다.
    """
    period = DateRange(span.start, span.end)
    recent = DateRange(req.today - dt.timedelta(days=FACTORS_DAYS - 1), req.today)
    exercise_block = None
    extra = None
    shown = span
    if (period.end - period.start).days + 1 < FACTORS_MIN_DAYS:
        appended = req.asks_exercise and period.start >= recent.start
        sleep_range, day_range = _analysis_lookups("factors", recent) if appended else (period, None)
        too_old = _analysis_too_old_outcome(sleep_range, expression, req.today)
        if too_old is not None:
            return too_old
        analysis = await _analysis_data(req, sleep_range, [span], day_range)
        text = short_factors_text(short_factors(analysis.data, period), req.today, _asked_label(span, req.today))
        if appended:
            exercise_block = exercise_comparison_text(factors(analysis.data, recent), req.today)
            extra = ExtraPeriod("exercise_period", recent)
    else:
        sleep_range, day_range = _analysis_lookups("factors", period)
        too_old = _analysis_too_old_outcome(sleep_range, expression, req.today)
        if too_old is not None:
            return too_old
        analysis = await _analysis_data(req, sleep_range, [span], day_range)
        asked = factors(analysis.data, period)
        text = factors_text(asked, req.today, _asked_label(span, req.today))
        covers_recent = period.start <= recent.start and period.end >= recent.end
        if not covers_recent and not compares_exercise(asked):
            recent_span = _span(f"recent_days:{FACTORS_DAYS}", "sleep", req.today)
            recent_sleep, recent_days = _analysis_lookups("factors", recent)
            recent_analysis = await _analysis_data(req, recent_sleep, [recent_span], recent_days)
            extended = factors(recent_analysis.data, recent)
            if compares_exercise(extended):
                lead = extended_factors_lead(asked, _asked_label(span, req.today), recent, req.today)
                text = f"{lead} {factors_text(extended, req.today)}"
                shown = recent_span
                extra = ExtraPeriod("extended_period", recent)
                analysis = recent_analysis
    result = SleepAnalysisResult(
        analysis="factors",
        text=text,
        period=shown,
        exercise_comparison=exercise_block,
        non_main_note=ANALYSIS_MAIN_ONLY if inp.include_non_main else None,
    )
    main, rest = _analysis_evidence("factors", analysis)
    payload = result.model_dump(mode="json", exclude_none=True)
    return ToolOutcome("ok", payload, (main,), extra_period=extra, event_only_evidence=rest)


def _analysis_too_old_outcome(sleep_range: DateRange, expression: str, today: dt.date) -> ToolOutcome | None:
    """분석에 필요한 주 수면 조회가 12개월 전보다 앞서면 조회하지 않는 안내, 아니면 None."""
    earliest = earliest_start(today)
    if sleep_range.start >= earliest:
        return None
    message = _analysis_too_old(earliest)
    return ToolOutcome("unavailable_period", {"tool": "get_sleep_records", "period": expression, "message": message})


@dataclass(frozen=True, slots=True)
class _AnalysisData:
    data: SleepCoachingInput
    # 근거로 내보낼 주 수면 조회의 기간과 결과. 첫째가 결과 글의 기간이다
    sleeps: tuple[tuple[PeriodSpan, MetricResult], ...]
    # 낮 기록(걸음·심박·운동) 조회의 지표·기간·결과. factors 만 있다
    days: tuple[tuple[Metric, PeriodSpan, MetricResult], ...]


async def _analysis_data(
    req: ToolRequest, sleep_range: DateRange, spans: Sequence[PeriodSpan], day_range: DateRange | None
) -> _AnalysisData:
    """분석 계산 입력과 근거로 내보낼 조회 결과.

    주 수면은 근거 기간(spans)마다 따로 조회하고, sleep_range 의 나머지 날짜는 계산에만 쓰는 조회로 읽는다. 근거마다
    그 기간의 조회 결과를 그대로 담는다. 대화 API 와 화면은 근거의 평균 같은 값을 다시 계산하지 않는다. 낮
    기록(걸음·심박·운동)은 day_range 가 있을 때만 조회한다. 조회는 모두 동시에 실행한다.
    """
    rest = _uncovered(sleep_range, [DateRange(span.start, span.end) for span in spans])
    sleep_spans = [*spans, *(_lookup_span(r, "sleep", req.today) for r in rest)]
    day_spans: list[tuple[Metric, PeriodSpan]] = (
        []
        if day_range is None
        else [(metric, _lookup_span(day_range, metric, req.today)) for metric in ("steps", "heart_rate", "exercise")]
    )
    rows = await _lookup_all(req, [*(("sleep", span) for span in sleep_spans), *day_spans])
    sleep_rows, day_rows = rows[: len(sleep_spans)], rows[len(sleep_spans) :]
    return _AnalysisData(
        _coaching_input(req.today, sleep_rows, day_rows),
        tuple(zip(spans, sleep_rows[: len(spans)], strict=True)),
        tuple((metric, span, row) for (metric, span), row in zip(day_spans, day_rows, strict=True)),
    )


def _analysis_evidence(kind: SleepAnalysis, analysis: _AnalysisData) -> tuple[EvidenceDraft, tuple[EvidenceDraft, ...]]:
    """분석의 근거. 결과 글 기간의 주 수면은 도구 메시지에 근거 번호를 넣는 근거이고, 비교한 앞선 기간의 주 수면과
    factors 의 낮 기록은 근거 이벤트로만 보내는 근거다."""
    name = ANALYSIS_NAMES[kind]

    def draft(metric: Metric, span: PeriodSpan, row: MetricResult, used: str) -> EvidenceDraft:
        line = f"{name}{used} {_label(span)} {METRIC_LABELS[metric]} 기록 ({_coverage(row, metric).text})"
        return EvidenceDraft("get_sleep_records", PUBLIC_METRICS[metric], span, line, row.snapshot)

    (span, sleep), *compared = analysis.sleeps
    rest = [draft("sleep", s, row, "에서 비교한") for s, row in compared]
    rest += [draft(metric, s, row, "에 쓴") for metric, s, row in analysis.days]
    return draft("sleep", span, sleep, "에 쓴"), tuple(rest)


def _spo2_low(mains: Sequence[ResultNight], today: dt.date) -> str | None:
    """90% 미만으로 기록된 날 수·기록 횟수·시각. 90% 미만 기록 수가 있는 수면 기록이 없으면 None 이다.

    시각은 90% 미만 기록이 있는 잠의 시각이 모두 있을 때만 쓴다(하나라도 재집계 전이면 이른 순을 정할 수 없다).
    이른 순 SPO2_TIMES_SHOWN 개를 그 잠의 깬 날과 시간대 말을 붙인 시각으로 쓰고, 나머지는 "외 N회"다.
    N 은 기록 횟수 − 보여 준 개수이고, 같은 시각이 둘이면 둘 다 센다. 시각 표시는 이상 신호 결과 문장과 같은 함수다.
    """
    recorded = [n for n in mains if n.spo2_low_count is not None]
    if not recorded:
        return None
    low = [n for n in recorded if n.spo2_low_count]
    count = sum(n.spo2_low_count or 0 for n in low)
    text = f"산소포화도를 기록한 {len(recorded)}일 중 90% 미만으로 기록된 날 {len(low)}일, 기록 {count}회"
    if not low or any(n.spo2_low_times is None for n in low):
        return text
    shown = sorted((t, n.start_at, n.date) for n in low for t in n.spo2_low_times or ())[:SPO2_TIMES_SHOWN]
    listed = low_times_text(
        [x[0] for x in shown], [x[1] for x in shown], count - len(shown), [x[2] for x in shown], today
    )
    return f"{text} ({listed})"


def _non_main_sleep(s: ResultSleepSession) -> str:
    """주 수면이 아닌 잠 한 번을 잠든 시각 구분과 실제 수면 시간 한 줄로 쓴다 ("10월 16일 낮 38분")."""
    minutes = NO_SLEEP_MINUTES if s.sleep_minutes is None else format_minutes(s.sleep_minutes)
    return f"{format_slept(s.start_at)} {minutes}"


def _sleep_night(n: ResultNight, *, with_measures: bool) -> SleepNight:
    """주 수면 기록 하나. with_measures 가 아니면 수면 중 심박·산소포화도와 잠든 때·깬 때 글을 넣지 않는다."""
    measures = {}
    if with_measures:
        measures = dict(
            slept=format_sleep_window(n.start_at, n.end_at),
            avg_heart_rate=_heart_rate(n.avg_heart_rate),
            min_heart_rate=_heart_rate(n.min_heart_rate),
            max_heart_rate=_heart_rate(n.max_heart_rate),
            spo2_low_count=n.spo2_low_count,
            spo2_avg=_percent(n.spo2_avg),
            spo2_min=_percent(n.spo2_min),
            spo2_max=_percent(n.spo2_max),
        )
    return SleepNight(
        date=n.date,
        bedtime=f"{_clock(n.start_at):%H:%M}",
        wake=f"{_clock(n.end_at):%H:%M}",
        total_min=n.sleep_minutes,
        total=format_minutes(n.sleep_minutes) if n.sleep_minutes is not None else None,
        deep_min=n.deep_minutes,
        rem_min=n.rem_minutes,
        light_min=n.light_minutes,
        awake_min=n.awake_minutes,
        **measures,
    )


def _measure_summary(mains: Sequence[ResultNight]) -> SleepMeasureSummary | None:
    """수면 기록의 수면 중 심박·산소포화도 기간 요약. 값이 있는 수면 기록만 쓰고, 값이 하나도 없으면 None 이다.

    평균은 수면마다 기록된 평균을 다시 평균해 한 번만 반올림한다. 최저·최고는 가장 낮은·높은 값과 그 수면 기록의
    날짜이고, 같은 값이면 이른 날짜다.
    """

    def average(name: str, unit: Unit) -> SleepMeasureValue | None:
        values = [getattr(n, name) for n in mains if getattr(n, name) is not None]
        if not values:
            return None
        mean = float(_quantize(unit, sum(map(_decimal, values)) / len(values)))
        return SleepMeasureValue(value=mean, display=format_value(unit, mean), records=len(values))

    def extreme(name: str, unit: Unit, *, lowest: bool) -> DatedSleepMeasureValue | None:
        points = [(getattr(n, name), n.date) for n in mains if getattr(n, name) is not None]
        if not points:
            return None
        found = (min if lowest else max)(value for value, _ in points)
        value = _round(unit, found)
        day = min(d for v, d in points if v == found)
        return DatedSleepMeasureValue(value=value, display=format_value(unit, value), date=day, records=len(points))

    # 90% 미만 기록이 있는 밤 수는 평균보다 앞의 spo2_low 문장에 쓴다
    values = dict(
        avg_heart_rate=average("avg_heart_rate", "bpm"),
        min_heart_rate=extreme("min_heart_rate", "bpm", lowest=True),
        max_heart_rate=extreme("max_heart_rate", "bpm", lowest=False),
        spo2_avg=average("spo2_avg", "%"),
        spo2_min=extreme("spo2_min", "%", lowest=True),
    )
    if all(v is None for v in values.values()):
        return None
    return SleepMeasureSummary(**values, note=MEASURE_SUMMARY_NOTE)


def _signed(display: str, value: Decimal) -> str:
    if value > 0:
        return f"+{display}"
    return f"-{display}" if value < 0 else display


def _difference(unit: Unit, current: float, baseline: float) -> tuple[Value, Value | None]:
    """공개 평균끼리의 차이와 변화율. 둘 다 공개 평균에서 바로 계산해 한 번만 반올림한다.

    표시 평균끼리 빼면 반올림이 두 번 들어가 차이가 1씩 어긋날 수 있다.
    """
    raw = Decimal(str(current)) - Decimal(str(baseline))
    diff = _quantize(unit, raw)
    # 반올림해 0 이 된 음수(-0)는 부호 없는 0 으로 쓴다
    diff = diff or abs(diff)
    text = format_value(unit, float(abs(diff)))
    # 산소포화도 차이는 퍼센트포인트라 변화율(%)과 구분한다
    if unit == "%":
        text += "p"
    difference = Value(value=float(diff), display=_signed(text, diff))
    if not baseline:
        return difference, None
    rate = (raw / Decimal(str(baseline)) * 100).quantize(CHANGE_RATE_PLACES, ROUND_HALF_UP)
    rate = rate or abs(rate)
    return difference, Value(value=float(rate), display=_signed(f"{abs(rate):,}%", rate))


def _is_reliable(s: MetricSummary) -> bool:
    return (
        s.avg is not None
        and s.days_with_data >= RELIABLE_MIN_DAYS
        and s.days_with_data >= s.completed_days * RELIABLE_MIN_SHARE
    )


def _period_average(s: MetricSummary) -> PeriodAverage:
    return PeriodAverage(
        period=s.period,
        avg=s.avg,
        days_with_data=s.days_with_data,
        completed_days=s.completed_days,
        coverage=s.coverage,
        in_progress=s.in_progress,
    )


def _baseline_options(current: PeriodSpan, today: dt.date) -> list[str]:
    """current 시작일보다 앞에서 끝나는 기준 기간 후보. 이름 기간은 열거 순서로 놓는다.

    같은 종류로 바로 앞 기간을 맨 앞에 넣는다. 그 기간이 후보 이름 기간과 범위가 같으면 이름 기간만 둔다.
    """
    named = {p: resolve_period(p, today) for p in PERIODS}
    options: list[str] = [p for p, r in named.items() if r.end < current.start]
    previous = previous_period(current.period, today)
    if previous is not None and resolve_period(previous, today) not in [named[p] for p in options]:
        options.insert(0, previous)
    return options


def _invalid_periods(current: PeriodSpan, baseline: PeriodSpan, today: dt.date) -> ToolOutcome:
    options = _baseline_options(current, today)
    head = (
        "current 는 baseline 보다 늦고 겹치지 않는 기간이어야 한다. 기록을 조회하지 않았고, 기록이 없다는 뜻이 아니다. "
    )
    if options:
        tail = "baseline 을 baseline_options 중에서 골라 다시 호출한다."
    else:
        tail = "이 current 와 함께 쓸 수 있는 baseline 이 없다. current 를 더 늦은 기간으로 바꿔 다시 호출한다."
    return ToolOutcome(
        "invalid_periods",
        {
            "tool": "compare_periods",
            "current": current.model_dump(mode="json"),
            "baseline": baseline.model_dump(mode="json"),
            "baseline_options": options,
            "message": head + tail,
        },
    )


def _in_progress_only(s: MetricSummary) -> bool:
    return s.avg is None and s.completed_days == 0 and s.in_progress is not None


async def compare_periods(inp: ComparePeriods, req: ToolRequest) -> ToolOutcome:
    try:
        current = _span(inp.current, inp.metric, req.today)
    except PeriodError as e:
        return _unavailable("compare_periods", "current", inp.current, e)
    try:
        baseline = _span(inp.baseline, inp.metric, req.today)
    except PeriodError as e:
        return _unavailable("compare_periods", "baseline", inp.baseline, e)
    if current.start <= baseline.end:
        return _invalid_periods(current, baseline, req.today)
    spans = (current, baseline)
    rows = await _lookup_all(req, [(inp.metric, span) for span in spans])
    summaries = [_summary(inp.metric, span, row, req.today) for span, row in zip(spans, rows, strict=True)]
    (cur, _), (base, _) = summaries
    cur_avg, base_avg = (row.summary.avg for row in rows)
    unit = UNITS[inp.metric]
    difference, change = (None, None) if cur_avg is None or base_avg is None else _difference(unit, cur_avg, base_avg)
    reliable = _is_reliable(cur) and _is_reliable(base)
    in_progress_note = IN_PROGRESS_NOTE if _in_progress_only(cur) or _in_progress_only(base) else None
    result = PeriodComparison(
        metric=inp.metric,
        unit=unit,
        measured_during=MEASURED_DURING.get(inp.metric),
        current=_period_average(cur),
        baseline=_period_average(base),
        difference=difference,
        change_rate_pct=change,
        reliable=reliable,
        # 오늘이 진행 중이라 평균이 없는 경우는 원인이 기록 부족이 아니라 기록 부족 안내를 넣지 않는다
        reliable_note=None if reliable or in_progress_note else RELIABLE_NOTE,
        in_progress_note=in_progress_note,
    )
    # API 명세 2.3 은 비교의 기간마다 근거 하나를 둔다
    drafts = tuple(
        EvidenceDraft("compare_periods", PUBLIC_METRICS[inp.metric], span, line, row.snapshot)
        for span, (_, line), row in zip(spans, summaries, rows, strict=True)
    )
    notes = {name for name in ("reliable_note", "in_progress_note") if getattr(result, name) is None}
    return ToolOutcome("ok", result.model_dump(mode="json", exclude=notes), drafts)


async def get_health_overview(inp: GetHealthOverview, req: ToolRequest) -> ToolOutcome:
    try:
        spans = [_span(inp.period, metric, req.today) for metric in OVERVIEW_METRICS]
    except PeriodError as e:
        return _unavailable("get_health_overview", "period", inp.period, e)
    # 명세에 부분 성공이 없어 한 지표라도 조회에 실패하면 호출 전체가 실패한다
    rows = await _lookup_all(req, list(zip(OVERVIEW_METRICS, spans, strict=True)))
    summaries = [
        _summary(metric, span, row, req.today) for metric, span, row in zip(OVERVIEW_METRICS, spans, rows, strict=True)
    ]
    result = HealthOverview(**{metric: s for metric, (s, _) in zip(OVERVIEW_METRICS, summaries, strict=True)})
    drafts = tuple(
        EvidenceDraft("get_health_overview", PUBLIC_METRICS[metric], span, line, row.snapshot)
        for metric, span, (_, line), row in zip(OVERVIEW_METRICS, spans, summaries, rows, strict=True)
    )
    # 지표 넷의 비어 있는 항목(측정 최저·최고, 오늘 진행 값 등)이 많아 null 인 항목을 빼고 전달한다
    return ToolOutcome("ok", result.model_dump(mode="json", exclude_none=True), drafts)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _distance(meters: Decimal) -> str:
    return f"{format_distance(meters)} ({DISTANCE_BASIS})"


def _missing(label: str, recorded: int, count: int) -> str | None:
    if recorded == count:
        return None
    return f"{label} 기록 없음" if recorded == 0 else f"{label} 기록 없는 세션 {count - recorded}회"


def _by_type(sessions: Sequence[ResultSession]) -> dict[str, list[ResultSession]]:
    """종류 이름으로 묶는다. 횟수가 많은 종류부터, 같으면 운동 시간이 긴 종류부터 놓는다.

    원형에 기준표 등록 여부가 없어 코드가 아니라 이름으로 묶는다. 기준표에 없는 코드는 이름이 코드 0과 같은
    "기타 운동"이라 계산 규칙 3.3처럼 코드 0과 한 묶음이 된다.
    """
    groups: dict[str, list[ResultSession]] = {}
    for s in sessions:
        groups.setdefault(s.type_name, []).append(s)
    return dict(sorted(groups.items(), key=lambda g: (-len(g[1]), -sum(s.duration_minutes for s in g[1]), g[0])))


def _exercise_total(sessions: Sequence[ResultSession], type_name: str | None = None) -> ExerciseTotal:
    # 거리·칼로리는 기록이 있는 세션만 더한 뒤 한 번만 반올림한다. 없는 거리를 0이나 속도로 채우지 않는다
    distances = [_decimal(s.distance_m) for s in sessions if s.distance_m is not None]
    calories = [_decimal(s.calories_kcal) for s in sessions if s.calories_kcal is not None]
    return ExerciseTotal(
        type=type_name,
        count=len(sessions),
        duration=format_minutes(sum(s.duration_minutes for s in sessions)),
        distance=_distance(sum(distances)) if distances else None,
        distance_sessions=len(distances),
        calories=format_calories(sum(calories)) if calories else None,
        missing=_missing("칼로리", len(calories), len(sessions)),
    )


def _heart_rate(value: float | None) -> str | None:
    return None if value is None else format_value("bpm", _round("bpm", value))


def _percent(value: float | None) -> str | None:
    return None if value is None else format_value("%", _round("%", value))


def _session_item(s: ResultSession) -> ExerciseSessionItem:
    return ExerciseSessionItem(
        date=s.date,
        start=f"{_clock(s.start_at):%H:%M}",
        type=s.type_name,
        duration=format_minutes(s.duration_minutes),
        distance=None if s.distance_m is None else _distance(_decimal(s.distance_m)),
        calories=None if s.calories_kcal is None else format_calories(_decimal(s.calories_kcal)),
        avg_heart_rate=_heart_rate(s.avg_heart_rate),
        max_heart_rate=_heart_rate(s.max_heart_rate),
        counts_as_exercise=None if s.counts_as_exercise else False,
    )


def _not_counted_note(sessions: Sequence[ResultSession]) -> str | None:
    if not sessions:
        return None
    kinds = ", ".join(f"{name} {len(items)}회" for name, items in _by_type(sessions).items())
    return (
        f"운동으로 인정되지 않은 세션 {len(sessions)}회({kinds})는 합계에서 뺐다. "
        "목록에는 counts_as_exercise 가 false 로 표시된다."
    )


def _with_progress(line: str, progress: str, late: bool) -> str:
    """운동 세션 합계 문구·근거 summary 끝에 오늘 기준 시각을 붙인다.

    오늘보다 이른 기준 시각(late)은 다른 문장에 두고 그 시각이 오늘이 아니라는 문장을 붙인다.
    """
    return f"{line}. {progress}이다. {BASIS_NOT_TODAY_NOTE}" if late else f"{line}, {progress}"


def _session_coverage(row: MetricResult) -> str | None:
    s = row.summary
    # 완성된 날 없이 오늘만 있는 기간은 "0일 중 …" 대신 오늘 기준 시각(in_progress_note)만 전달한다
    if s.completed_days == 0 and s.linked_from is not None and all(d.reason != "BEFORE_LINK" for d in row.days):
        return None
    return _coverage(row, "exercise").text


async def get_exercise_sessions(inp: GetExerciseSessions, req: ToolRequest) -> ToolOutcome:
    try:
        span = _span(inp.period, "exercise", req.today, tool="get_exercise_sessions")
    except PeriodError as e:
        return _unavailable("get_exercise_sessions", "period", inp.period, e)
    row = await _lookup(req, "exercise", span)
    sessions = row.exercise_sessions()
    # 저장된 인정 여부만 따른다. 속도나 코드로 다시 판정하지 않는다
    counted = [s for s in sessions if s.counts_as_exercise]
    not_counted = [s for s in sessions if not s.counts_as_exercise]
    latest = sorted(sessions, key=lambda s: s.start_at, reverse=True)
    coverage = _session_coverage(row)
    # 기간에 오늘이 있으면 운동 기간 요약의 오늘 진행 안내와 같은 동기화 기준 시각까지의 기록이다
    synced = row.data_status.synced_until
    basis = None if synced is None else f"{format_basis_time(synced, req.today)}까지 기록 기준"
    late = basis is not None and is_before_today(synced, req.today)
    in_progress_note = None
    # 합계 문구에 붙일 기록 기준에 넣는 기준 시각. 오늘보다 이른 시각 앞에는 "오늘"을 붙이지 않는다
    progress = None
    if row.in_progress is not None:
        if basis is None:
            in_progress_note = progress = "오늘은 진행 중이라 기록이 더 들어올 수 있다"
        elif late:
            # 지표 요약의 진행 중 문장처럼 오늘보다 이른 기준 시각은 "오늘"과 다른 문장에 둔다
            in_progress_note = f"오늘은 진행 중이다. {basis}이다. {BASIS_NOT_TODAY_NOTE}"
            progress = basis
        else:
            in_progress_note = f"오늘은 진행 중이라 {basis}이다"
            progress = f"오늘 {basis}"
    total = _exercise_total(counted) if counted else None
    types = [_exercise_total(items, name) for name, items in _by_type(counted).items()]
    label = _label(span)
    if total is not None:
        head = f"{label} 운동 {total.count}회 {total.duration}"
    else:
        head = f"{label} 운동으로 인정된 기록 없음" if sessions else f"{label} 운동 기록 없음"
    line = head if coverage is None else f"{head} ({coverage})"
    answer_note = None
    if total is not None:
        # 따로 떨어진 칸의 기록 일수와 오늘 기준 시각은 답변에서 빠지기 쉬워 근거 summary 와 같은 형식으로
        # 합계 문구에 붙인다. 기준 시각이 없으면 근거 summary 와 달리 오늘 기록이 더 들어올 수 있다고 붙인다
        display = line if progress is None else _with_progress(line, progress, late)
        total = total.model_copy(update={"display": display})
        answer_note = ANSWER_NOTE.format(basis=", ".join(p for p in (coverage, progress) if p))
    items = [_session_item(s) for s in latest[:SESSION_LIST_LIMIT]]
    recorded = any(item.calories or item.distance for item in [*types, *items])
    result = ExerciseSessions(
        period=span,
        total=total,
        answer_note=answer_note,
        # 합계는 목록 개수와 관계없이 기간 전체 세션으로 계산한다
        types=types,
        type_note=TYPE_NOTE if len(types) > 1 else None,
        distance_note=DISTANCE_NOTE if total is not None and total.distance is not None else None,
        calorie_distance_note=CALORIE_DISTANCE_NOTE if recorded else None,
        sessions=items,
        omitted_sessions=max(len(latest) - SESSION_LIST_LIMIT, 0) or None,
        not_counted_sessions=len(not_counted),
        not_counted_note=_not_counted_note(not_counted),
        no_record=None if sessions else NO_SESSION_RECORD,
        coverage=coverage if total is None else None,
        in_progress_note=in_progress_note if total is None else None,
    )
    if progress is not None and basis is not None:
        line = _with_progress(line, progress, late)
    draft = EvidenceDraft("get_exercise_sessions", PUBLIC_METRICS["exercise"], span, line, row.snapshot)
    # 세션마다 비어 있는 항목(거리·칼로리·심박)이 많아 null 인 항목을 빼고 전달한다
    return ToolOutcome("ok", result.model_dump(mode="json", exclude_none=True), (draft,))


def _grade(grade: int | None) -> ScoreGradeLabel | None:
    return None if grade is None else SCORE_GRADE_LABELS[grade]


def _activity_area(a: ScoreActivity) -> ScoreAreaResult:
    if a.score is None:
        return ScoreAreaResult(missing="기록 없음")
    parts = []
    if a.steps.value is not None and a.steps.score is not None:
        steps = format_value("steps", a.steps.value)
        parts.append(f"{steps}으로 걸음 점수 {a.steps.score}점({STEPS_MAX_POINTS}점 만점)")
    if a.exercise.minutes is not None and a.exercise.score is not None:
        minutes = format_minutes(a.exercise.minutes)
        parts.append(f"인정 운동 {minutes}으로 운동 점수 {a.exercise.score}점({EXERCISE_MAX_POINTS}점 만점)")
    return ScoreAreaResult(score=a.score, grade=_grade(a.grade), used=", ".join(parts) or None)


def _sleep_area(s: ScoreSleep) -> SleepScoreAreaResult:
    """수면 영역. 점수를 어떻게 매기는지 묻는 질문에도 답할 수 있게 점수가 없어도 점수 기준을 넣는다."""
    if s.score is None:
        return SleepScoreAreaResult(missing="기록 없음", rule=SLEEP_SCORE_RULE)
    used = None if s.sleep_minutes is None else f"이날 깬 주 수면의 실제 수면 {format_minutes(s.sleep_minutes)}"
    targets = None if s.sleep_minutes is None else sleep_targets(s.sleep_minutes, s.score)
    return SleepScoreAreaResult(score=s.score, grade=_grade(s.grade), used=used, rule=SLEEP_SCORE_RULE, targets=targets)


def _heart_area(h: ScoreHeart) -> tuple[ScoreAreaResult, list[str]]:
    """심박 영역과 그 안내. 평소 값과의 차이는 코칭에만 쓰는 계산이라 여기서 한 번만 반올림한다."""
    used = None if h.sleep_hr_avg is None else f"수면 중 평균 심박 {format_value('bpm', _round('bpm', h.sleep_hr_avg))}"
    if h.score is None:
        # 평소 값이 없어 점수가 없어도 홈 화면의 안정 시 심박수와 같은 수면 중 평균 심박은 전달한다
        missing = "평소 값 없음" if used is not None and h.baseline_hr_avg is None else "기록 없음"
        return ScoreAreaResult(missing=missing, used=used), []
    if h.sleep_hr_avg is None or h.baseline_hr_avg is None:
        return ScoreAreaResult(score=h.score, grade=_grade(h.grade), used=used), []
    diff = _quantize("bpm", Decimal(str(h.sleep_hr_avg)) - Decimal(str(h.baseline_hr_avg)))
    baseline = f"평소 {format_value('bpm', _round('bpm', h.baseline_hr_avg))}({h.baseline_nights}일 평균)"
    if diff:
        compared = f"{baseline}보다 {format_value('bpm', float(abs(diff)))} {'높음' if diff > 0 else '낮음'}"
    else:
        compared = f"{baseline}과 같음"
    notes = []
    if h.check_wear:
        notes.append(SCORE_CHECK_WEAR_NOTE)
    elif diff < 0:
        notes.append(SCORE_LOWER_HEART_NOTE)
    return ScoreAreaResult(score=h.score, grade=_grade(h.grade), used=f"{used}, {compared}"), notes


def _lifestyle_score(span: PeriodSpan, row: LifestyleScoreResult, today: dt.date) -> LifestyleScore:
    """포트의 점수 응답을 LLM 결과로 바꾼다. 점수·등급은 저장값 그대로 쓰고 다시 계산하지 않는다."""
    if row.reason is not None:
        return LifestyleScore(
            period=span, total=ScoreTotalResult(score=None, display="점수 없음"), missing=MISSING_LABELS[row.reason]
        )
    activity = _activity_area(row.areas.activity)
    sleep = _sleep_area(row.areas.sleep)
    heart, heart_notes = _heart_area(row.areas.heart)
    grades = [area.grade for area in (activity, sleep, heart) if area.grade is not None]
    notes = [SCORE_REASON_NOTE] if grades else []
    if "관리" in grades:
        notes.append(SCORE_CARE_NOTE)
    if row.areas.activity.score is not None and row.areas.activity.exercise.minutes == 0:
        notes.append(SCORE_REST_DAY_NOTE)
    notes += heart_notes
    score = row.total.score
    note = None
    if score is not None:
        note = f"{SCORE_TOTAL_NOTE}. {SCORE_PARTIAL_NOTE}" if row.total.missing_areas else SCORE_TOTAL_NOTE
    in_progress = None
    if row.is_in_progress:
        as_of = None if row.computed_at is None else row.computed_at.astimezone(_KST)
        basis = "" if as_of is None else f" ({format_basis_time(as_of, today)} 계산 기준)"
        head = add_not_today_note(f"오늘 진행 중인 점수{basis}", as_of, today)
        in_progress = ScoreInProgress(date=row.date, display=f"{head}. {SCORE_IN_PROGRESS_NOTE}", as_of=as_of)
    return LifestyleScore(
        period=span,
        in_progress=in_progress,
        total=ScoreTotalResult(score=score, display="점수 없음" if score is None else f"{score}점", note=note),
        activity=activity,
        sleep=sleep,
        heart=heart,
        notes=notes or None,
    )


def _score_unavailable(period: str, today: dt.date) -> ToolOutcome:
    """하루가 아니거나 조회할 수 없는 날짜의 기간. 점수가 하루 단위라는 것과 조회할 수 있는 날짜 범위만 전달한다."""
    dates = range_text(DateRange(earliest_start(today), today), today)
    message = f"생활 습관 점수는 날짜 하루 단위로만 있다. 조회할 수 있는 날짜는 {dates}(오늘)이다"
    return ToolOutcome("unavailable_period", {"tool": "get_lifestyle_score", "period": period, "message": message})


async def get_lifestyle_score(inp: GetLifestyleScore, req: ToolRequest) -> ToolOutcome:
    try:
        r = resolve_period(inp.period, req.today, max_days=1)
    except PeriodError:
        return _score_unavailable(inp.period, req.today)
    span = PeriodSpan(
        period=inp.period, start=r.start, end=r.end, basis="날짜(KST)", display=period_label(inp.period, r, req.today)
    )
    row = await req.port.get_lifestyle_score(req.user_id, r.start)
    if row.date != r.start:
        raise ResultContractError("점수 날짜가 요청과 다르다")
    result = _lifestyle_score(span, row, req.today)
    # 근거 검증·저장은 지표 원형(API 명세 2.2)만 받아 점수 결과는 근거로 내보내지 않는다.
    # 점수가 없는 영역은 score 대신 missing 만 전달한다
    return ToolOutcome("ok", result.model_dump(mode="json", exclude_none=True))


HANDLERS: dict[str, ToolHandler] = {
    "get_metric_summary": get_metric_summary,
    "get_sleep_records": get_sleep_records,
    "get_daily_trend": get_daily_trend,
    "compare_periods": compare_periods,
    "get_health_overview": get_health_overview,
    "get_exercise_sessions": get_exercise_sessions,
    "get_lifestyle_score": get_lifestyle_score,
    "get_daily_message": daily_message.get_daily_message,
}
