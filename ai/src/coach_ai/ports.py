from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from coach_ai.tools.results import Unit
from coach_ai.tools.schemas import Metric, PublicMetric

DataErrorCode = Literal["user_not_linked", "data_not_ready", "data_unavailable", "forbidden"]


class HealthDataError(Exception):
    """포트가 발생시키는 조회 실패. code·message·retryable과 metrics가 error 이벤트에 들어간다.

    예외 문구(str)는 원인 기록용이고 이벤트에는 넣지 않는다. message는 사용자에게 보여도 되는 정해진 문장이다.
    """

    code: ClassVar[DataErrorCode] = "data_unavailable"
    message: ClassVar[str] = "건강 기록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요."
    retryable: ClassVar[bool] = True

    def __init__(self, *args: object, metrics: Iterable[Metric] = ()) -> None:
        super().__init__(*args)
        self.metrics: tuple[Metric, ...] = tuple(metrics)


class HealthDataNotLinkedError(HealthDataError):
    """연동 전이다 (CANDiY 계정 미등록, 첫 적재 전). 기록 없음과 다르다."""

    code = "user_not_linked"
    message = "건강 기록이 아직 연동되지 않았어요. 연동을 마치고 잠시 후 다시 시도해 주세요."


class HealthDataNotReadyError(HealthDataError):
    """집계 준비 전이다 (첫 연동 뒤 적재·집계가 끝나기 전 등). 기록 없음과 다르다."""

    code = "data_not_ready"
    message = "건강 기록을 아직 준비하고 있어요. 잠시 후 다시 시도해 주세요."


class HealthDataUnavailableError(HealthDataError):
    """조회 실패·시간 초과. 저장소 장애처럼 다시 시도하면 될 수 있는 실패다."""


class HealthDataForbiddenError(HealthDataError):
    """조회 권한 거부. 다시 시도해도 같다."""

    code = "forbidden"
    message = "건강 기록 조회 권한을 확인해 주세요."
    retryable = False


class HealthDataLimitError(HealthDataError):
    """조회 범위의 기록이 연동 쪽 한도를 넘었다. 요청을 끝내지 않고 도구 결과(limit_exceeded)가 된다.

    code 는 연동 쪽 오류 코드 그대로이고 error 이벤트의 code 가 아니다. source 는 조회 종류(sleep_sessions 등),
    can_narrow 는 기간을 줄여 다시 조회하면 풀릴 수 있는지다. limit·limit_unit 은 한도를 알 때만 넣는다.
    """

    retryable = False

    def __init__(
        self, code: str, *, source: str, can_narrow: bool, limit: int | None = None, limit_unit: str | None = None
    ) -> None:
        if limit is not None and (isinstance(limit, bool) or limit < 1):
            raise ValueError("limit 은 1 이상의 정수다")
        if limit_unit is not None and limit is None:
            raise ValueError("limit_unit 은 limit 과 함께 넣는다")
        super().__init__(code)
        self.code = code
        self.source = source
        self.can_narrow = can_narrow
        self.limit = limit
        self.limit_unit = limit_unit


class HealthDataTargetNotFoundError(HealthDataError):
    """조회할 기록이 없다. 요청을 끝내지 않고 도구 결과(target_not_found)가 된다.

    없는 기록과 다른 회원의 기록을 구분하지 않는다. code 는 연동 쪽 오류 코드 그대로이고 error 이벤트의 code 가
    아니다. source 는 조회 종류(exercise_samples 등)다.
    """

    retryable = False

    def __init__(self, code: str, *, source: str) -> None:
        super().__init__(code)
        self.code = code
        self.source = source


class ResultContractError(ValueError):
    """포트 결과가 계약과 다르다. 연동 구현의 결함이라 HealthDataError가 아니고 internal로 끝난다.

    공통 원형의 구조만 검사한다. 명세 필드 누락, 타입 오류, 시간대 없는 시각, 원형 안의 내부 user_id,
    요청과 다른 지표·기간이 해당한다. 결측 구분과 요약 일수는 연동 함수가 정한 값이라 다시 계산해 비교하지 않는다.
    다시 시도해도 같아 조회 실패와 구분한다.
    """


# 지표 조회 결과 (API 명세 2.2 공통 원형)

MissingReason = Literal["NO_RECORD", "NULL_VALUE", "ZERO_STEPS", "UNSYNCED", "BEFORE_LINK"]
SyncStatus = Literal["success", "fail"]


class _Prototype(BaseModel):
    # 연동 쪽 원형에 필드가 늘어도 읽기가 실패하지 않게 모르는 필드는 무시한다
    model_config = ConfigDict(extra="ignore", frozen=True)


class ResultPeriod(_Prototype):
    start: dt.date
    end: dt.date


class ResultValue(_Prototype):
    """날짜가 붙은 값 하나. 최소·최대가 나온 날을 가리킨다. 동률이면 가장 이른 날이다."""

    date: dt.date
    value: float


class ResultSummary(_Prototype):
    # 통계 대상 날짜가 0이면 avg·daily_min·daily_max·total은 None이다
    avg: float | None
    daily_min: ResultValue | None
    daily_max: ResultValue | None
    # 걸음 수·운동만 완료된 유효 날짜의 합계를 채운다
    total: float | None
    days_with_data: int
    days_used_for_summary: int
    # 통계 대상 날짜 수. 걸음·심박·운동은 오늘을 빼고, linked_from 이전 날짜는 넣지 않는다
    completed_days: int
    # 결측 중 synced_until의 KST 날짜 이전(기록 없음)과 같거나 이후(동기화 전). 오늘 진행 중인 날도 들어갈 수 있다
    missing_days: tuple[dt.date, ...]
    unsynced_days: tuple[dt.date, ...]
    # 회원의 모든 지표 중 가장 이른 기록 날짜. 기록이 하나도 없으면 None이다
    linked_from: dt.date | None


class ResultDay(_Prototype):
    """요청 기간의 하루. reason이 있으면 missing이다. 0보는 value=0, reason=ZERO_STEPS로 둔다."""

    date: dt.date
    value: float | None
    missing: bool
    reason: MissingReason | None
    # 하루가 끝나지 않은 날. 걸음·심박·운동의 오늘만 참이다
    is_in_progress: bool
    # 그날 심박이 측정된 시간대 수와 충분히 착용한 날인지 (calculation-rules.md 9.1). 모든 지표에 같은 값이다.
    # null 은 판단을 보류한 날이고 그대로 둔다. 두 칸이 없는 원형(착용 값이 생기기 전 근거)도 null 로 읽는다
    wear_hours: int | None = None
    is_well_worn: bool | None = None


class ResultInProgress(_Prototype):
    """걸음·심박·운동의 오늘 값. 기간 통계에 넣지 않는다."""

    date: dt.date
    value: float | None
    missing: bool
    # 지표 기준 시각 (data_status.last_data_at과 같다)
    as_of: AwareDatetime | None


class ResultDataStatus(_Prototype):
    last_data_at: AwareDatetime | None
    # 회원의 모든 지표 last_data_at 중 가장 늦은 시각. 결측 날짜를 기록 없음과 동기화 전으로 나누는 기준이다
    synced_until: AwareDatetime | None
    last_sync_status: SyncStatus | None
    last_success_at: AwareDatetime | None
    checked_at: AwareDatetime


class ResultNight(_Prototype):
    """주 수면 한 번. date는 수면 종료일(KST)이다.

    심박은 잠든 동안 측정한 값(bpm)이고 산소포화도는 수면 중 측정한 값(%)이다. 측정이 없으면 None이다.
    """

    date: dt.date
    start_at: AwareDatetime
    end_at: AwareDatetime
    # 실제 수면 분을 모르면 None이다. 세션 시각은 그대로 둔다
    sleep_minutes: int | None
    deep_minutes: int | None
    rem_minutes: int | None
    light_minutes: int | None
    awake_minutes: int | None
    avg_heart_rate: float | None
    min_heart_rate: float | None
    max_heart_rate: float | None
    spo2_avg: float | None
    spo2_min: float | None
    spo2_max: float | None
    # 90% 미만으로 측정된 산소포화도 수. 낮은 상태가 이어진 시간이 아니다
    spo2_low_count: int | None
    # 90% 미만으로 기록된 시각. 연동 쪽 순서(이른 순)와 같은 시각 반복, 오프셋을 그대로 둔다.
    # 키가 없는 원형(메모리 어댑터, 칸을 더하기 전의 원형)은 None 이다
    spo2_low_times: tuple[AwareDatetime, ...] | None = None

    @model_validator(mode="after")
    def _low_times_match_count(self) -> ResultNight:
        # "외 N회"를 spo2_low_count − 보여 준 개수로 계산해 두 값의 수가 같아야 한다 (연동 쪽 DB 제약과 같다)
        if self.spo2_low_times is not None and len(self.spo2_low_times) != self.spo2_low_count:
            raise ValueError("spo2_low_times 수가 spo2_low_count 와 다르다")
        return self


class ResultDailyRange(_Prototype):
    date: dt.date
    min: float | None
    max: float | None


class ResultSession(_Prototype):
    """운동 세션 하나. date는 시작 시각의 KST 날짜이고, 기준표에 없는 코드의 type_name은 "기타 운동"이다.

    거리·칼로리·심박은 기록이 없으면 None이다. 저장된 0은 기록이다.
    """

    date: dt.date
    start_at: AwareDatetime
    duration_minutes: int
    type_name: str
    counts_as_exercise: bool
    distance_m: float | None
    calories_kcal: float | None
    avg_heart_rate: float | None
    max_heart_rate: float | None


_SESSIONS = TypeAdapter(tuple[ResultSession, ...])


class ResultExerciseActivityDay(_Prototype):
    date: dt.date
    exercise_minutes: int | None = Field(ge=0, strict=True)
    exercise_count: int | None = Field(ge=0, strict=True)
    exercise_calories_kcal: Decimal | None = Field(ge=0, allow_inf_nan=False)
    exercise_distance_m: Decimal | None = Field(ge=0, allow_inf_nan=False)
    computed_at: AwareDatetime | None
    is_in_progress: bool
    reason: Literal["BEFORE_LINK", "UNSYNCED", "NO_RECORD", "NULL_VALUE"] | None
    calories_reason: Literal["BEFORE_LINK", "UNSYNCED", "NO_RECORD", "NULL_VALUE"] | None
    distance_reason: Literal["BEFORE_LINK", "UNSYNCED", "NO_RECORD", "NULL_VALUE"] | None


class ResultExerciseStatistic(_Prototype):
    total: Decimal | None = Field(ge=0, allow_inf_nan=False)
    avg: Decimal | None = Field(ge=0, allow_inf_nan=False)
    days_with_data: int = Field(ge=0)


class ResultExerciseActivitySummary(_Prototype):
    exercise_minutes: ResultExerciseStatistic
    exercise_count: ResultExerciseStatistic
    exercise_calories_kcal: ResultExerciseStatistic
    exercise_distance_m: ResultExerciseStatistic


class ResultExerciseSourceStatus(_Prototype):
    ready: bool | None
    last_sync_status: SyncStatus | None
    last_data_at: AwareDatetime | None
    last_success_at: AwareDatetime | None


class ResultExerciseActivity(_Prototype):
    days: tuple[ResultExerciseActivityDay, ...]
    summary: ResultExerciseActivitySummary
    source_statuses: dict[Literal["exercise_sessions", "calories_burned", "distances"], ResultExerciseSourceStatus]


class ResultDetail(_Prototype):
    # 심박·산소포화도만 통계 대상 날짜의 실제 측정 최저·최고를 채운다
    measured_min: ResultValue | None
    measured_max: ResultValue | None
    # 수면만 주 수면 상세를 날짜 순으로 채운다
    nights: tuple[ResultNight, ...]
    # 심박·산소포화도만 요청한 모든 날짜의 최저·최고를 채운다
    daily_ranges: tuple[ResultDailyRange, ...]
    # 운동만 기간 안에 시작한 세션을 시작 시각 순으로 채운다. 항목 형식은 세션을 읽는 도구만 검사하고
    # (MetricResult.exercise_sessions), 다른 도구는 항목 형식이 달라도 결과가 같다
    sessions: tuple[dict[str, Any], ...]


class _MetricPrototype(_Prototype):
    metric: PublicMetric
    period: ResultPeriod
    unit: Unit
    summary: ResultSummary
    days: tuple[ResultDay, ...]
    in_progress: ResultInProgress | None
    data_status: ResultDataStatus
    detail: ResultDetail


@dataclass(frozen=True, slots=True, eq=False)
class MetricResult:
    """포트가 반환하는 지표 조회 결과. 결과 소유자와 API 명세 2.2 공통 원형(snake_case)을 함께 담는다.

    snapshot은 받은 공통 원형의 사본이고 근거에 그대로 저장한다. 소유자 user_id는 snapshot에 넣지 않는다.
    나머지 필드는 snapshot을 명세 2.2의 이름 그대로 읽은 값이다.
    원형이 명세 필드를 갖추지 못했거나 내부 user_id가 들어 있으면 ResultContractError를 발생시킨다.
    """

    user_id: str
    snapshot: Mapping[str, Any] = field(repr=False)
    metric: PublicMetric = field(init=False)
    period: ResultPeriod = field(init=False)
    unit: Unit = field(init=False, repr=False)
    summary: ResultSummary = field(init=False, repr=False)
    days: tuple[ResultDay, ...] = field(init=False, repr=False)
    in_progress: ResultInProgress | None = field(init=False, repr=False)
    data_status: ResultDataStatus = field(init=False, repr=False)
    detail: ResultDetail = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _read_snapshot(self, _MetricPrototype, "공통 원형")

    def exercise_sessions(self) -> tuple[ResultSession, ...]:
        """detail.sessions를 운동 세션으로 읽는다.

        항목이 명세 필드를 갖추지 못했거나 세션 날짜가 결과 기간 밖이면 ResultContractError를 발생시킨다.
        """
        try:
            sessions = _SESSIONS.validate_python(self.detail.sessions)
        except ValidationError as e:
            raise ResultContractError("운동 세션이 명세 필드를 갖추지 못했다") from e
        if any(not self.period.start <= s.date <= self.period.end for s in sessions):
            raise ResultContractError("운동 세션 날짜가 결과 기간 밖이다")
        return sessions

    def exercise_activity(self) -> ResultExerciseActivity | None:
        """추가 활동값은 이를 사용하는 운동 요약·추이에서만 검증한다."""
        raw = self.snapshot["detail"].get("exercise_activity")
        if raw is None:
            return None
        try:
            activity = ResultExerciseActivity.model_validate(raw)
        except ValidationError as e:
            raise ResultContractError("운동 활동값이 명세 필드를 갖추지 못했다") from e
        if self.metric != "exercises" or [d.date for d in activity.days] != [d.date for d in self.days]:
            raise ResultContractError("운동 활동값의 지표·날짜가 공통 원형과 다르다")
        if set(activity.source_statuses) != {"exercise_sessions", "calories_burned", "distances"}:
            raise ResultContractError("운동 활동값의 원천 상태가 누락됐다")
        return activity


# 전체 수면 세션 조회 결과 (백엔드 app.health.schemas.SleepSessionsResult)


class ResultSleepSession(_Prototype):
    """저장된 수면 세션 하나. 주 수면과 주 수면이 아닌 잠(낮잠 등)이 함께 온다. date는 수면 종료일(KST)이다."""

    date: dt.date
    start_at: AwareDatetime
    end_at: AwareDatetime
    is_main: bool
    # 실제 수면 분을 모르면 None 이다
    sleep_minutes: int | None


class _SleepSessionsPrototype(_Prototype):
    period: ResultPeriod
    sessions: tuple[ResultSleepSession, ...]


@dataclass(frozen=True, slots=True, eq=False)
class SleepSessionsResult:
    """포트가 반환하는 기간의 모든 저장 수면 세션. 결과 소유자와 snapshot 을 함께 담는다.

    snapshot 은 백엔드 결과 모델을 model_dump(mode="json", exclude={"user_id"}) 한 값이고 kind 근거에 그대로 쓴다.
    나머지 필드는 snapshot 을 백엔드와 같은 이름으로 읽은 값이고, 결과 문장에 쓰는 칸만 둔다.
    그 칸을 갖추지 못했거나 내부 user_id 가 들어 있으면 ResultContractError 를 발생시킨다.
    """

    user_id: str
    snapshot: Mapping[str, Any] = field(repr=False)
    period: ResultPeriod = field(init=False)
    sessions: tuple[ResultSleepSession, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _read_snapshot(self, _SleepSessionsPrototype, "수면 세션 결과")


# 하루 점수 조회 결과 (API 명세 5.3 응답 data)

ScoreArea = Literal["activity", "sleep", "heart"]
# 영역 등급 코드. 표시명은 tools/results.py 의 SCORE_GRADE_LABELS 다
ScoreGrade = Literal[1, 2, 3, 4]
# 점수 행이 없는 날짜의 사유
ScoreMissingReason = Literal["BEFORE_LINK", "UNSYNCED", "NO_RECORD"]


class ScoreTotal(_Prototype):
    # 오늘의 점수. 점수가 있는 영역의 평균이고 등급이 없다
    score: int | None
    missing_areas: tuple[ScoreArea, ...]


class ScoreSteps(_Prototype):
    score: int | None
    value: int | None


class ScoreExercise(_Prototype):
    score: int | None
    minutes: int | None


class ScoreActivity(_Prototype):
    score: int | None
    grade: ScoreGrade | None
    steps: ScoreSteps
    exercise: ScoreExercise


class ScoreSleep(_Prototype):
    score: int | None
    grade: ScoreGrade | None
    sleep_minutes: int | None


class ScoreHeart(_Prototype):
    score: int | None
    grade: ScoreGrade | None
    # 홈 화면의 안정 시 심박수와 같은 값이다. 평소 값이 없어 심박 점수가 없어도 채워질 수 있다
    sleep_hr_avg: float | None
    baseline_hr_avg: float | None
    baseline_nights: int | None
    check_wear: bool | None


class ScoreAreas(_Prototype):
    activity: ScoreActivity
    sleep: ScoreSleep
    heart: ScoreHeart


class _ScorePrototype(_Prototype):
    date: dt.date
    is_in_progress: bool
    reason: ScoreMissingReason | None
    total: ScoreTotal
    areas: ScoreAreas
    computed_at: AwareDatetime | None


@dataclass(frozen=True, slots=True, eq=False)
class LifestyleScoreResult:
    """포트가 반환하는 하루 점수. 결과 소유자와 API 명세 5.3 응답의 data(snake_case)를 함께 담는다.

    snapshot은 받은 응답의 사본이고, 나머지 필드는 snapshot을 명세 5.3의 이름 그대로 읽은 값이다.
    점수·등급은 연동 쪽 저장값이라 다시 계산해 비교하지 않는다.
    응답이 명세 필드를 갖추지 못했거나 내부 user_id가 들어 있으면 ResultContractError를 발생시킨다.
    """

    user_id: str
    snapshot: Mapping[str, Any] = field(repr=False)
    date: dt.date = field(init=False)
    is_in_progress: bool = field(init=False, repr=False)
    reason: ScoreMissingReason | None = field(init=False, repr=False)
    total: ScoreTotal = field(init=False, repr=False)
    areas: ScoreAreas = field(init=False, repr=False)
    computed_at: dt.datetime | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _read_snapshot(self, _ScorePrototype, "점수 응답")


# 오늘의 한마디 조회 결과 (API 명세 5.5 응답 data)

DailyMessageStatus = Literal["READY", "PARTIAL", "EMPTY", "WAITING"]
DailyMessageArea = Literal["activity", "sleep"]
# 한마디가 읽는 지표의 공개 코드. 명세 5.5 의 dataAsOf 키이자 notices 의 지표다
DailyMessageMetric = Literal["steps", "exercises", "sleeps"]
DailyMessageNoticeCode = Literal["LOOKUP_FAILED", "DATA_NOT_READY", "SYNC_FAILED"]


class DailyMessageDataAsOf(_Prototype):
    """지표마다 정상 조회한 기록의 기준 시각(lastDataAt). 조회 실패·준비 전 지표는 None 이다."""

    steps: AwareDatetime | None
    exercises: AwareDatetime | None
    sleeps: AwareDatetime | None


class DailyMessageNotice(_Prototype):
    metric: DailyMessageMetric
    code: DailyMessageNoticeCode
    # 홈 화면 카드 아래에 보이는 안내 문장
    message: str


class _DailyMessagePrototype(_Prototype):
    date: dt.date
    status: DailyMessageStatus
    # 공개 계약에서 문자열이라 새 문구 코드가 생겨도 읽기가 실패하지 않는다
    code: str
    area: DailyMessageArea | None
    title: str
    description: str
    data_as_of: DailyMessageDataAsOf
    score_computed_at: AwareDatetime | None
    notices: tuple[DailyMessageNotice, ...]
    # 문구에 쓴 지표. 공개 응답에는 없고 포트 계약에만 있다
    used_metrics: tuple[DailyMessageMetric, ...]


@dataclass(frozen=True, slots=True, eq=False)
class DailyMessageResult:
    """포트가 반환하는 오늘의 한마디. 결과 소유자와 snapshot 을 함께 담는다.

    snapshot 은 API 명세 5.5 응답의 data(snake_case)에 used_metrics 를 더한 사본이고, 나머지 필드는 snapshot 을 같은
    이름으로 읽은 값이다. 문구는 연동 쪽 규칙이 오늘 기록으로 고른 값이라 다시 고르지 않는다.
    응답이 명세 필드를 갖추지 못했거나 내부 user_id 가 들어 있으면 ResultContractError 를 발생시킨다.
    """

    user_id: str
    snapshot: Mapping[str, Any] = field(repr=False)
    date: dt.date = field(init=False)
    status: DailyMessageStatus = field(init=False, repr=False)
    code: str = field(init=False, repr=False)
    area: DailyMessageArea | None = field(init=False, repr=False)
    title: str = field(init=False, repr=False)
    description: str = field(init=False, repr=False)
    data_as_of: DailyMessageDataAsOf = field(init=False, repr=False)
    score_computed_at: dt.datetime | None = field(init=False, repr=False)
    notices: tuple[DailyMessageNotice, ...] = field(init=False, repr=False)
    used_metrics: tuple[DailyMessageMetric, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _read_snapshot(self, _DailyMessagePrototype, "한마디 응답")


def _read_snapshot(
    result: MetricResult | SleepSessionsResult | LifestyleScoreResult | DailyMessageResult,
    prototype: type[_Prototype],
    label: str,
) -> None:
    """snapshot의 사본을 원형으로 읽어 결과의 필드에 넣는다. 계약과 다르면 ResultContractError를 발생시킨다."""
    snapshot = copy.deepcopy(dict(result.snapshot))
    if "user_id" in snapshot:
        raise ResultContractError(f"{label}에 내부 user_id가 들어 있다")
    try:
        parsed = prototype.model_validate(snapshot)
    except ValidationError as e:
        raise ResultContractError(f"{label}이 명세 필드를 갖추지 못했다") from e
    object.__setattr__(result, "snapshot", snapshot)
    for name in prototype.model_fields:
        object.__setattr__(result, name, getattr(parsed, name))


@runtime_checkable
class HealthDataPort(Protocol):
    """에이전트가 건강데이터를 읽는 경계. 구현은 집계 테이블을 가진 쪽이 한다.

    - 구현도 async def로 둔다 (runtime_checkable 검사는 비동기 여부를 확인하지 않음).
    - metric은 도구 지표 이름이다. 공개 지표 코드(heart_rates 등)로 바꾸는 일은 구현이 한다.
    - 날짜 구간은 KST 양 끝을 포함하고, end는 KST 오늘을 넘지 않는다.
    - user_id는 users.id(UUID 문자열)이다. 결과의 user_id에는 조회한 기록의 소유자(end_user_id)를 채운다.
      비어 있거나 요청한 회원과 다르면 게이트웨이가 결과를 버리고 forbidden으로 끝낸다.
    - 한 요청 안에서도 동시에 불릴 수 있다.
    - 도구 호출 하나의 조회는 1초 안에 끝나야 한다. 넘으면 data_unavailable로 끝난다.
    - 기록이 없는 것은 실패가 아니다. 결측 날짜를 담은 정상 결과를 반환한다.

    조회에 실패하면 HealthDataError 하위 예외만 발생시킨다. 괄호 안은 error 이벤트의 code다.
    - HealthDataNotLinkedError: 미연동, 계정 미등록·첫 적재 전 (user_not_linked)
    - HealthDataNotReadyError: 집계 준비 전 (data_not_ready)
    - HealthDataUnavailableError: 조회 실패·시간 초과 (data_unavailable)
    - HealthDataForbiddenError: 권한 거부 (forbidden)
    실패한 지표를 알면 metrics에 넣고, 모르면 비워 둔다. 그 밖의 예외는 internal로 끝난다.
    결과가 계약과 다르면 ResultContractError가 발생하고 internal로 끝난다.
    """

    async def get_metric_result(self, user_id: str, metric: Metric, start: dt.date, end: dt.date) -> MetricResult:
        """한 지표의 기간 조회 결과. 상세·요약·상태는 같은 읽기 시점의 값이다."""
        ...

    async def get_sleep_sessions(self, user_id: str, start: dt.date, end: dt.date) -> SleepSessionsResult:
        """기간(KST 수면 종료일, 양 끝 포함)의 모든 저장 수면 세션. 주 수면이 아닌 잠까지 담는다.

        기간은 31일까지이고 처리 함수가 조회 전에 확인한다. 기록이 없으면 sessions 가 빈 정상 결과다.
        세션 수가 연동 쪽 한도를 넘으면 HealthDataLimitError 다. 나머지 실패는 get_metric_result 와 같은
        HealthDataError 하위 예외이고 metrics 에 sleep 을 넣는다. 연동 쪽 입력 검증에 걸리면 조회 실패다.
        """
        ...

    async def get_lifestyle_score(self, user_id: str, day: dt.date) -> LifestyleScoreResult:
        """하루의 저장 생활 습관 점수(API 명세 5.3). 다시 계산하지 않는다.

        day는 KST 오늘을 넘지 않는다. 점수 행이 없는 날짜는 실패가 아니라 reason이 있는 정상 결과다.
        실패는 get_metric_result와 같은 HealthDataError 하위 예외다. 점수 조회 실패에는 지표가 없어 metrics를 비운다.
        """
        ...

    async def get_daily_message(self, user_id: str, day: dt.date, *, request_id: str) -> DailyMessageResult:
        """홈 화면 오늘의 한마디(API 명세 5.5). 저장하지 않고 조회할 때마다 그날 기록에 규칙을 적용해 고른다.

        day는 질문을 받은 시각의 KST 날짜이고 KST 오늘을 넘지 않는다. 지난 날짜의 한마디는 계약에 없다.
        request_id는 CoachContext.request_id이고 연동 쪽 조회 로그에만 쓴다.
        일부 지표만 실패하고 고를 문구가 있으면 notices가 있는 정상 결과다. 고를 문구가 없고 실패한 지표가 있으면
        get_metric_result와 같은 HealthDataError 하위 예외이고 metrics에 그 지표를 넣는다.
        """
        ...
