from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from coach_ai.tools.periods import PERIODS as PERIODS
from coach_ai.tools.periods import Period as Period
from coach_ai.tools.periods import check_period

# strict 모드에서 Enum 필드는 dict 입력의 문자열을 거부하므로 Literal로 열거한다
Metric = Literal["steps", "heart_rate", "oxygen_saturation", "sleep", "exercise"]
# 기간 표현. 형식만 확인하고, 수의 범위와 조회 한도는 오늘 날짜로 처리 함수가 검사한다
PeriodExpr = Annotated[str, AfterValidator(check_period)]
# 수면 코칭 분석 (calculation-rules.md 11장). pattern 은 11.2·11.3, signals 는 11.5, factors 는 11.4 다.
# factors 는 늘 11.4 의 조건 일곱 개를 모두 비교한다
SleepAnalysis = Literal["pattern", "signals", "factors"]

METRICS: tuple[Metric, ...] = get_args(Metric)
# 전체 요약(get_health_overview)이 묶는 지표. 홈 화면 카드와 같은 순서이고, 도구 설명과 결과 모델(HealthOverview)도
# 이 네 지표만 다룬다. 산소포화도는 기간 요약 도구로 따로 조회한다
OVERVIEW_METRICS: tuple[Metric, ...] = ("steps", "exercise", "sleep", "heart_rate")

# API 명세의 공개 지표 코드. error 이벤트의 metrics에 쓴다
PublicMetric = Literal["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
PUBLIC_METRICS: dict[Metric, PublicMetric] = {
    "steps": "steps",
    "heart_rate": "heart_rates",
    "oxygen_saturation": "oxygen_saturations",
    "sleep": "sleeps",
    "exercise": "exercises",
}


def public_metrics(metrics: Iterable[Metric]) -> list[PublicMetric]:
    """도구 지표 이름을 공개 지표 코드로 바꾼다. 순서는 지키고 중복은 뺀다."""
    return list(dict.fromkeys(PUBLIC_METRICS[m] for m in metrics))


_METRIC_DESC = (
    "지표. steps=하루 걸음 수, heart_rate=하루 평균 심박수(기간 중 측정 최저·최고도 함께 준다), "
    "oxygen_saturation=주 수면 중 산소포화도 평균(수면 중 측정값만 있고 수면 종료일로 묶는다), "
    "sleep=주 수면의 실제 수면 시간(수면 종료일로 묶는다), exercise=하루 운동 시간"
)
# 기간 칸마다 같은 설명이 들어가 형식만 적는다. 표현 규칙과 예는 시스템 프롬프트에 한 번만 둔다.
# ".." 는 범위(dates:, 시작..끝)에만 쓰므로 요일은 weekday:요일 로 적는다
_PERIOD_DESC = (
    "기간(한국 시간). days_ago:N, recent_days:N, weeks_ago:N, months_ago:N, recent_months:N, "
    "date:MM-DD, dates:MM-DD..MM-DD, weekday:요일"
)


# 모델 docstring과 필드 description은 LLM에 도구 설명으로 그대로 전달된다.
# 사용자를 가리키는 필드는 두지 않는다. 조회 대상 사용자는 서버의 CoachContext로만 정해진다.
class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _without_default(schema: dict[str, Any]) -> None:
    # 도구 정의는 모든 호출의 고정 입력이라 기본값이 null 인 선택 칸은 null 선택지(SkipJsonSchema)와 기본값을
    # 적지 않는다. 선택 칸인 것은 required 에 없는 것으로 전달된다
    schema.pop("default", None)


class GetMetricSummary(ToolInput):
    """한 지표의 기간 요약을 조회한다. 하루만 조회하는 기간(오늘, 어제, 날짜·요일 하루)의 값도 이 도구로 조회한다.
    기간의 평균·합계(걸음 수·운동 시간)·기록 일수와 빠진 날, 기간 중 하루 값이 가장 낮은·높은 날,
    심박의 기간 측정 최저·최고는 이 도구로 조회한다.
    운동은 하루·기간의 시간·횟수·칼로리·거리를 반환한다.
    가장 낮은·높은 날(lowest_day·highest_day)의 값은 그날 하루 값이고, 하루 안에서 측정한 최저·최고가 아니다."""

    metric: Metric = Field(description=_METRIC_DESC)
    period: PeriodExpr = Field(description=_PERIOD_DESC)


class GetDailyTrend(ToolInput):
    """한 지표의 여러 날 값을 날짜별로 조회한다.
    날짜별 값의 흐름이나 목록, 주와 요일로 말한 날(지난주 X요일)의 하루 값, 날짜끼리 비교는 이 도구로 조회한다.
    수면은 날짜별 수면 시간의 흐름이나 비교만 이 도구로 조회하고, 밤별 기록은 get_sleep_records 로 조회한다.
    날짜별 값은 걸음 수는 그날 합계, 심박은 그날 평균, 수면은 그날 주 수면 시간, 산소포화도는 그날 주 수면 중 평균이다.
    값이 없는 날은 missing 에 결측 사유(기록 없음, 동기화 전, 연동 전)를 담는다.
    운동은 날짜마다 시간·횟수·칼로리·거리를 반환하며, 운동 0인 날을 셀 때도 쓴다.
    운동 종류는 나누지 않는다.
    기간의 평균·합계, 가장 낮은·높은 날, 측정 최저·최고, 하루만 조회하는 기간의 값은 get_metric_summary 로 조회한다.
    같은 지표·같은 기간을 get_metric_summary 와 get_daily_trend 로 함께 조회하지 않는다."""

    metric: Metric = Field(description=_METRIC_DESC)
    period: PeriodExpr = Field(description=_PERIOD_DESC)


class GetSleepRecords(ToolInput):
    """밤별 기록(취침·기상 시각, 총 수면, 단계별 시간)은 이 도구로 조회한다.
    수면 기록을 보여 달라는 질문, 잠든·깬 시각, 수면 단계(깊은 잠·렘수면·얕은 잠)를 묻는 질문이 해당한다.
    낮에 잔 주 수면도 포함하나 별도 낮잠은 제외한다. 기간 요약도 반환한다."""

    # 분석은 질문에 기간이 없으면 처리 함수가 분석마다의 기본 기간을 쓴다. 분석이 없으면 기간이 있어야 한다
    period: PeriodExpr | SkipJsonSchema[None] = Field(
        default=None, description=_PERIOD_DESC, json_schema_extra=_without_default
    )
    # 도구 정의는 모든 호출의 고정 입력이라 설명은 필드에 한 문장으로만 둔다
    include_non_main: bool = Field(default=False, description="낮잠을 물을 때 참")
    # 분석을 넣는 조건은 도구 설명이 아니라 이 칸 설명에만 두고 지표 이름은 적지 않는다. 도구 설명에 조건을 적거나
    # 칸 설명에 지표 이름을 적으면 분석이 아닌 수면·산소포화도 질문에도 이 도구가 불린다
    analysis: SleepAnalysis | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "수면을 평가하거나 이유, 수면 습관, 개선 방법, 수면 중 이상 신호, 조건에 따른 차이를 물을 때 넣는다. "
            "평가·이유·수면 습관·개선 방법은 pattern, 이상 신호는 그 이유를 물어도 signals, "
            "운동·걸음·주말 같은 조건에 따른 차이는 factors. "
            "얼마나 잤는지 같은 사실은 넣지 않는다. 질문에 기간이 없으면 period 를 넣지 않는다"
        ),
        json_schema_extra=_without_default,
    )

    @model_validator(mode="after")
    def _period_without_analysis(self) -> GetSleepRecords:
        if self.analysis is None and self.period is None:
            raise ValueError("analysis 가 없는 수면 기록 조회는 period 가 있어야 한다")
        return self


class ComparePeriods(ToolInput):
    """한 지표의 두 기간 평균을 비교한다(차이, 변화율).
    결과에는 두 기간의 평균과 기록 일수도 함께 들어 있다."""

    metric: Metric = Field(description=_METRIC_DESC)
    current: PeriodExpr = Field(description="비교할 기간. " + _PERIOD_DESC)
    baseline: PeriodExpr = Field(description="기준 기간. " + _PERIOD_DESC)

    @model_validator(mode="after")
    def _distinct_periods(self) -> ComparePeriods:
        if self.current == self.baseline:
            raise ValueError("current와 baseline은 서로 다른 기간이어야 한다")
        return self


class GetHealthOverview(ToolInput):
    """걸음 수·운동 시간·수면·심박수 4개 지표의 기간 요약을 한 번에 조회한다."""

    period: PeriodExpr = Field(description=_PERIOD_DESC)


class GetExerciseSessions(ToolInput):
    """운동 종류별 횟수·거리·칼로리, 운동 시각·운동 중 심박·인정 여부와 최근 세션 목록을 조회한다.
    종류를 말한 운동을 한 날을 찾을 때도 쓴다.
    운동별 수면 비교는 get_sleep_records(analysis=factors)와 같은 기간으로 함께 조회한다.
    운동이 배경인 다른 지표의 기간 비교에는 쓰지 않는다.
    운동 전체의 시간·횟수·칼로리·거리 합계·평균은 하루·기간 모두 get_metric_summary(metric=exercise)로 조회한다.
    운동 이름이 모호하면 먼저 조회해 해당 종류가 여럿일 때만 되묻고, 하나면 그 종류로 답한다.
    예: 헬스는 근력 운동과 웨이트 트레이닝에 걸친다."""

    period: PeriodExpr = Field(description=_PERIOD_DESC)


class GetLifestyleScore(ToolInput):
    """하루의 생활 습관 점수(홈 화면의 오늘의 점수)를 조회한다. 저장된 점수를 그대로 반환한다.
    오늘의 점수, 활동·수면·심박 점수와 등급(훌륭·안정·관심·관리), 점수가 그렇게 나온 이유, 점수를 어떻게 계산하는지,
    점수를 올리는 방법을 물을 때와 홈 화면의 안정 시 심박수(수면 중 평균 심박)를 물을 때만 쓴다.
    점수나 등급을 묻지 않으면 쓰지 않는다. 수면·심박 점수는 그날 깬 잠으로 매긴다."""

    # 점수는 하루씩 조회해 하루 표현만 보여 준다. 여러 날 표현은 처리 함수가 조회하지 않고 안내한다
    period: PeriodExpr = Field(description="점수 날짜 하루(한국 시간). days_ago:N, date:MM-DD, weekday:요일")


class GetDailyMessage(ToolInput):
    """홈 화면의 오늘의 한마디(오늘 기록으로 고른 응원 문구)를 조회한다. 오늘 것만 있다.
    오늘의 한마디 문구와 그 문구가 나온 이유를 물을 때만 쓴다. 오늘의 한마디를 묻지 않으면 쓰지 않는다.
    결과에는 점수와 걸음 수 같은 기록 값이 없다.
    응원이나 조언을 해 달라는 요청에는 이 도구를 부르지 않고 직접 답한다."""

    # 날짜는 서버가 정한 질문 날짜 하나라 인자가 없다


TOOL_INPUTS: dict[str, type[ToolInput]] = {
    "get_metric_summary": GetMetricSummary,
    "get_daily_trend": GetDailyTrend,
    "get_sleep_records": GetSleepRecords,
    "compare_periods": ComparePeriods,
    "get_health_overview": GetHealthOverview,
    "get_exercise_sessions": GetExerciseSessions,
    "get_lifestyle_score": GetLifestyleScore,
    "get_daily_message": GetDailyMessage,
}
