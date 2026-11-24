"""코칭 에이전트의 건강데이터 포트(coach_ai.ports.HealthDataPort)를 건강 조회 함수에 연결한다."""

from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.health import errors as health_errors
from app.health.daily_message import DailyMessageResult
from app.health.daily_message_service import MESSAGE_METRICS, get_daily_message
from app.health.rest_schemas import daily_message_response, lifestyle_score_response
from app.health.schemas import (
    KST,
    SLEEP_SESSION_LIMIT,
    HealthMetric,
    HealthMetricResult,
    LifestyleScoreRecord,
    MissingLifestyleScore,
    SleepSessionsResult,
)
from app.health.service import (
    get_lifestyle_score,
    get_metric_result,
    get_sleep_sessions,
)
from coach_ai import ports
from coach_ai.tools.schemas import PUBLIC_METRICS, Metric


class MetricLookup(Protocol):
    """app.health.service.get_metric_result 와 같은 호출 형태다."""

    async def __call__(
        self,
        user_id: UUID,
        metric: HealthMetric,
        start: date,
        end: date,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> HealthMetricResult: ...


class MessageLookup(Protocol):
    """한마디 서비스의 독립 읽기에 앱 엔진과 요청 식별자를 전달한다."""

    async def __call__(
        self, user_id: UUID, *, today: date, request_id: str, engine: AsyncEngine
    ) -> DailyMessageResult: ...


class ScoreLookup(Protocol):
    """app.health.service.get_lifestyle_score 와 같은 호출 형태다."""

    async def __call__(
        self,
        user_id: UUID,
        score_date: date,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> LifestyleScoreRecord | MissingLifestyleScore: ...


class SleepSessionsLookup(Protocol):
    """app.health.service.get_sleep_sessions 와 같은 호출 형태다."""

    async def __call__(
        self,
        user_id: UUID,
        start: date,
        end: date,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> SleepSessionsResult: ...


# 준비 전과 조회 실패는 코칭 오류 code 로 구분되고, 공개 응답에서 다시 reason 이 된다
# (data_not_ready → DATA_NOT_READY, data_unavailable → LOOKUP_FAILED)
_PORT_ERRORS: dict[type[health_errors.HealthDataError], type[ports.HealthDataError]] = {
    health_errors.HealthDataNotReadyError: ports.HealthDataNotReadyError,
    health_errors.HealthDataLookupError: ports.HealthDataUnavailableError,
    health_errors.HealthDataForbiddenError: ports.HealthDataForbiddenError,
    health_errors.HealthDataNotLinkedError: ports.HealthDataNotLinkedError,
}
# 도구 지표 이름과 공개 지표 코드의 변환은 coach_ai 의 PUBLIC_METRICS 한 곳에서 관리한다
_TOOL_METRICS: dict[str, Metric] = {
    public: metric for metric, public in PUBLIC_METRICS.items()
}


class HealthQueryPort:
    """건강 조회 함수의 결과와 오류를 코칭 포트 형식으로 바꾼다. 결과 값은 다시 계산하지 않는다.

    호출마다 조회 함수가 세션 팩토리에서 새 세션과 읽기 트랜잭션을 열고 닫으므로
    동시에 호출해도 세션을 공유하지 않는다. 결과 소유자 확인은 코칭 게이트웨이가 한다.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        lookup: MetricLookup = get_metric_result,
        score_lookup: ScoreLookup = get_lifestyle_score,
        *,
        engine: AsyncEngine | None = None,
        message_lookup: MessageLookup = get_daily_message,
        sleep_sessions_lookup: SleepSessionsLookup = get_sleep_sessions,
    ) -> None:
        self._session_factory = session_factory
        self._lookup = lookup
        self._score_lookup = score_lookup
        self._engine = engine
        self._message_lookup = message_lookup
        self._sleep_sessions_lookup = sleep_sessions_lookup

    @property
    def engine(self) -> AsyncEngine | None:
        """앱에서 명시적으로 주입한 한마디 읽기 엔진. 접근 시 DB 연결을 열지 않는다."""
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """조회 함수에 넘기는 세션 팩토리. 생성자에 받은 객체를 그대로 반환한다."""
        return self._session_factory

    async def get_metric_result(
        self, user_id: str, metric: Metric, start: date, end: date
    ) -> ports.MetricResult:
        public = _public_metric(metric)
        owner = _owner_id(user_id)
        try:
            result = await self._lookup(
                owner, public, start, end, session_factory=self._session_factory
            )
        except health_errors.HealthDataError as exc:
            converted = _PORT_ERRORS.get(type(exc))
            if converted is None:
                raise
            metrics = exc.metrics
        else:
            return ports.MetricResult(
                str(result.user_id), result.to_public().model_dump(mode="json")
            )
        # except 블록 밖에서 발생시켜 원래 예외의 문구·cause 를 새 예외에 연결하지 않는다.
        # 조회 실패의 cause 에는 SQL 과 측정값이 들어 있을 수 있다
        raise converted(metrics=_tool_metrics(metrics)) from None

    async def get_daily_message(
        self, user_id: str, day: date, *, request_id: str
    ) -> ports.DailyMessageResult:
        owner = _owner_id(user_id)
        if self._engine is None:
            raise RuntimeError("한마디 조회에는 앱 엔진이 필요하다")
        if day > datetime.now(KST).date():
            raise ValueError("한마디 날짜는 KST 오늘을 넘지 않아야 한다")
        try:
            result = await self._message_lookup(
                owner, today=day, request_id=request_id, engine=self._engine
            )
        except health_errors.HealthDataError as exc:
            converted = _PORT_ERRORS.get(type(exc))
            if converted is None:
                raise
            metrics = exc.metrics
        else:
            try:
                data = daily_message_response(result).model_dump(
                    mode="json", by_alias=False
                )
            except (ValidationError, PydanticSerializationError):
                # 서비스 내부 오류와 구분해 공개 응답 변환 실패만 조회 실패로 바꾼다.
                converted = _PORT_ERRORS[health_errors.HealthDataLookupError]
                metrics = MESSAGE_METRICS
            else:
                data["used_metrics"] = list(result.used_metrics)
                return ports.DailyMessageResult(str(result.user_id), data)
        raise converted(metrics=_tool_metrics(metrics)) from None

    async def get_lifestyle_score(
        self, user_id: str, day: date
    ) -> ports.LifestyleScoreResult:
        owner = _owner_id(user_id)
        # 미래 날짜 검사와 is_in_progress 가 같은 날을 쓰도록 KST 오늘을 한 번만 읽는다
        today = datetime.now(KST).date()
        if day > today:
            raise ValueError("점수 날짜는 KST 오늘을 넘지 않아야 한다")
        try:
            result = await self._score_lookup(
                owner, day, session_factory=self._session_factory
            )
        except health_errors.HealthDataError as exc:
            converted = _PORT_ERRORS.get(type(exc))
            if converted is None:
                raise
        else:
            try:
                # 공개 응답 모델은 기본으로 camelCase 별칭을 쓰고, AI 포트 계약은 필드 이름(snake_case)이다
                data = lifestyle_score_response(result, today=today).model_dump(
                    mode="json", by_alias=False
                )
            except (ValidationError, PydanticSerializationError):
                # 홈 점수 API 와 같이 응답 변환·직렬화 실패를 조회 실패로 처리한다
                converted = _PORT_ERRORS[health_errors.HealthDataLookupError]
            else:
                return ports.LifestyleScoreResult(str(result.end_user_id), data)
        # 점수 조회 실패에는 지표가 없어 metrics 를 비운다
        raise converted() from None

    async def get_sleep_sessions(
        self, user_id: str, start: date, end: date
    ) -> ports.SleepSessionsResult:
        """주 수면이 아닌 잠까지 담은 전체 수면 세션. snapshot 은 결과 모델에서 user_id 만 뺀 값이다."""
        owner = _owner_id(user_id)
        failure: ports.HealthDataError | None = None
        try:
            result = await self._sleep_sessions_lookup(
                owner, start, end, session_factory=self._session_factory
            )
        except health_errors.SleepSessionLimitError:
            # 한도 값은 연동 쪽 상수를 쓴다. 하루 조회가 한도를 넘으면 기간을 줄여도 풀리지 않는다
            failure = ports.HealthDataLimitError(
                health_errors.SleepSessionLimitError.code,
                source="sleep_sessions",
                can_narrow=end > start,
                limit=SLEEP_SESSION_LIMIT,
                limit_unit="sessions",
            )
        except ValidationError:
            # 기간·인자는 도구 입력 단계에서 먼저 차단한다. 그래도 연동 쪽 입력 검증에 걸리면 조회 실패다
            failure = ports.HealthDataUnavailableError(metrics=("sleep",))
        except health_errors.HealthDataError as exc:
            converted = _PORT_ERRORS.get(type(exc))
            if converted is None:
                raise
            metrics = exc.metrics
        else:
            return ports.SleepSessionsResult(
                str(result.user_id), result.model_dump(mode="json", exclude={"user_id"})
            )
        # except 블록 밖에서 발생시켜 원래 예외의 문구·cause 를 새 예외에 연결하지 않는다
        if failure is None:
            failure = converted(metrics=_tool_metrics(metrics))
        raise failure from None


def _public_metric(metric: str) -> HealthMetric:
    if metric not in PUBLIC_METRICS:
        raise ValueError(f"건강 조회를 지원하지 않는 도구 지표: {metric}")
    return PUBLIC_METRICS[metric]


def _owner_id(user_id: str) -> UUID:
    """서버가 확인한 users.id 문자열을 UUID 로 바꾼다.

    게이트웨이가 결과 소유자 str(UUID) 와 문자열로 비교하므로 같은 표기(소문자, 하이픈)만 받는다.
    """
    try:
        owner = UUID(user_id)
    except ValueError:
        owner = None
    if owner is None or str(owner) != user_id:
        raise ValueError("user_id 는 소문자·하이픈 표기의 UUID 문자열이어야 한다")
    return owner


def _tool_metrics(metrics: tuple[str, ...]) -> tuple[Metric, ...]:
    unknown = [m for m in metrics if m not in _TOOL_METRICS]
    if unknown:
        raise ValueError(f"코칭 도구 지표로 바꿀 수 없는 공개 지표: {unknown}")
    return tuple(_TOOL_METRICS[m] for m in metrics)
