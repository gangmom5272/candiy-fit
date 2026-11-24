from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from coach_ai.tools.rounding import round_half_up
from coach_ai.tools.schemas import OVERVIEW_METRICS, Metric, PeriodExpr, SleepAnalysis

Unit = Literal["steps", "bpm", "%", "min"]
UNITS: dict[Metric, Unit] = {
    "steps": "steps",
    "heart_rate": "bpm",
    "oxygen_saturation": "%",
    "sleep": "min",
    "exercise": "min",
}

Basis = Literal["날짜(KST)", "수면 종료일(KST)"]
# 수면 세션에서 나온 값은 수면 종료일로 묶인다. 운동 세션은 시작한 날짜로 묶인다
BASES: dict[Metric, Basis] = {
    "steps": "날짜(KST)",
    "heart_rate": "날짜(KST)",
    "oxygen_saturation": "수면 종료일(KST)",
    "sleep": "수면 종료일(KST)",
    "exercise": "날짜(KST)",
}

MeasuredDuring = Literal["주 수면 중"]
# 산소포화도는 수면 중 측정값만 집계되어 하루 전체 값처럼 읽히지 않게 표시한다
MEASURED_DURING: dict[Metric, MeasuredDuring] = {"oxygen_saturation": "주 수면 중"}

ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM (KST)")]
_MONTH_DAY = r"(1[0-2]|[1-9])월 ([1-9]|[12]\d|3[01])일"
# 잠든 때와 깬 때를 실제 날짜와 시각으로 쓴 글 ("10월 18일 밤 11시 20분에 잠들어 19일 새벽 5시 12분에 깸")
SleepWindow = Annotated[str, Field(pattern=rf"^{_MONTH_DAY} \S+ .+에 잠들어 .+에 깸$")]
# 오늘 깬 잠과 어제 깬 잠이 어느 깬 날의 수면인지 ("어젯밤 잠은 10월 22일 수면, 그저께 밤 잠은 10월 21일 수면").
# 밤에 잠들지 않은 잠은 잠든 때와 잠 이름을 쓴다 (format_relative_nights)
RelativeNights = Annotated[
    str,
    Field(
        pattern=rf"^오늘\({_MONTH_DAY}\) 기준 (어젯밤 잠은 {_MONTH_DAY} 수면|오늘 깬 잠은 "
        rf"(오늘 아침 6시 이후에 잠든 오늘|어제 저녁 6시 전에 잠든 {_MONTH_DAY}) 수면), "
        rf"(그저께 밤 잠은 {_MONTH_DAY} 수면|어제 깬 잠은 (어제 아침 6시 이후|그저께 저녁 6시 전)에 잠든 어제 수면)"
        r"이다 \(날짜는 깬 날\)$"
    ),
]

_MINUTES_PER_DAY = 24 * 60
_KST = ZoneInfo("Asia/Seoul")


class ToolResult(BaseModel):
    """도구 결과 모델의 기반. 수치와 표시 문자열(display)은 서버가 계산해 채운다 (LLM 산술·시간 환산 오류 방지)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PeriodSpan(ToolResult):
    period: PeriodExpr
    start: dt.date
    end: dt.date
    # 수면은 수면 종료일로 묶이므로 같은 기간이라도 기준이 다르다
    basis: Basis
    # 새 기간 표현의 표시 문구("3주 전 (10월 5일-11일)"). 이름 기간은 비워 두고 직렬화에서 뺀다
    display: str | None = None

    @model_serializer(mode="wrap")
    def _drop_empty_display(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # 이름 기간의 결과·근거에는 display 칸을 넣지 않는다
        data = handler(self)
        if self.display is None:
            data.pop("display", None)
        return data


class Value(ToolResult):
    value: float
    display: str


class DatedValue(Value):
    date: dt.date


class ExerciseActivityStatistic(ToolResult):
    total: Value | None
    avg: Value | None
    days_with_data: int = Field(ge=0)


class ExerciseActivityPoint(ToolResult):
    date: dt.date
    exercise_minutes: Value | None
    exercise_count: Value | None
    exercise_calories_kcal: Value | None
    exercise_distance_m: Value | None
    # 거리 value는 원본 m이고 display만 km 소수 첫째 자리로 표시한다.
    is_in_progress: bool
    missing: dict[str, str]


def _check_missing_days(missing: list[dt.date], unsynced: list[dt.date]) -> None:
    # 일수·통계·결측 분류는 공통 원형 값을 그대로 쓴다.
    # 일수 합, 기록 일수와 통계 값의 유무, 통계·결측 목록의 오늘 날짜는 검사하지 않는다
    if set(missing) & set(unsynced):
        raise ValueError("missing_days와 unsynced_days에 같은 날짜가 있다")


def _check_metric_fields(metric: Metric, measured_during: MeasuredDuring | None, *spans: PeriodSpan) -> None:
    if measured_during != MEASURED_DURING.get(metric):
        raise ValueError(f"{metric}의 measured_during이 지표와 맞지 않는다")
    if any(s.basis != BASES[metric] for s in spans):
        raise ValueError(f"{metric}의 기간 기준은 {BASES[metric]}이어야 한다")


def _check_measured(metric: Metric, measured_min: DatedValue | None, measured_max: DatedValue | None) -> None:
    if metric != "heart_rate" and (measured_min is not None or measured_max is not None):
        raise ValueError("measured_min·measured_max는 heart_rate에서만 채운다")


class InProgress(ToolResult):
    """오늘 진행 중인 값. 하루가 끝나지 않아 완성된 날의 통계에 넣지 않는다."""

    date: dt.date
    # 오늘 기록이 없으면 비어 있다
    value: float | None
    display: str
    # 몇 시까지의 기록인지 나타내는 시각. 답변에서 함께 말한다. 운동은 지표 기준 시각 대신 동기화 기준 시각이다
    as_of: dt.datetime | None


class MetricSummary(ToolResult):
    """한 지표의 기간 요약. 완성된 날 중 기록이 있는 날의 하루 값으로 만든 평균과 가장 낮은·높은 날이다."""

    metric: Metric
    period: PeriodSpan
    unit: Unit
    measured_during: MeasuredDuring | None = None
    avg: Value | None
    # 기간 중 하루 값(심박은 하루 평균)이 가장 낮은·높은 날과 그날 값이다.
    # 공통 원형의 summary.daily_min·daily_max 를 하루 안의 최저·최고로 읽히지 않는 이름으로 LLM 에 전달한다
    lowest_day: DatedValue | None
    highest_day: DatedValue | None
    # 심박만 기간 중 측정된 최저·최고 심박을 따로 준다
    measured_min: DatedValue | None = None
    measured_max: DatedValue | None = None
    # 걸음 수·운동 시간만 기간 합계가 의미 있어서 나머지 지표는 비워 둔다
    total: Value | None = None
    days_with_data: int = Field(ge=0)
    # 통계에 넣은 날 수. 오늘의 걸음 수·심박·운동 시간과 연동 전 날짜는 빠진다
    completed_days: int = Field(ge=0)
    # 기록 없음과 동기화 전인 날을 나눈다. 답변에서 말하는 방법이 다르다
    missing_days: list[dt.date]
    unsynced_days: list[dt.date]
    # 기간 시작보다 늦으면 그 앞 날짜는 연동 전이라 계산에서 빠졌다
    linked_from: dt.date | None
    # 기록 일수 표시 문자열. 연동 시작일이 기간 시작보다 늦으면 연동일을 함께 적는다
    coverage: str
    # 걸음 수·심박·운동 시간이고 기간에 오늘이 있을 때만 채운다
    in_progress: InProgress | None = None
    # 조회된 상태의 해석 한계. 수치·결측 분류·근거 snapshot은 바꾸지 않는다.
    record_note: str | None = None
    # 지표 요약 도구 결과의 심박·산소포화도에만 넣는다. 값을 어떤 측정으로 계산했는지와 자는 동안 잰 값이 있는 결과를
    # 적는다. 기간 비교·전체 요약 결과와 다른 지표에는 칸이 나가지 않는다
    sleep_values_note: str | None = None

    @model_serializer(mode="wrap")
    def _drop_empty_sleep_values_note(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # 칸을 채우지 않은 결과에는 sleep_values_note 를 넣지 않는다
        data = handler(self)
        if data.get("sleep_values_note") is None:
            data.pop("sleep_values_note", None)
        return data

    @model_validator(mode="after")
    def _consistent(self) -> MetricSummary:
        _check_metric_fields(self.metric, self.measured_during, self.period)
        _check_missing_days(self.missing_days, self.unsynced_days)
        _check_measured(self.metric, self.measured_min, self.measured_max)
        if self.sleep_values_note is not None and self.metric not in ("heart_rate", "oxygen_saturation"):
            raise ValueError("sleep_values_note는 heart_rate·oxygen_saturation에서만 채운다")
        if self.total is not None and self.metric not in ("steps", "exercise"):
            raise ValueError("total은 steps·exercise에서만 채운다")
        if self.in_progress is not None and self.metric not in ("steps", "heart_rate", "exercise"):
            raise ValueError("in_progress는 steps·heart_rate·exercise에서만 채운다")
        note = f"기록 범위: {self.coverage}."
        if self.unsynced_days:
            note += (
                " 동기화 전인 날은 기록 유무를 확인할 수 없다. "
                "평균·합계에도 동기화 전 일수를 밝히고 앱 동기화를 안내한다."
            )
        if self.missing_days:
            note += (
                " 기록 없는 날은 평균에 0으로 포함되지 않는다. "
                "실제 활동량 감소나 기록 누락 원인은 이 결과만으로 확인할 수 없다."
            )
        if self.metric == "exercise" and self.in_progress is not None and self.in_progress.value == 0:
            note += " 오늘 운동 시간은 0분으로 기록되어 있다. 운동 기록 없음이 아니다."
        object.__setattr__(self, "record_note", note)
        return self


# 결측 사유 표시. 기록 없음만 기록이 없는 날이고, 동기화 전·연동 전은 기록이 없다는 뜻이 아니다
MissingLabel = Literal["기록 없음", "동기화 전", "연동 전"]


class TrendPoint(ToolResult):
    """하루 값 하나. 값이 있는 날은 값과 표시를, 결측인 날은 결측 사유만 담는다.

    값과 결측 사유, 진행 중 여부는 공통 원형의 하루(days[])에서 가져오고 서로의 관계는 검사하지 않는다.
    """

    date: dt.date
    value: float | None = None
    display: str | None = None
    missing: MissingLabel | None = None
    # 진행 중이 아닌 날은 false 대신 비워 두어 LLM 결과에서 빠지게 한다
    in_progress: Literal[True] | None = None


class DailyTrend(ToolResult):
    """한 지표의 날짜별 하루 값. 기간의 모든 날짜가 순서대로 들어 있다."""

    metric: Metric
    period: PeriodSpan
    unit: Unit
    measured_during: MeasuredDuring | None = None
    points: list[TrendPoint]
    # 심박만 기간 요약과 같은 기간 측정 최저·최고를 담는다.
    # 날짜별 조회를 골라도 측정 최고·최저를 묻는 질문에 답할 수 있다
    measured_min: DatedValue | None = None
    measured_max: DatedValue | None = None
    days_with_data: int = Field(ge=0)
    # 받은 기록이 없는 회원(linked_from 없음)만 채운다.
    # 모든 날이 연동 전이라 기간 요약의 coverage 와 같은 문구를 전달한다
    coverage: str | None = None
    # 원형에 오늘 진행 중 값이 있을 때만 채운다. 기준 시각을 담은 표시가 기간 요약과 같다
    in_progress: InProgress | None = None
    record_note: str | None = None
    # 심박·산소포화도에만 넣는다. 기간 요약 결과의 sleep_values_note 와 같은 문장이다
    sleep_values_note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> DailyTrend:
        _check_metric_fields(self.metric, self.measured_during, self.period)
        _check_measured(self.metric, self.measured_min, self.measured_max)
        if self.sleep_values_note is not None and self.metric not in ("heart_rate", "oxygen_saturation"):
            raise ValueError("sleep_values_note는 heart_rate·oxygen_saturation에서만 채운다")
        note = None
        if any(point.missing is not None for point in self.points):
            note = (
                "값을 확인할 수 없는 날은 0이 아니다. 실제 활동량 감소나 기록 누락 원인은 이 결과만으로 확인할 수 없다."
            )
        if self.metric == "exercise" and self.in_progress is not None and self.in_progress.value == 0:
            note = (note or "") + " 오늘 운동 시간은 0분으로 기록되어 있다. 운동 기록 없음이 아니다."
        object.__setattr__(self, "record_note", note)
        return self


# 수면 기록마다 넣고 값이 없으면 빼는 수면 중 측정 칸
NIGHT_MEASURES = (
    "avg_heart_rate",
    "min_heart_rate",
    "max_heart_rate",
    "spo2_avg",
    "spo2_min",
    "spo2_max",
    "spo2_low_count",
)


class SleepNight(ToolResult):
    """주 수면(그날 가장 긴 수면) 한 번. date는 수면 종료일이고 slept는 잠든 때와 깬 때를 날짜·시각으로 쓴 글이다."""

    date: dt.date
    # format_sleep_window 로 만든 글. 수면 기록마다 값을 넣는 결과(7개 이하)에만 넣고, 기간 요약을 넣는
    # 결과에서는 비워 직렬화에서 뺀다
    slept: SleepWindow | None = None
    bedtime: ClockTime
    wake: ClockTime
    # 실제 수면 시간을 알 수 없는 날은 비어 있다. 세션 길이로 대신하지 않는다
    total_min: int | None = Field(ge=0)
    total: str | None
    deep_min: int | None = Field(ge=0)
    rem_min: int | None = Field(ge=0)
    light_min: int | None = Field(ge=0)
    awake_min: int | None = Field(ge=0)
    # 잠든 동안의 심박과 수면 중 산소포화도의 표시 문자열. 측정이 없는 값은 직렬화에서 뺀다
    avg_heart_rate: str | None = None
    min_heart_rate: str | None = None
    max_heart_rate: str | None = None
    # 90% 미만으로 기록된 산소포화도 수. 0은 기록이 있고 90% 미만이 없었던 수면 기록이다.
    # 산소포화도 평균보다 앞에 둔다 (calculation-rules.md 11.7, 평균은 저하를 가린다)
    spo2_low_count: int | None = Field(default=None, ge=0)
    spo2_avg: str | None = None
    spo2_min: str | None = None
    spo2_max: str | None = None

    @model_serializer(mode="wrap")
    def _drop_missing_measures(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # 측정이 없는 칸과 비워 둔 잠든 시각 구분은 빼고, 다른 칸의 null 은 그대로 둔다
        data = handler(self)
        for name in (*NIGHT_MEASURES, "slept"):
            if data.get(name) is None:
                data.pop(name, None)
        return data


class Duration(ToolResult):
    minutes: int = Field(ge=0)
    display: str


class DatedDuration(Duration):
    date: dt.date


class DatedClock(ToolResult):
    date: dt.date
    time: ClockTime


class ClockRange(ToolResult):
    """시각의 범위와 평균. 자정을 넘는 경우를 이어진 시간으로 본다."""

    earliest: DatedClock
    latest: DatedClock
    average: ClockTime

    @classmethod
    def from_times(cls, items: Sequence[tuple[dt.date, dt.time]]) -> ClockRange:
        """날짜별 시각에서 범위와 평균을 만든다.

        24시간 원에서 가장 넓게 빈 구간을 잘라, 23:50과 00:40이 섞여도 23:50이 가장 이른 시각이 된다.
        """
        if not items:
            raise ValueError("시각이 하나 이상 있어야 한다")
        minutes = sorted({t.hour * 60 + t.minute for _, t in items})
        gaps = [
            (minutes[i] - minutes[i - 1]) if i else (minutes[0] + _MINUTES_PER_DAY - minutes[-1])
            for i in range(len(minutes))
        ]
        cut = minutes[gaps.index(max(gaps))]

        def unwrap(t: dt.time) -> int:
            m = t.hour * 60 + t.minute
            return m if m >= cut else m + _MINUTES_PER_DAY

        ordered = sorted(items, key=lambda it: (unwrap(it[1]), it[0]))
        mean = int(round_half_up(sum(unwrap(t) for _, t in items) / len(items))) % _MINUTES_PER_DAY
        first, last = ordered[0], ordered[-1]
        return cls(
            earliest=DatedClock(date=first[0], time=_hhmm(first[1].hour * 60 + first[1].minute)),
            latest=DatedClock(date=last[0], time=_hhmm(last[1].hour * 60 + last[1].minute)),
            average=_hhmm(mean),
        )


class SleepSummary(ToolResult):
    # 수면 시간 통계와 기록 일수는 total_min이 있는 밤만으로 센다.
    # 실제 수면 시간을 모르는 밤은 missing_days에 들어가도 취침·기상 시각 범위에는 들어간다
    avg_total: Duration | None
    shortest: DatedDuration | None
    longest: DatedDuration | None
    bedtime: ClockRange | None
    wake: ClockRange | None
    days_with_data: int = Field(ge=0)
    completed_days: int = Field(ge=0)
    missing_days: list[dt.date]
    unsynced_days: list[dt.date]
    linked_from: dt.date | None
    coverage: str

    @model_validator(mode="after")
    def _consistent(self) -> SleepSummary:
        _check_missing_days(self.missing_days, self.unsynced_days)
        if (self.bedtime is None) != (self.wake is None):
            raise ValueError("취침·기상 시각 범위는 함께 채운다")
        return self


class SleepMeasureValue(Value):
    """수면마다 잰 값을 모은 기간 값. records 는 그 값이 있는 수면 기록 수다."""

    records: int = Field(ge=1)


class DatedSleepMeasureValue(DatedValue):
    """수면 기록 중 가장 낮은·높은 값과 그 수면 기록의 날짜(수면 종료일). 같은 값이면 이른 날짜다."""

    records: int = Field(ge=1)


class SleepMeasureSummary(ToolResult):
    """수면 기록이 많은 기간의 수면 중 심박·산소포화도 기간 요약. 값이 있는 수면 기록만 쓴다.

    값이 없는 칸은 직렬화에서 뺀다. note 는 값의 뜻을 적은 사실 문장이다. 90% 미만 기록은 결과의 spo2_low 에 있다.
    """

    # 수면마다 기록된 평균 심박의 평균
    avg_heart_rate: SleepMeasureValue | None = None
    min_heart_rate: DatedSleepMeasureValue | None = None
    max_heart_rate: DatedSleepMeasureValue | None = None
    # 수면마다 기록된 산소포화도 평균의 평균
    spo2_avg: SleepMeasureValue | None = None
    spo2_min: DatedSleepMeasureValue | None = None
    note: str

    @model_validator(mode="after")
    def _consistent(self) -> SleepMeasureSummary:
        values = (self.avg_heart_rate, self.min_heart_rate, self.max_heart_rate, self.spo2_avg, self.spo2_min)
        if all(v is None for v in values):
            raise ValueError("기간 요약에는 값이 하나 이상 있어야 한다")
        return self

    @model_serializer(mode="wrap")
    def _drop_missing_values(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {name: value for name, value in handler(self).items() if value is not None}


# 주 수면이 아닌 잠(낮잠 등) 한 번의 한 줄 표기 ("10월 16일 낮 38분"). 잠든 시각 구분과 실제 수면 시간만 쓰고,
# 수면 시간을 모르면 세션 길이로 대신하지 않는다. 백엔드 테스트가 이 표기를 기대한다
NonMainSleepLine = Annotated[
    str,
    Field(pattern=rf"^{_MONTH_DAY} (밤|아침|낮) (\d+시간 \d+분|\d+시간|\d+분|수면 시간 기록 없음)$"),
]
NO_SLEEP_MINUTES = "수면 시간 기록 없음"


# 수면 기록에 보이는 산소포화도 칸
_SPO2_FIELDS = ("spo2_low_count", "spo2_avg", "spo2_min", "spo2_max")


def oxygen_shown(nights: Sequence[SleepNight], summary: SleepMeasureSummary | None, low: str | None) -> bool:
    """수면 기록 결과에 산소포화도 값이 보이는지. 수면 기록마다의 칸, 기간 요약, 90% 미만 기록 문장 중 하나라도 있다."""
    return (
        low is not None
        or any(getattr(n, name) is not None for n in nights for name in _SPO2_FIELDS)
        or (summary is not None and (summary.spo2_avg is not None or summary.spo2_min is not None))
    )


class SleepRecords(ToolResult):
    """기간 안의 날짜(깬 날)별 수면 기록과 요약."""

    metric: Literal["sleep"] = "sleep"
    period: PeriodSpan
    # 기간에 오늘부터 2일 전 사이의 날짜가 있을 때만 채운다. 서버의 오늘(KST)로 계산한 오늘 깬 잠·그저께 밤 잠의 날짜다
    relative_nights: RelativeNights | None = None
    # 90% 미만 기록 수(spo2_low_count)가 있는 수면 기록이 있을 때만 채운다. 90% 미만 기록이 있는 밤 수·횟수·시각을
    # 산소포화도 평균보다 앞에 둔다 (calculation-rules.md 11.7)
    spo2_low: str | None = None
    # 산소포화도 값이 보일 때만 채운다. 워치 측정이 의료기기 측정이 아니라는 사실이다
    spo2_note: str | None = None
    # 수면 기록이 있을 때만 채운다. 수면 시간을 재는 방식(잠든 시각부터 깬 시각 사이에 실제로 잔 시간)의 사실 문장이다
    sleep_time_note: str | None = None
    nights: list[SleepNight]
    summary: SleepSummary
    # 수면 기록마다 수면 중 측정 값을 넣지 않은 결과에만 채운다
    measure_summary: SleepMeasureSummary | None = None
    # include_non_main 으로 전체 수면 세션을 조회한 결과에만 채운다. 기간 안의 주 수면이 아닌 잠을
    # 깬 날·잠든 시각 순으로 한 줄씩 담는다
    non_main_sleeps: list[NonMainSleepLine] | None = None
    non_main_note: str | None = None
    # 기간이 오늘이나 어제로 끝나고 14일 이하일 때만 채운다. 오늘 기준 줄어드는 중(calculation-rules.md 11.3) 판정
    # 값과 어젯밤 대 그 전 7일 비교(11.6) 문장이다 (tools/sleep_coaching_text.py 의 두 줄)
    sleep_vs_baseline: str | None = None
    last_night_vs_week: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SleepRecords:
        if (self.summary.bedtime is None) != (not self.nights):
            raise ValueError("밤 기록이 있을 때만 취침·기상 시각 범위를 채운다")
        each = any(getattr(n, name) is not None for n in self.nights for name in (*NIGHT_MEASURES, "slept"))
        if self.measure_summary is not None and each:
            raise ValueError(
                "measure_summary 는 수면 기록마다 수면 중 측정 값과 잠든 시각 구분을 넣지 않은 결과에만 채운다"
            )
        if self.spo2_low is None and any(n.spo2_low_count is not None for n in self.nights):
            raise ValueError("spo2_low 는 90% 미만 기록 수가 있는 수면 기록이 있으면 채운다")
        if (self.spo2_note is not None) != oxygen_shown(self.nights, self.measure_summary, self.spo2_low):
            raise ValueError("spo2_note 는 산소포화도 값이 있을 때만 채운다")
        if (self.non_main_sleeps is None) != (self.non_main_note is None):
            raise ValueError("non_main_sleeps 와 non_main_note 는 함께 채운다")
        if (self.sleep_vs_baseline is None) != (self.last_night_vs_week is None):
            raise ValueError("sleep_vs_baseline 과 last_night_vs_week 는 함께 채운다")
        if (self.sleep_time_note is not None) != bool(self.nights):
            raise ValueError("sleep_time_note 는 수면 기록이 있을 때만 채운다")
        return self


class SleepAnalysisResult(ToolResult):
    """수면 코칭 분석 결과 (calculation-rules.md 11장). text 는 판정을 맨 앞에 둔 결과 문장이다 (11.7)."""

    metric: Literal["sleep"] = "sleep"
    analysis: SleepAnalysis
    text: str
    # 분석 기간 (깬 날짜). 본인 기준과 비교한 앞선 기간은 결과 문장에 있다
    period: PeriodSpan
    # 14일보다 짧은 기간의 factors 에 따로 덧붙인 최근 28일 운동 조건 비교. 머리와 문장마다 그 기간을 쓴다
    exercise_comparison: str | None = None
    # 주 수면이 아닌 잠 조회(include_non_main)를 함께 받았을 때만 채운다
    non_main_note: str | None = None
    # pattern 이고 기간이 오늘이나 어제로 끝날 때만 두 칸을 함께 채운다. 기본 조회의 수면 시간 문장과 어젯밤 대
    # 그 전 7일 문장과 같다
    sleep_time_note: str | None = None
    last_night_vs_week: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SleepAnalysisResult:
        if (self.sleep_time_note is None) != (self.last_night_vs_week is None):
            raise ValueError("sleep_time_note 와 last_night_vs_week 는 함께 채운다")
        return self


class PeriodAverage(ToolResult):
    """비교하는 한 기간. 평균·일수·기록 일수 문구·오늘 진행 값은 같은 기간의 기간 요약과 같다."""

    period: PeriodSpan
    avg: Value | None
    days_with_data: int = Field(ge=0)
    completed_days: int = Field(ge=0)
    coverage: str
    # 걸음 수·심박·운동 시간이고 기간에 오늘이 있을 때만 채운다
    in_progress: InProgress | None = None


class PeriodComparison(ToolResult):
    """두 기간의 하루 평균 비교. difference는 current - baseline이다.

    차이·변화율은 공개 평균에서 바로 계산해 한 번만 반올림해서, 표시 평균끼리의 차이와 1만큼 다를 수 있다.
    """

    metric: Metric
    unit: Unit
    measured_during: MeasuredDuring | None = None
    current: PeriodAverage
    baseline: PeriodAverage
    # 한쪽 평균이 없으면 비운다
    difference: Value | None
    # 한쪽 평균이 없거나 기준 평균이 0이면 변화율을 정의할 수 없어 비운다
    change_rate_pct: Value | None
    # 두 기간 모두 기록이 3일 이상이고 완성된 날의 절반 이상일 때만 true (calculation-rules.md 4장)
    reliable: bool
    # reliable 이 false 이고 in_progress_note 가 없을 때만 채운다. 비교 결과를 단정하지 않게 하는 안내와 기준이다
    reliable_note: str | None = None
    # 완성된 날 없이 오늘 진행 값만 있는 기간이 있을 때만 채운다. 진행 값과 평균을 따로 말하게 하는 안내다
    in_progress_note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PeriodComparison:
        _check_metric_fields(self.metric, self.measured_during, self.current.period, self.baseline.period)
        both = self.current.avg is not None and self.baseline.avg is not None
        if (self.difference is not None) != both:
            raise ValueError("difference는 두 기간 평균이 모두 있을 때만 채운다")
        if self.change_rate_pct is not None and not both:
            raise ValueError("change_rate_pct는 두 기간 평균이 모두 있을 때만 채운다")
        if self.reliable and not both:
            raise ValueError("평균이 없는 기간이 있으면 reliable은 false다")
        if (self.reliable_note is not None) != (not self.reliable and self.in_progress_note is None):
            raise ValueError("reliable_note는 reliable이 false이고 in_progress_note가 없을 때만 채운다")
        if self.metric not in ("steps", "heart_rate", "exercise") and (
            self.current.in_progress is not None or self.baseline.in_progress is not None
        ):
            raise ValueError("in_progress는 steps·heart_rate·exercise에서만 채운다")
        return self


class HealthOverview(ToolResult):
    """4개 지표의 같은 기간 요약 묶음. 칸 순서는 OVERVIEW_METRICS(홈 화면 카드 순서)다.

    수면은 수면 종료일 기준이라 기간 기준(period.basis)을 지표마다 담는다.
    """

    steps: MetricSummary
    exercise: MetricSummary
    sleep: MetricSummary
    heart_rate: MetricSummary

    @model_validator(mode="after")
    def _metrics_match(self) -> HealthOverview:
        for name in OVERVIEW_METRICS:
            if getattr(self, name).metric != name:
                raise ValueError(f"{name} 칸에 다른 지표 요약이 들어 있다")
        if len({getattr(self, name).period.period for name in OVERVIEW_METRICS}) != 1:
            raise ValueError("네 지표 요약의 기간이 같아야 한다")
        return self


class ExerciseTotal(ToolResult):
    """운동으로 인정된 세션의 합계. type 이 있으면 그 종류만, 없으면 기간의 모든 종류다.

    거리는 거리 기록이 있는 세션(distance_sessions)의 값만 더하고, 하나도 없으면 비운다.
    거리 표시에는 일별 활동 거리와 구분하는 기준("5.57km (운동 기록 기준)")을 붙인다.
    칼로리도 기록이 있는 세션의 값만 더하고, 기록이 없는 세션이 있으면 missing 에 적는다.
    """

    # 기간 전체 합계에만 채우는 합계 문구. 기록 일수와 오늘 기준 시각이 붙어 있다
    display: str | None = None
    # 기준표 이름. 기준표에 없는 코드는 코드 0과 같은 "기타 운동"으로 묶인다
    type: str | None = None
    # 인정 세션 수
    count: int = Field(ge=1)
    duration: str
    distance: str | None = None
    # 인정 세션 중 거리 기록이 있는 세션 수. 저장된 0도 기록이다
    distance_sessions: int = Field(ge=0)
    calories: str | None = None
    missing: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ExerciseTotal:
        if self.distance_sessions > self.count or (self.distance is None) != (self.distance_sessions == 0):
            raise ValueError("거리는 거리 기록이 있는 인정 세션이 있을 때만 채운다")
        return self


class ExerciseSessionItem(ToolResult):
    """운동 한 번. date 는 시작한 날짜(KST), start 는 시작 시각이다."""

    date: dt.date
    start: ClockTime
    type: str
    duration: str
    # 합계와 같이 운동 기록 기준이라는 말을 붙인다. 운동으로 인정되지 않은 세션의 거리도 운동 기록의 값이다
    distance: str | None = None
    calories: str | None = None
    avg_heart_rate: str | None = None
    max_heart_rate: str | None = None
    # 운동으로 인정되지 않은 세션(느린 걷기 등)에만 false 를 넣는다. 나머지는 비워 LLM 결과에서 빠지게 한다
    counts_as_exercise: Literal[False] | None = None


class ExerciseSessions(ToolResult):
    """기간 안에 시작한 운동 세션의 합계와 최근 세션 목록."""

    metric: Literal["exercise"] = "exercise"
    period: PeriodSpan
    # 운동으로 인정된 세션이 없으면 비운다
    total: ExerciseTotal | None = None
    # 전체 합계가 있을 때만 채운다. 수치를 말할 때 같은 문장에 붙일 기록 기준(기록 일수, 오늘 기준 시각)을 알린다
    answer_note: str | None = None
    types: list[ExerciseTotal]
    # 종류가 둘 이상일 때만 채운다. 종류를 합치지 않고 질문한 종류를 고르는 기준이다
    type_note: str | None = None
    # 거리 합계가 있을 때만 채운다. 거리 합계에 넣은 세션(distance_sessions) 안내다
    distance_note: str | None = None
    # 합계나 목록에 칼로리·거리 값이 있을 때만 채운다. 운동 세션의 값이라 하루 전체 값과 다르다는 사실이다
    calorie_distance_note: str | None = None
    # 최근 순. 운동으로 인정되지 않은 세션도 담는다
    sessions: list[ExerciseSessionItem]
    # 목록 개수를 넘어 목록에서 뺀 이전 세션 수
    omitted_sessions: int | None = Field(default=None, ge=1)
    # 기간 안에 시작한 세션 중 운동으로 인정되지 않은 세션 수. 목록 개수와 관계없이 기간 전체로 센다
    not_counted_sessions: int = Field(ge=0)
    not_counted_note: str | None = None
    # 기간 안에 세션이 하나도 없을 때만 채운다
    no_record: str | None = None
    # 운동 기간 요약과 같은 기록 일수 문구. 완성된 날 없이 오늘만 있는 기간은 비운다.
    # 전체 합계가 있으면 합계 문구(total.display)에 들어 있어 비운다
    coverage: str | None = None
    # 기간에 오늘이 있을 때만 채운다. 오늘 기록이 몇 시까지 들어왔는지 알린다.
    # 전체 합계가 있으면 합계 문구(total.display)에 들어 있어 비운다
    in_progress_note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ExerciseSessions:
        _check_metric_fields("exercise", None, self.period)
        if (self.total is None) != (not self.types):
            raise ValueError("종류별 합계가 있을 때만 전체 합계를 채운다")
        if self.total is not None and (
            self.total.type is not None
            or any(t.type is None for t in self.types)
            or sum(t.count for t in self.types) != self.total.count
            or sum(t.distance_sessions for t in self.types) != self.total.distance_sessions
        ):
            raise ValueError("전체 합계는 종류 없이 종류별 세션 수의 합이어야 한다")
        if self.total is not None and (self.total.display is None or any(t.display is not None for t in self.types)):
            raise ValueError("합계 문구는 전체 합계에만 채운다")
        if self.total is not None and (self.coverage is not None or self.in_progress_note is not None):
            raise ValueError("전체 합계가 있으면 기록 일수와 오늘 진행 안내는 합계 문구에만 넣는다")
        if (self.type_note is not None) != (len(self.types) > 1):
            raise ValueError("type_note 는 종류가 둘 이상일 때만 채운다")
        if (self.answer_note is not None) != (self.total is not None):
            raise ValueError("answer_note 는 전체 합계가 있을 때만 채운다")
        if (self.distance_note is not None) != (self.total is not None and self.total.distance is not None):
            raise ValueError("distance_note 는 거리 합계가 있을 때만 채운다")
        shown = [*self.types, *self.sessions]
        if (self.calorie_distance_note is not None) != any(s.calories or s.distance for s in shown):
            raise ValueError("calorie_distance_note 는 칼로리나 거리 값이 있을 때만 채운다")
        counted = 0 if self.total is None else self.total.count
        if len(self.sessions) + (self.omitted_sessions or 0) != counted + self.not_counted_sessions:
            raise ValueError("목록과 뺀 세션 수의 합은 인정·미인정 세션 수의 합이어야 한다")
        if (self.not_counted_note is not None) != (self.not_counted_sessions > 0):
            raise ValueError("not_counted_note 는 미인정 세션이 있을 때만 채운다")
        if (self.no_record is not None) != (not self.sessions):
            raise ValueError("no_record 는 세션이 없을 때만 채운다")
        return self


ScoreGradeLabel = Literal["훌륭", "안정", "관심", "관리"]
# 점수 응답(API 명세 5.3)의 영역 등급 코드와 표시명. 홈 화면과 같은 이름이다.
# TODO: 등급의 점수 구간이 정해지면 이 표에 더한다
SCORE_GRADE_LABELS: dict[int, ScoreGradeLabel] = {1: "훌륭", 2: "안정", 3: "관심", 4: "관리"}
# 활동 점수를 이루는 걸음 점수와 운동 점수의 만점 (API 명세 5.3 의 점수 범위)
STEPS_MAX_POINTS = 60
EXERCISE_MAX_POINTS = 40
# 영역 점수가 없는 사유. 평소 값 없음은 이전 밤 기록이 없어 수면 중 심박을 비교하지 못한 경우다
ScoreAreaMissing = Literal["기록 없음", "평소 값 없음"]


class ScoreAreaResult(ToolResult):
    """영역 하나의 저장 점수와 등급, 점수에 쓴 값. 점수가 없으면 missing 에 사유를 담는다."""

    score: int | None = Field(default=None, ge=0, le=100)
    grade: ScoreGradeLabel | None = None
    # 점수에 쓴 값. 평소 값이 없어 심박 점수가 없어도 수면 중 평균 심박은 담는다
    used: str | None = None
    missing: ScoreAreaMissing | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ScoreAreaResult:
        if (self.score is None) != (self.grade is None):
            raise ValueError("점수와 등급은 함께 채운다")
        if (self.score is None) != (self.missing is not None):
            raise ValueError("missing 은 점수가 없을 때만 채운다")
        return self


class SleepScoreAreaResult(ScoreAreaResult):
    """수면 영역. 점수 기준(rule)과 하루 수면 시간별 점수(targets)를 함께 담는다."""

    # 수면 점수를 매기는 규칙 (score-rules.md 3장). 수면 영역이 있으면 늘 넣는다
    rule: str
    # 이날 수면 시간과 점수, 같은 규칙으로 계산한 목표 수면 시간별 점수. 수면 시간과 점수가 있을 때만 넣는다
    targets: str | None = None


class ScoreTotalResult(ToolResult):
    """오늘의 점수. 점수가 있는 영역의 평균이고 등급이 없다."""

    score: int | None = Field(ge=0, le=100)
    display: str
    # 점수가 있을 때만 채운다. 등급이 없고 기록이 없는 영역은 평균에서 빠졌다는 안내다
    note: str | None = None


class ScoreInProgress(ToolResult):
    """오늘 점수. 하루가 끝나지 않아 동기화될 때마다 다시 계산된다."""

    date: dt.date
    display: str
    # 점수를 계산한 시각(KST). 답변에서 함께 말한다
    as_of: dt.datetime | None


class LifestyleScore(ToolResult):
    """하루의 생활 습관 점수 (API 명세 5.3). 점수·등급은 저장값 그대로이고 다시 계산하지 않는다."""

    period: PeriodSpan
    # 오늘 점수일 때만 채운다. 지표 결과의 진행 중 값과 같은 이름이라 같은 답변 규칙을 따른다
    in_progress: ScoreInProgress | None = None
    total: ScoreTotalResult
    activity: ScoreAreaResult | None = None
    sleep: SleepScoreAreaResult | None = None
    heart: ScoreAreaResult | None = None
    # 점수 행이 없는 날짜의 사유 (연동 전, 동기화 전, 기록 없음). 이때는 영역을 비운다
    missing: MissingLabel | None = None
    # 조건이 맞을 때만 넣는 답변 안내
    notes: list[str] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> LifestyleScore:
        areas = (self.activity, self.sleep, self.heart)
        if (self.missing is None) != all(area is not None for area in areas):
            raise ValueError("점수 행이 없을 때만 missing 을 채우고 영역을 비운다")
        if self.missing is not None and self.total.score is not None:
            raise ValueError("점수 행이 없으면 오늘의 점수도 없다")
        return self


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_minutes(minutes: int) -> str:
    """분을 "6시간 4분" 형태로 바꾼다."""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}시간 {mins}분"
    return f"{hours}시간" if hours else f"{mins}분"


def format_value(unit: Unit, value: float) -> str:
    """지표 값을 단위가 붙은 표시 문자열로 바꾼다. 정수인 값은 소수점을 붙이지 않는다."""
    match unit:
        case "steps":
            return f"{round_half_up(value):,}걸음"
        case "min":
            return format_minutes(int(round_half_up(value)))
        case "bpm" | "%":
            text = f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
            return f"{text}{unit}"
    raise ValueError(f"알 수 없는 단위: {unit}")


def format_distance(meters: Decimal) -> str:
    """거리(m)를 소수 둘째 자리까지의 km 로 바꾼다. 끝자리 0은 뺀다 ("5.57km", "2.6km", "0km")."""
    km = (meters / 1000).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{km:,.2f}".rstrip("0").rstrip(".") + "km"


def format_calories(kcal: Decimal) -> str:
    """칼로리를 정수 kcal 로 바꾼다."""
    return f"{int(kcal.quantize(Decimal(1), ROUND_HALF_UP)):,}kcal"


def format_slept(start_at: dt.datetime) -> str:
    """잠든 시각(KST)으로 잠든 시각 구분을 "10월 20일 밤" 형태로 정한다 (calculation-rules.md 11.1). 낮잠 줄에만 쓴다.

    18:00~23:59 는 그 날짜의 밤, 00:00~05:59 는 하루 전 날짜의 밤, 06:00~09:59 는 아침, 10:00~17:59 는 낮이다.
    """
    local = start_at.astimezone(_KST)
    day = local.date()
    if local.hour < 6:
        day, part = day - dt.timedelta(days=1), "밤"
    elif local.hour < 10:
        part = "아침"
    elif local.hour < 18:
        part = "낮"
    else:
        part = "밤"
    return f"{day.month}월 {day.day}일 {part}"


def format_clock_word(moment: dt.datetime | dt.time) -> str:
    """시각에 시간대 말을 붙여 쓴다 ("새벽 3시 20분", "오전 8시", "저녁 7시 5분", "밤 11시 30분").

    0~5시 새벽, 6~11시 오전, 12~17시 오후, 18~20시 저녁, 21~23시 밤이고 시는 12시간제다. datetime 은 KST 로 바꾼다.
    """
    t = moment.astimezone(_KST).time() if isinstance(moment, dt.datetime) else moment
    word = (
        "새벽" if t.hour < 6 else "오전" if t.hour < 12 else "오후" if t.hour < 18 else "저녁" if t.hour < 21 else "밤"
    )
    hour = t.hour % 12 or 12
    return f"{word} {hour}시" + (f" {t.minute}분" if t.minute else "")


def format_sleep_name(woke_on: dt.date, start_at: dt.datetime | None, today: dt.date) -> str:
    """잠 하나를 깬 날로 부른다 ("10월 18일 수면").

    오늘 깬 잠은 어제 저녁 6시부터 오늘 새벽 6시 전까지 잠들었으면 "어젯밤", 오늘 아침 6시 이후에 잠들었으면
    "오늘 수면"이다. 자정 넘어 잔 밤잠은 어젯밤이고, 낮에 자는 교대 근무자의 잠은 오늘 수면이다. 그보다 앞서
    잠들었으면 깬 날로 부르고, 잠든 시각을 모르면 "어젯밤"이다.
    """
    if woke_on != today:
        return f"{woke_on.month}월 {woke_on.day}일 수면"
    if start_at is None:
        return "어젯밤"
    start = start_at.astimezone(_KST)
    morning = dt.datetime.combine(today, dt.time(6), tzinfo=_KST)
    if start >= morning:
        return "오늘 수면"
    if start >= morning - dt.timedelta(hours=12):
        return "어젯밤"
    return f"{woke_on.month}월 {woke_on.day}일 수면"


def _day_text(day: dt.date, before: dt.date | None) -> str:
    """날짜를 쓰되 앞에 쓴 날짜(before)와 같은 달이면 월을 뺀다."""
    if before is not None and (day.year, day.month) == (before.year, before.month):
        return f"{day.day}일"
    return f"{day.month}월 {day.day}일"


def format_sleep_window(start_at: dt.datetime, end_at: dt.datetime) -> str:
    """잠든 때와 깬 때를 실제 날짜와 시각으로 쓴다 ("10월 18일 밤 11시 20분에 잠들어 19일 새벽 5시 12분에 깸").

    깬 날이 잠든 날과 같으면 깬 쪽 날짜를 뺀다.
    """
    start, end = start_at.astimezone(_KST), end_at.astimezone(_KST)
    head = f"{_day_text(start.date(), None)} {format_clock_word(start)}에 잠들어 "
    if end.date() == start.date():
        return f"{head}{format_clock_word(end)}에 깸"
    return f"{head}{_day_text(end.date(), start.date())} {format_clock_word(end)}에 깸"


def _start_part(woke_on: dt.date, start_at: dt.datetime | None) -> Literal["night", "morning", "earlier"]:
    """깬 날의 잠을 언제 잠들었는지로 나눈다. 깬 날을 오늘로 본 format_sleep_name 과 같은 기준이다.

    전날 저녁 6시부터 깬 날 새벽 6시 전(잠든 시각을 모를 때 포함)은 night, 깬 날 아침 6시 이후는 morning,
    그보다 앞은 earlier 다.
    """
    name = format_sleep_name(woke_on, start_at, woke_on)
    return "night" if name == "어젯밤" else "morning" if name == "오늘 수면" else "earlier"


def format_relative_nights(today: dt.date, today_start: dt.datetime | None, yesterday_start: dt.datetime | None) -> str:
    """오늘 깬 잠과 어제 깬 잠이 어느 깬 날의 수면인지 쓴다. 두 시각은 각 잠의 잠든 시각이다.

    깬 날 전날 저녁 6시부터 깬 날 새벽 6시 전에 잠들었거나 잠든 시각을 모르면 "어젯밤 잠은 10월 22일 수면",
    "그저께 밤 잠은 10월 21일 수면"이다. 그 밖에는 잠든 때와 잠 이름을 쓴다. 오늘 깬 잠은 format_sleep_name 과 같은
    이름("오늘 수면", "10월 22일 수면")이고 어제 깬 잠은 "어제 수면"이다.
    """
    yesterday = today - dt.timedelta(days=1)
    woke_today = {
        "night": f"어젯밤 잠은 {today.month}월 {today.day}일 수면",
        "morning": "오늘 깬 잠은 오늘 아침 6시 이후에 잠든 오늘 수면",
        "earlier": f"오늘 깬 잠은 어제 저녁 6시 전에 잠든 {today.month}월 {today.day}일 수면",
    }[_start_part(today, today_start)]
    woke_yesterday = {
        "night": f"그저께 밤 잠은 {yesterday.month}월 {yesterday.day}일 수면",
        "morning": "어제 깬 잠은 어제 아침 6시 이후에 잠든 어제 수면",
        "earlier": "어제 깬 잠은 그저께 저녁 6시 전에 잠든 어제 수면",
    }[_start_part(yesterday, yesterday_start)]
    return f"오늘({today.month}월 {today.day}일) 기준 {woke_today}, {woke_yesterday}이다 (날짜는 깬 날)"


def format_basis_time(as_of: dt.datetime, today: dt.date) -> str:
    """오늘 진행 안내의 기준 시각을 "12:05" 형태로 바꾼다.

    기준 시각은 동기화가 늦으면 오늘이 아닐 수 있어, KST 날짜가 오늘이 아니면 "10월 20일 23:30"처럼 날짜를 함께 적는다.
    """
    local = as_of.astimezone(_KST)
    clock = f"{local:%H:%M}"
    return clock if local.date() == today else f"{local.month}월 {local.day}일 {clock}"


# 기준 시각이 오늘보다 이를 때 진행 중 문장 뒤에 붙이는 사실 문장. 날짜만 붙은 기준 시각은 답변에서
# "오늘 10월 20일 23시 30분까지 기록 기준"처럼 오늘 시각으로 적힐 수 있다
BASIS_NOT_TODAY_NOTE = "이 시각은 오늘이 아니고, 오늘 기록은 아직 반영되지 않았다"


def is_before_today(as_of: dt.datetime | None, today: dt.date) -> bool:
    """기준 시각의 KST 날짜가 오늘보다 이른지. 기준 시각이 없으면 False 다."""
    return as_of is not None and as_of.astimezone(_KST).date() < today


def add_not_today_note(text: str, as_of: dt.datetime | None, today: dt.date) -> str:
    """기준 시각이 오늘보다 이르면 text 뒤에 BASIS_NOT_TODAY_NOTE 를 붙인다."""
    return f"{text}. {BASIS_NOT_TODAY_NOTE}" if is_before_today(as_of, today) else text


def format_in_progress(metric: Metric, value: float | None, as_of: dt.datetime | None, today: dt.date) -> str:
    """오늘 진행 중 값을 "오늘 진행 중 4,210걸음 (12:05까지 기록 기준)" 형태로 바꾼다.

    기준 시각이 오늘보다 늦으면 "(10월 23일 00:10까지 기록 기준)"처럼 날짜를 함께 적는다. 오늘보다 이르면
    "오늘 진행 중 기록 아직 없음. 10월 20일 23:30까지 기록 기준이다."처럼 기준 시각을 다른 문장에 두고
    BASIS_NOT_TODAY_NOTE 를 뒤에 붙인다.
    """
    text = "오늘 진행 중 " + (format_value(UNITS[metric], value) if value is not None else "기록 아직 없음")
    if as_of is None:
        return text
    basis = f"{format_basis_time(as_of, today)}까지 기록 기준"
    if is_before_today(as_of, today):
        # "오늘"과 지난 날짜의 기준 시각이 한 문장에 있으면 답변에서 "오늘 10월 20일 23시 30분까지"처럼 이어 적힌다
        return f"{text}. {basis}이다. {BASIS_NOT_TODAY_NOTE}"
    return f"{text} ({basis})"
