"""코칭 WebSocket 프레임 검증과 공개 이벤트."""

import base64
import binascii
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Annotated, Any, Literal, cast, get_args
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from app.coaching.repository import (
    Accepted,
    ConversationKey,
    ConversationPage,
    EvidenceInfo,
    MessageInfo,
    MessagePage,
    MessageRole,
    RequestState,
    RequestStatus,
)
from app.core.errors import ApiError
from app.core.schemas import ApiRequest, ApiResponse
from app.core.validation import strict_json
from app.health.messages import health_error_message
from app.health.schemas import (
    DataStatus,
    Detail,
    ExerciseSessionDetail,
    HealthReason,
    HealthSchema,
    HourlyStepsDay,
    Period,
    PublicHealthMetricResult,
    SleepDataStatus,
    SleepSessionDetail,
)
from coach_ai.history.budget import INPUT_TOO_LARGE_MESSAGE
from coach_ai.tools.schemas import PublicMetric

PROTOCOL_VERSION = "1.0"
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
MAX_QUESTION_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class PublicError:
    message: str
    retryable: bool


PUBLIC_ERRORS: Mapping[str, PublicError] = {
    "AUTH_REQUIRED": PublicError("로그인이 필요합니다.", True),
    "AUTH_EXPIRED": PublicError("로그인이 필요합니다.", True),
    "AUTH_UNAVAILABLE": PublicError("인증 처리를 일시적으로 이용할 수 없습니다.", True),
    "INVALID_REQUEST": PublicError("요청 내용을 확인해 주세요.", False),
    "INVALID_MESSAGE": PublicError("요청 내용을 확인해 주세요.", False),
    "INVALID_QUESTION": PublicError("질문 내용을 확인해 주세요.", False),
    "CONVERSATION_NOT_FOUND": PublicError("대화를 찾을 수 없습니다.", False),
    "REQUEST_NOT_FOUND": PublicError("요청을 찾을 수 없습니다.", False),
    "REQUEST_ID_CONFLICT": PublicError("이미 사용한 전송 ID입니다.", False),
    "CONVERSATION_BUSY": PublicError("답변이 생성 중입니다.", True),
    "CANDIY_USER_NOT_LINKED": PublicError(
        "앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 "
        "기다린 뒤 다시 시도해 주세요.",
        True,
    ),
    "HEALTH_DATA_FORBIDDEN": PublicError(
        "건강데이터 조회 권한을 확인해 주세요.", False
    ),
    "HEALTH_DATA_UNAVAILABLE": PublicError(health_error_message("LOOKUP_FAILED"), True),
    "AI_UNAVAILABLE": PublicError("답변을 생성하지 못했습니다.", True),
    "GENERATION_TIMEOUT": PublicError("답변을 생성하지 못했습니다.", True),
    "GENERATION_INTERRUPTED": PublicError("답변을 생성하지 못했습니다.", True),
    "PERSISTENCE_FAILED": PublicError("요청을 처리하지 못했습니다.", True),
    "INTERNAL_ERROR": PublicError("요청을 처리하지 못했습니다.", True),
}


class _Frame(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        hide_input_in_errors=True,
    )


class AuthFrame(_Frame):
    type: Literal["auth"]
    access_token: str = Field(min_length=1)


class QuestionFrame(_Frame):
    type: Literal["question"]
    client_request_id: str = Field(pattern=UUID_PATTERN)
    conversation_id: Annotated[str, Field(pattern=UUID_PATTERN)] | None
    content: str


@dataclass(frozen=True, slots=True)
class Question:
    client_request_id: UUID
    conversation_id: UUID | None
    content: str


class FrameError(Exception):
    """프레임을 해석하지 못했다. 원본 입력 대신 공개 코드만 보관한다."""

    code: str
    client_request_id: str | None

    def __init__(self, code: str, client_request_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.client_request_id = client_request_id


def decode_frame(raw: str) -> dict[str, Any]:
    try:
        obj = strict_json(raw.encode("utf-8"))
    except (ApiError, UnicodeEncodeError):
        raise FrameError("INVALID_MESSAGE") from None
    if not isinstance(obj, dict):
        raise FrameError("INVALID_MESSAGE")
    return obj


def read_client_request_id(obj: Mapping[str, Any]) -> str | None:
    value = obj.get("clientRequestId")
    if isinstance(value, str) and re.fullmatch(UUID_PATTERN, value):
        return value
    return None


def parse_auth(obj: Mapping[str, Any]) -> AuthFrame:
    try:
        return AuthFrame.model_validate(obj)
    except ValidationError:
        raise FrameError("INVALID_MESSAGE") from None


def parse_question(obj: Mapping[str, Any]) -> Question:
    client_request_id = read_client_request_id(obj)
    try:
        frame = QuestionFrame.model_validate(obj)
    except ValidationError:
        raise FrameError("INVALID_MESSAGE", client_request_id) from None
    content = frame.content.strip()
    if not 1 <= len(content) <= MAX_QUESTION_LENGTH:
        raise FrameError("INVALID_QUESTION", client_request_id)
    return Question(
        client_request_id=UUID(frame.client_request_id),
        conversation_id=(
            UUID(frame.conversation_id) if frame.conversation_id is not None else None
        ),
        content=content,
    )


def ready_event() -> dict[str, Any]:
    return {"type": "ready", "data": {"protocolVersion": PROTOCOL_VERSION}}


def connection_error_event(
    code: Literal["AUTH_REQUIRED", "AUTH_EXPIRED", "AUTH_UNAVAILABLE"],
) -> dict[str, Any]:
    """연결 수준 인증 오류 이벤트를 공개 메시지와 함께 만든다."""
    return {
        "type": "connection_error",
        "data": {"code": code, "message": PUBLIC_ERRORS[code].message},
    }


def request_event(
    type_: str,
    *,
    client_request_id: UUID | str | None,
    request_id: UUID | None,
    conversation_id: UUID | None,
    event_seq: int,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "type": type_,
        "clientRequestId": (
            str(client_request_id) if client_request_id is not None else None
        ),
        "requestId": str(request_id) if request_id is not None else None,
        "conversationId": str(conversation_id) if conversation_id is not None else None,
        "eventSeq": event_seq,
        "data": dict(data),
    }


def pre_accept_error(code: str, client_request_id: UUID | str | None) -> dict[str, Any]:
    error = PUBLIC_ERRORS[code]
    return request_event(
        "error",
        client_request_id=client_request_id,
        request_id=None,
        conversation_id=None,
        event_seq=0,
        data={
            "code": code,
            "message": error.message,
            "retryable": error.retryable,
            "accepted": False,
        },
    )


PublicStage = Literal["reading_history", "fetching_health_data", "generating_answer"]


@dataclass(frozen=True, slots=True)
class RequestIds:
    """한 요청의 공개 이벤트에 넣을 세 식별자를 보관한다."""

    client_request_id: UUID
    request_id: UUID
    conversation_id: UUID

    @classmethod
    def from_state(cls, state: RequestState) -> "RequestIds":
        """저장된 요청 상태에서 공개 이벤트 식별자를 가져온다."""
        return cls(state.client_request_id, state.id, state.conversation_id)


class ConversationData(ApiResponse):
    """접수 이벤트에 포함할 대화 정보를 camelCase로 직렬화한다."""

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class AssistantMessageData(ApiResponse):
    """요청 조회와 완료 이벤트에서 사용할 AI 메시지를 직렬화한다."""

    id: UUID
    role: Literal["assistant"]
    sequence_no: int
    content: str
    created_at: datetime


class AcceptedData(ApiResponse):
    """접수된 메시지 식별자·순번과 대화 정보를 직렬화한다."""

    status: Literal["accepted"]
    user_message_id: UUID
    assistant_message_id: UUID
    user_sequence_no: int
    assistant_sequence_no: int
    conversation: ConversationData


class ErrorContextData(ApiResponse):
    """저장된 건강 조회 실패 사유와 공개 지표를 검증한다."""

    reason: HealthReason
    metrics: list[PublicMetric]


class EvidenceData(ApiResponse):
    """근거 외곽을 camelCase로 직렬화하고 snapshot 내부 키는 그대로 보존한다."""

    id: UUID
    position: int
    metric: PublicMetric
    period_start: date
    period_end: date
    snapshot: dict[str, Any]


MAX_SEQUENCE_NO = 9_223_372_036_854_775_807


def _query_integer(
    value: object, *, minimum: int, maximum: int, optional: bool = False
) -> int | None:
    """쿼리의 ASCII 정수와 선택적 None을 검증하고 범위 안의 정수로 반환한다.

    부호와 선행 0을 뺀 유효 자릿수가 20자를 넘으면 변환하지 않고 범위 오류로 거부한다.
    오류 문구에는 입력값을 포함하지 않는다.
    """
    if value is None and optional:
        return None
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        digits = value.removeprefix("-").lstrip("0") or "0"
        if len(digits) > 20:
            raise PydanticCustomError("out_of_range", "Integer out of range")
        number = int(digits)
        if value.startswith("-"):
            number = -number
    else:
        raise PydanticCustomError("invalid_type", "Integer required")
    if not minimum <= number <= maximum:
        raise PydanticCustomError("out_of_range", "Integer out of range")
    return number


class RequestStateData(ApiResponse):
    """요청 조회와 request_snapshot이 공유하는 현재 상태를 직렬화한다."""

    request_id: UUID
    client_request_id: UUID
    conversation_id: UUID
    status: RequestStatus
    user_message_id: UUID
    assistant_message: AssistantMessageData
    evidence: list[EvidenceData]
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
    error_context: ErrorContextData | None


class MessagePageQuery(ApiRequest):
    """메시지 조회 개수와 제외할 경계 순번을 기본값까지 검증한다."""

    limit: Annotated[
        int, BeforeValidator(partial(_query_integer, minimum=1, maximum=100))
    ] = Field(default=50, ge=1, le=100, validate_default=True)
    before_sequence_no: Annotated[
        int | None,
        BeforeValidator(
            partial(_query_integer, minimum=1, maximum=MAX_SEQUENCE_NO, optional=True)
        ),
    ] = Field(default=None, ge=1, le=MAX_SEQUENCE_NO, validate_default=True)


class ConversationRestData(ApiResponse):
    """WS 대화 모델과 별도로 요약을 포함한 REST 대화를 직렬화한다."""

    id: UUID
    title: str
    summary: str | None
    created_at: datetime
    updated_at: datetime


class MessageItemData(ApiResponse):
    """각 메시지의 저장 본문과 소속 요청의 상태·오류를 직렬화한다."""

    id: UUID
    request_id: UUID
    role: MessageRole
    sequence_no: int
    content: str
    request_status: RequestStatus
    error_code: str | None
    created_at: datetime
    evidence: list[EvidenceData]
    error_context: ErrorContextData | None


class MessagePageData(ApiResponse):
    """대화와 메시지 목록 및 이전 페이지 경계 순번을 직렬화한다."""

    conversation: ConversationRestData
    items: list[MessageItemData]
    next_before_sequence_no: int | None


CURSOR_VERSION = "1"
MAX_CURSOR_LENGTH = 128
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def encode_conversation_cursor(key: ConversationKey) -> str:
    """대화 정렬 키를 정수 마이크로초와 UUID의 패딩 없는 base64url 커서로 바꾼다.

    시간대 없는 시각과 에포크 이전 시각은 서버 오류인 ValueError로 알린다.
    """
    updated_at = key.updated_at
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if updated_at < EPOCH:
        raise ValueError("timestamp must not precede epoch")
    micros = (updated_at - EPOCH) // timedelta(microseconds=1)
    text = f"{CURSOR_VERSION}.{micros}.{key.id.hex}".encode("ascii")
    return base64.urlsafe_b64encode(text).rstrip(b"=").decode("ascii")


def decode_conversation_cursor(value: str) -> ConversationKey:
    """서버가 만든 대화 목록 커서를 엄격하게 해석해 정렬 위치로 돌려준다.

    형식·버전·표기가 다르면 입력값 없이 ValueError를 던진다.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_CURSOR_LENGTH:
        raise ValueError("Invalid conversation cursor")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("Invalid conversation cursor")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        text = decoded.decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("Invalid conversation cursor") from None
    match = re.fullmatch(r"1\.(0|[1-9][0-9]{0,16})\.([0-9a-f]{32})", text)
    if match is None:
        raise ValueError("Invalid conversation cursor")
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise ValueError("Invalid conversation cursor")
    return ConversationKey(
        updated_at=EPOCH + timedelta(microseconds=int(match[1])),
        id=UUID(hex=match[2]),
    )


def _query_conversation_cursor(value: object) -> ConversationKey | None:
    """생략한 커서는 None으로 두고 문자열 커서의 타입·형식 오류를 공개 사유로 바꾼다."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PydanticCustomError("invalid_type", "String required")
    try:
        return decode_conversation_cursor(value)
    except ValueError:
        raise PydanticCustomError(
            "invalid_format", "Invalid conversation cursor"
        ) from None


class ConversationPageQuery(ApiRequest):
    """대화 조회 개수와 커서를 검증해 정수와 정렬 키로 반환한다."""

    limit: Annotated[
        int, BeforeValidator(partial(_query_integer, minimum=1, maximum=100))
    ] = Field(default=20, ge=1, le=100, validate_default=True)
    cursor: Annotated[
        ConversationKey | None,
        BeforeValidator(_query_conversation_cursor, json_schema_input_type=str | None),
    ] = Field(default=None, validate_default=True)


class ConversationItemData(ConversationRestData):
    """REST 대화 정보 뒤에 가장 최근 접수된 요청 상태를 직렬화한다."""

    last_request_status: RequestStatus


class ConversationPageData(ApiResponse):
    """대화 목록과 이어 읽을 서버 커서를 직렬화한다."""

    items: list[ConversationItemData]
    next_cursor: str | None


class CompletedData(ApiResponse):
    """저장이 끝난 AI 답변과 완료 시각을 직렬화한다."""

    status: Literal["completed"]
    assistant_message: AssistantMessageData
    evidence: list[EvidenceData]
    finished_at: datetime


def _utc(value: datetime) -> datetime:
    """시간대가 있는 시각을 UTC로 바꾸고, 시간대가 없으면 ValueError를 낸다."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RequestFailure:
    """공개 오류와 건강 저장 문맥·실시간 입력 초과 안내를 보관한다."""

    code: str
    reason: HealthReason | None = None
    metrics: tuple[PublicMetric, ...] = ()
    input_too_large: bool = False

    def __post_init__(self) -> None:
        """공개 코드·건강 문맥·입력 초과 안내의 조합이 잘못되면 거부한다."""
        if self.code not in PUBLIC_ERRORS:
            raise ValueError("unknown public error code")
        if type(self.input_too_large) is not bool or (
            self.input_too_large and self.code != "AI_UNAVAILABLE"
        ):
            raise ValueError("input size failure requires AI_UNAVAILABLE")
        if self.code == "HEALTH_DATA_UNAVAILABLE":
            if self.reason not in get_args(HealthReason):
                raise ValueError("health failure requires a public reason")
            if any(metric not in get_args(PublicMetric) for metric in self.metrics):
                raise ValueError("health failure requires public metrics")
        elif self.reason is not None or self.metrics:
            raise ValueError("only health failures may include reason or metrics")

    def error_context(self) -> dict[str, Any] | None:
        """건강 조회 실패면 DB에 저장할 사유·지표를, 그 밖에는 None을 반환한다."""
        if self.code == "HEALTH_DATA_UNAVAILABLE":
            return {"reason": self.reason, "metrics": list(self.metrics)}
        return None

    def event_data(self, *, accepted: bool) -> dict[str, Any]:
        """건강 사유 또는 입력 초과의 범위 축소 안내를 기존 공개 필드로 반환한다."""
        error = (
            PublicError(INPUT_TOO_LARGE_MESSAGE, False)
            if self.input_too_large
            else PUBLIC_ERRORS[self.code]
        )
        data = {
            "code": self.code,
            "message": (
                health_error_message(self.reason, self.metrics)
                if self.reason is not None
                else error.message
            ),
            "retryable": error.retryable,
            "accepted": accepted,
        }
        context = self.error_context()
        if context is not None:
            data.update(context)
        return data


def failure_from_coach_error(
    code: str | None, metrics: Sequence[str]
) -> RequestFailure:
    """coach_ai 내부 오류 코드를 공개 코칭 오류로 바꾼다.

    입력 초과는 재시도 불가 안내로, 건강 실패는 사유·지표로, 모르는 코드는 내부 오류로 바꾼다.
    """
    if code == "input_too_large":
        return RequestFailure("AI_UNAVAILABLE", input_too_large=True)
    # ai/docs/agent-interface.md §5의 공개 코드 대응은 이 경계에서만 관리한다.
    failures: dict[str | None, tuple[str, HealthReason | None]] = {
        "llm_unavailable": ("AI_UNAVAILABLE", None),
        "llm_timeout": ("AI_UNAVAILABLE", None),
        "rate_limited": ("AI_UNAVAILABLE", None),
        "invalid_request": ("AI_UNAVAILABLE", None),
        "user_not_linked": ("CANDIY_USER_NOT_LINKED", None),
        "data_not_ready": ("HEALTH_DATA_UNAVAILABLE", "DATA_NOT_READY"),
        "data_unavailable": ("HEALTH_DATA_UNAVAILABLE", "LOOKUP_FAILED"),
        "forbidden": ("HEALTH_DATA_FORBIDDEN", None),
        "internal": ("INTERNAL_ERROR", None),
    }
    public_code, reason = failures.get(code, ("INTERNAL_ERROR", None))
    if reason is None:
        return RequestFailure(public_code)
    public_metrics = tuple(
        dict.fromkeys(
            cast(PublicMetric, metric)
            for metric in metrics
            if metric in get_args(PublicMetric)
        )
    )
    return RequestFailure(public_code, reason, public_metrics)


def _assistant_message_data(message: MessageInfo, content: str) -> AssistantMessageData:
    """AI 메시지의 역할과 시각을 확인하고 지정된 본문으로 공개 모델을 만든다."""
    if message.role != "assistant":
        raise ValueError("assistant message required")
    return AssistantMessageData(
        id=message.id,
        role="assistant",
        sequence_no=message.sequence_no,
        content=content,
        created_at=_utc(message.created_at),
    )


class EvidenceFormatError(Exception):
    """AI 근거가 명세 §2.3 형식과 다름을 입력값 없이 알린다."""


class _EvidenceExerciseSession(ExerciseSessionDetail):
    """record_id가 없는 도구 결과(합성 어댑터 등)를 허용한다. DB/REST 계약은 필수다."""

    record_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("record_id", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        """키 부재와 달리 명시적인 null은 잘못된 식별자로 거부한다."""
        if value is None:
            raise ValueError("record_id cannot be null")
        return value


class _EvidenceDetail(Detail):
    """새 AI 결과의 상세를 검증하며 운동 식별자 키 부재를 허용한다."""

    sessions: list[_EvidenceExerciseSession]


class _EvidenceMetricResult(PublicHealthMetricResult):
    """새 AI 근거 원형을 검증한다. 저장된 근거 재조회에는 적용하지 않는다."""

    detail: _EvidenceDetail


EvidenceKind = Literal["hourly_steps", "sleep_sessions"]


class _EvidenceHourlySteps(HealthSchema):
    """kind=hourly_steps 근거 snapshot. HourlyStepsResult에서 user_id를 뺀 모양이다."""

    kind: Literal["hourly_steps"]
    period: Period
    start_hour: int
    end_hour: int
    days: list[HourlyStepsDay]
    total: int | None = Field(strict=True, ge=0)
    data_status: DataStatus


class _EvidenceSleepSessions(HealthSchema):
    """kind=sleep_sessions 근거 snapshot. SleepSessionsResult에서 user_id를 뺀 모양이다."""

    kind: Literal["sleep_sessions"]
    period: Period
    sessions: list[SleepSessionDetail]
    data_status: SleepDataStatus


_KIND_MODELS: dict[str, type[HealthSchema]] = {
    "hourly_steps": _EvidenceHourlySteps,
    "sleep_sessions": _EvidenceSleepSessions,
}
# kind 근거의 외곽 metric. 2.3 "종류별 snapshot" 표
_KIND_METRIC: dict[str, PublicMetric] = {
    "hourly_steps": "steps",
    "sleep_sessions": "sleeps",
}


def _evidence_item(item: object) -> EvidenceInfo:
    """AI 근거 항목 하나를 공개 근거로 바꾸고 snapshot이 §2.2 원형인지 확인한다.

    형식이 다르면 입력값과 원인 연결 없이 EvidenceFormatError를 던진다.
    """
    try:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise ValueError
        evidence_id = UUID(item["id"])
        position = item.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or position < 1:
            raise ValueError
        metric = item.get("metric")
        if metric not in get_args(PublicMetric):
            raise ValueError
        period = item.get("period")
        if not isinstance(period, Mapping):
            raise ValueError
        start, end = period.get("start"), period.get("end")
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
            for value in (start, end)
        ):
            raise ValueError
        period_start, period_end = (
            date.fromisoformat(start),
            date.fromisoformat(end),
        )
        if period_start > period_end:
            raise ValueError
        snapshot_raw = item.get("snapshot")
        if isinstance(snapshot_raw, Mapping) and "kind" in snapshot_raw:
            kind = snapshot_raw.get("kind")
            if (
                not isinstance(kind, str)
                or kind not in _KIND_MODELS
                or _KIND_METRIC[kind] != metric
            ):
                raise ValueError
            snapshot_data = (
                _KIND_MODELS[kind].model_validate(snapshot_raw).model_dump(mode="json")
            )
        else:
            snapshot = _EvidenceMetricResult.model_validate(item.get("snapshot"))
            snapshot_data = snapshot.model_dump(mode="json")
            for parsed, output in zip(
                snapshot.days, snapshot_data["days"], strict=True
            ):
                for field in ("wear_hours", "is_well_worn"):
                    if field not in parsed.model_fields_set:
                        output.pop(field)
            for parsed, output in zip(
                snapshot.detail.nights, snapshot_data["detail"]["nights"], strict=True
            ):
                if "spo2_low_times" not in parsed.model_fields_set:
                    output.pop("spo2_low_times")
            for parsed, output in zip(
                snapshot.detail.sessions,
                snapshot_data["detail"]["sessions"],
                strict=True,
            ):
                if "record_id" not in parsed.model_fields_set:
                    output.pop("record_id")
        return EvidenceInfo(
            id=evidence_id,
            position=position,
            metric=cast(PublicMetric, metric),
            period_start=period_start,
            period_end=period_end,
            snapshot=snapshot_data,
        )
    except (ValueError, TypeError):
        # 검증 예외의 입력값이 __context__에도 남지 않도록 except 밖에서 새 오류를 낸다.
        pass
    raise EvidenceFormatError("Evidence format mismatch") from None


def _skipped_metric(item: object) -> str:
    """버린 항목의 지표 이름을 돌려준다. 공개 지표 목록에 있는 이름만 쓰고 그 밖은 "unknown"이다."""
    if isinstance(item, Mapping):
        metric = item.get("metric")
        if isinstance(metric, str) and metric in get_args(PublicMetric):
            return metric
    return "unknown"


@dataclass(frozen=True, slots=True)
class EvidencePartition:
    """형식이 맞는 공개 근거와 버린 항목의 지표 이름(skipped_metrics)을 함께 보관한다.

    skipped_metrics는 버린 항목마다 입력 순서대로 하나씩 담으며 값은 공개 지표 이름 또는 "unknown"이다.
    """

    valid: tuple[EvidenceInfo, ...]
    skipped_metrics: tuple[str, ...]

    @property
    def skipped(self) -> int:
        """버린 항목 수를 돌려준다."""
        return len(self.skipped_metrics)


def partition_evidence(items: object) -> EvidencePartition:
    """AI 근거를 항목마다 확인해 맞는 것만 순서대로 남기고 버린 항목의 지표 이름을 모은다.

    지표 이름은 공개 지표 목록에 있는 것만 쓰고 목록에 없거나 문자열·Mapping이 아니면 "unknown"이다.
    list·tuple이 아니면 맞는 근거 없이 ("unknown",) 1건을 버린 것으로 본다. 빈 목록은 (빈 튜플, 빈 튜플)이다.
    예외를 던지지 않는다.
    """
    if not isinstance(items, (list, tuple)):
        return EvidencePartition((), ("unknown",))
    valid: list[EvidenceInfo] = []
    skipped: list[str] = []
    for item in items:
        try:
            valid.append(_evidence_item(item))
        except EvidenceFormatError:
            skipped.append(_skipped_metric(item))
    return EvidencePartition(tuple(valid), tuple(skipped))


def evidence_data(items: Sequence[EvidenceInfo]) -> list[EvidenceData]:
    """저장 근거를 공개 모델로 옮기고 snapshot은 재검증 없이 사본으로 보존한다."""
    return [
        EvidenceData(
            id=item.id,
            position=item.position,
            metric=item.metric,
            period_start=item.period_start,
            period_end=item.period_end,
            snapshot=dict(item.snapshot),
        )
        for item in items
    ]


def request_state_data(state: RequestState) -> RequestStateData:
    """저장된 본문과 오류 문맥을 보존해 요청 상태의 공개 data를 만든다."""
    return RequestStateData(
        request_id=state.id,
        client_request_id=state.client_request_id,
        conversation_id=state.conversation_id,
        status=state.status,
        user_message_id=state.user_message.id,
        assistant_message=_assistant_message_data(
            state.assistant_message, state.assistant_message.content
        ),
        evidence=evidence_data(state.evidence),
        error_code=state.error_code,
        created_at=_utc(state.created_at),
        finished_at=_utc(state.finished_at) if state.finished_at is not None else None,
        error_context=(
            ErrorContextData.model_validate(state.error_context)
            if state.error_context is not None
            else None
        ),
    )


def message_page_data(page: MessagePage) -> MessagePageData:
    """저장된 대화·메시지를 UTC 시각과 이전 페이지 커서가 있는 REST 응답으로 바꾼다."""
    conversation = page.conversation
    return MessagePageData(
        conversation=ConversationRestData(
            id=conversation.id,
            title=conversation.title,
            summary=conversation.summary,
            created_at=_utc(conversation.created_at),
            updated_at=_utc(conversation.updated_at),
        ),
        items=[
            MessageItemData(
                id=item.message.id,
                request_id=item.request_id,
                role=item.message.role,
                sequence_no=item.message.sequence_no,
                content=item.message.content,
                request_status=item.request_status,
                error_code=item.error_code,
                created_at=_utc(item.message.created_at),
                evidence=evidence_data(item.evidence),
                error_context=(
                    ErrorContextData.model_validate(item.error_context)
                    if item.error_context is not None
                    else None
                ),
            )
            for item in page.messages
        ],
        next_before_sequence_no=(
            page.messages[0].message.sequence_no
            if page.has_more and page.messages
            else None
        ),
    )


def conversation_page_data(page: ConversationPage) -> ConversationPageData:
    """대화 목록 시각을 UTC로 바꾸고 마지막 항목의 키로 다음 페이지 커서를 만든다."""
    return ConversationPageData(
        items=[
            ConversationItemData(
                id=item.conversation.id,
                title=item.conversation.title,
                summary=item.conversation.summary,
                created_at=_utc(item.conversation.created_at),
                updated_at=_utc(item.conversation.updated_at),
                last_request_status=item.last_request_status,
            )
            for item in page.items
        ],
        next_cursor=(
            encode_conversation_cursor(
                ConversationKey(
                    updated_at=page.items[-1].conversation.updated_at,
                    id=page.items[-1].conversation.id,
                )
            )
            if page.has_more and page.items
            else None
        ),
    )


def accepted_event(accepted: Accepted) -> dict[str, Any]:
    """접수 결과의 메시지와 대화 정보를 eventSeq 1의 accepted로 만든다."""
    state = accepted.request
    conversation = accepted.conversation
    data = AcceptedData(
        status="accepted",
        user_message_id=state.user_message.id,
        assistant_message_id=state.assistant_message.id,
        user_sequence_no=state.user_message.sequence_no,
        assistant_sequence_no=state.assistant_message.sequence_no,
        conversation=ConversationData(
            id=conversation.id,
            title=conversation.title,
            created_at=_utc(conversation.created_at),
            updated_at=_utc(conversation.updated_at),
        ),
    )
    return request_event(
        "accepted",
        client_request_id=state.client_request_id,
        request_id=state.id,
        conversation_id=state.conversation_id,
        event_seq=1,
        data=data.model_dump(mode="json"),
    )


def request_snapshot_event(state: RequestState) -> dict[str, Any]:
    """요청 조회와 동일한 data를 eventSeq 0의 request_snapshot으로 만든다."""
    return request_event(
        "request_snapshot",
        client_request_id=state.client_request_id,
        request_id=state.id,
        conversation_id=state.conversation_id,
        event_seq=0,
        data=request_state_data(state).model_dump(mode="json"),
    )


def status_event(ids: RequestIds, event_seq: int, stage: PublicStage) -> dict[str, Any]:
    """허용된 공개 진행 단계를 status 이벤트로 만든다."""
    if stage not in get_args(PublicStage):
        raise ValueError("unknown public stage")
    return request_event(
        "status",
        client_request_id=ids.client_request_id,
        request_id=ids.request_id,
        conversation_id=ids.conversation_id,
        event_seq=event_seq,
        data={"stage": stage},
    )


def delta_event(ids: RequestIds, event_seq: int, text: str) -> dict[str, Any]:
    """답변 조각을 지정된 요청의 delta 이벤트로 만든다."""
    return request_event(
        "delta",
        client_request_id=ids.client_request_id,
        request_id=ids.request_id,
        conversation_id=ids.conversation_id,
        event_seq=event_seq,
        data={"text": text},
    )


def evidence_event(
    ids: RequestIds, event_seq: int, items: Sequence[EvidenceInfo]
) -> dict[str, Any]:
    """누적 근거 전체를 공개 evidence 이벤트로 만든다."""
    return request_event(
        "evidence",
        client_request_id=ids.client_request_id,
        request_id=ids.request_id,
        conversation_id=ids.conversation_id,
        event_seq=event_seq,
        data={"items": [item.model_dump(mode="json") for item in evidence_data(items)]},
    )


def completed_event(
    ids: RequestIds,
    event_seq: int,
    *,
    assistant_message: MessageInfo,
    answer: str,
    evidence: Sequence[EvidenceInfo],
    finished_at: datetime,
) -> dict[str, Any]:
    """저장한 최종 answer와 AI 메시지 메타데이터로 completed 이벤트를 만든다."""
    data = CompletedData(
        status="completed",
        assistant_message=_assistant_message_data(assistant_message, answer),
        evidence=evidence_data(evidence),
        finished_at=_utc(finished_at),
    )
    return request_event(
        "completed",
        client_request_id=ids.client_request_id,
        request_id=ids.request_id,
        conversation_id=ids.conversation_id,
        event_seq=event_seq,
        data=data.model_dump(mode="json"),
    )


def accepted_error_event(
    ids: RequestIds, event_seq: int, failure: RequestFailure
) -> dict[str, Any]:
    """접수된 요청의 공개 실패를 accepted=true인 error 이벤트로 만든다."""
    return request_event(
        "error",
        client_request_id=ids.client_request_id,
        request_id=ids.request_id,
        conversation_id=ids.conversation_id,
        event_seq=event_seq,
        data=failure.event_data(accepted=True),
    )
