"""공개 코칭 이벤트와 내부 이벤트 변환 계약을 합성 데이터로 검증한다."""

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from app.coaching.events import PublicEventSink
from app.coaching.repository import (
    Accepted,
    ConversationInfo,
    MessageInfo,
    RequestState,
    RequestStatus,
)
from app.coaching.schemas import (
    HealthReason,
    RequestFailure,
    RequestIds,
    accepted_error_event,
    accepted_event,
    completed_event,
    failure_from_coach_error,
    request_snapshot_event,
    request_state_data,
    status_event,
)
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.http import health_api_error
from coach_ai.tools.schemas import PublicMetric
from tests.coaching.fakes import SYNTHETIC_SNAPSHOT, synthetic_evidence

CLIENT_ID = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")
REQUEST_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_MESSAGE_ID = UUID("44444444-4444-4444-8444-444444444444")
ASSISTANT_MESSAGE_ID = UUID("55555555-5555-4555-8555-555555555555")
CREATED_AT = datetime(2026, 10, 19, 1, tzinfo=UTC)
FINISHED_AT = datetime(2026, 10, 19, 1, 0, 2, tzinfo=UTC)
IDS = RequestIds(CLIENT_ID, REQUEST_ID, CONVERSATION_ID)


class RecordingChannel:
    """송신 이벤트를 기록하거나 설정된 합성 예외를 던진다."""

    def __init__(self) -> None:
        """빈 송신 목록과 실패 설정을 준비한다."""
        self.events: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def send(self, event: Mapping[str, Any]) -> None:
        """실패 설정이 있으면 예외를 던지고, 없으면 이벤트를 기록한다."""
        if self.error is not None:
            raise self.error
        self.events.append(dict(event))


@pytest.fixture
def state() -> RequestState:
    """합성 UUIDv4와 고정 UTC 시각으로 완료 요청을 만든다."""
    return RequestState(
        id=REQUEST_ID,
        client_request_id=CLIENT_ID,
        conversation_id=CONVERSATION_ID,
        status="completed",
        user_message=MessageInfo(
            USER_MESSAGE_ID, "user", 1, "지난주 걸음 기록이 며칠 있어?", CREATED_AT
        ),
        assistant_message=MessageInfo(
            ASSISTANT_MESSAGE_ID, "assistant", 2, "합성 최종 답변", CREATED_AT
        ),
        error_code=None,
        error_context=None,
        created_at=CREATED_AT,
        deadline_at=CREATED_AT + timedelta(seconds=120),
        finished_at=FINISHED_AT,
    )


@pytest.fixture
def accepted(state: RequestState) -> Accepted:
    """완료 요청의 합성 식별자를 재사용해 접수 직후 상태를 만든다."""
    return Accepted(
        request=replace(
            state,
            status="accepted",
            assistant_message=replace(state.assistant_message, content=""),
            finished_at=None,
        ),
        conversation=ConversationInfo(
            CONVERSATION_ID, "지난주 걸음 기록이 며칠 있어?", CREATED_AT, CREATED_AT
        ),
    )


def test_accepted_event_new_request_matches_public_contract(accepted: Accepted) -> None:
    """새 요청을 접수하면 → 명세 키와 JSON 값으로 eventSeq 1을 만든다."""
    event = accepted_event(accepted)
    assert event == {
        "type": "accepted",
        "clientRequestId": "11111111-1111-4111-8111-111111111111",
        "requestId": "33333333-3333-4333-8333-333333333333",
        "conversationId": "22222222-2222-4222-8222-222222222222",
        "eventSeq": 1,
        "data": {
            "status": "accepted",
            "userMessageId": "44444444-4444-4444-8444-444444444444",
            "assistantMessageId": "55555555-5555-4555-8555-555555555555",
            "userSequenceNo": 1,
            "assistantSequenceNo": 2,
            "conversation": {
                "id": "22222222-2222-4222-8222-222222222222",
                "title": "지난주 걸음 기록이 며칠 있어?",
                "createdAt": "2026-10-19T01:00:00Z",
                "updatedAt": "2026-10-19T01:00:00Z",
            },
        },
    }
    assert json.loads(json.dumps(event)) == event


def test_snapshot_completed_request_preserves_saved_result(state: RequestState) -> None:
    """완료 요청을 조회하면 → 저장 답변과 종료 시각을 eventSeq 0으로 반환한다."""
    event = request_snapshot_event(state)
    assert event == {
        "type": "request_snapshot",
        "clientRequestId": "11111111-1111-4111-8111-111111111111",
        "requestId": "33333333-3333-4333-8333-333333333333",
        "conversationId": "22222222-2222-4222-8222-222222222222",
        "eventSeq": 0,
        "data": {
            "requestId": "33333333-3333-4333-8333-333333333333",
            "clientRequestId": "11111111-1111-4111-8111-111111111111",
            "conversationId": "22222222-2222-4222-8222-222222222222",
            "status": "completed",
            "userMessageId": "44444444-4444-4444-8444-444444444444",
            "assistantMessage": {
                "id": "55555555-5555-4555-8555-555555555555",
                "role": "assistant",
                "sequenceNo": 2,
                "content": "합성 최종 답변",
                "createdAt": "2026-10-19T01:00:00Z",
            },
            "evidence": [],
            "errorCode": None,
            "createdAt": "2026-10-19T01:00:00Z",
            "finishedAt": "2026-10-19T01:00:02Z",
            "errorContext": None,
        },
    }
    assert json.loads(json.dumps(event)) == event


@pytest.mark.parametrize("status", ["accepted", "generating"])
def test_snapshot_in_progress_request_has_empty_content(
    accepted: Accepted, status: RequestStatus
) -> None:
    """진행 중인 요청을 조회하면 → 빈 본문과 null 종료 시각을 반환한다."""
    event = request_snapshot_event(replace(accepted.request, status=status))
    assert event["eventSeq"] == 0
    assert event["requestId"] == "33333333-3333-4333-8333-333333333333"
    assert event["conversationId"] == "22222222-2222-4222-8222-222222222222"
    assert event["data"]["status"] == status
    assert event["data"]["assistantMessage"]["content"] == ""
    assert event["data"]["finishedAt"] is None
    assert event["data"]["errorCode"] is None
    assert event["data"]["errorContext"] is None
    assert event["data"]["evidence"] == []


def test_snapshot_health_failure_preserves_error_context(state: RequestState) -> None:
    """건강 조회 실패를 복구하면 → 저장된 사유와 지표를 그대로 반환한다."""
    failed = replace(
        state,
        id=UUID("99999999-9999-4999-8999-999999999999"),
        client_request_id=UUID("88888888-8888-4888-8888-888888888888"),
        status="failed",
        user_message=replace(
            state.user_message,
            id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            sequence_no=3,
            created_at=CREATED_AT + timedelta(seconds=3),
        ),
        assistant_message=replace(
            state.assistant_message,
            id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            sequence_no=4,
            content="",
            created_at=CREATED_AT + timedelta(seconds=3),
        ),
        error_code="HEALTH_DATA_UNAVAILABLE",
        error_context={"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
        created_at=CREATED_AT + timedelta(seconds=3),
        finished_at=CREATED_AT + timedelta(seconds=5),
    )
    event = request_snapshot_event(failed)
    assert event["eventSeq"] == 0
    assert event["requestId"] == "99999999-9999-4999-8999-999999999999"
    assert event["clientRequestId"] == "88888888-8888-4888-8888-888888888888"
    assert event["conversationId"] == "22222222-2222-4222-8222-222222222222"
    assert event["data"] == {
        "requestId": "99999999-9999-4999-8999-999999999999",
        "clientRequestId": "88888888-8888-4888-8888-888888888888",
        "conversationId": "22222222-2222-4222-8222-222222222222",
        "status": "failed",
        "userMessageId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "assistantMessage": {
            "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "role": "assistant",
            "sequenceNo": 4,
            "content": "",
            "createdAt": "2026-10-19T01:00:03Z",
        },
        "evidence": [],
        "errorCode": "HEALTH_DATA_UNAVAILABLE",
        "createdAt": "2026-10-19T01:00:03Z",
        "finishedAt": "2026-10-19T01:00:05Z",
        "errorContext": {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
    }


def test_snapshot_failed_request_preserves_partial_answer(state: RequestState) -> None:
    """실패 요청에 부분 답변이 있으면 → 저장된 본문을 지우지 않는다."""
    failed = replace(
        state,
        status="failed",
        assistant_message=replace(state.assistant_message, content="합성 부분 답변"),
        error_code="AI_UNAVAILABLE",
    )
    data = request_state_data(failed).model_dump(mode="json")
    assert data["assistantMessage"]["content"] == "합성 부분 답변"
    assert data["errorContext"] is None


def test_request_state_data_serialized_fields_match_eleven_contract_keys(
    state: RequestState,
) -> None:
    """요청 상태를 직렬화하면 → 명세의 11개 필드만 명세 순서대로 반환한다."""
    data = request_state_data(state).model_dump(mode="json")
    assert list(data) == [
        "requestId",
        "clientRequestId",
        "conversationId",
        "status",
        "userMessageId",
        "assistantMessage",
        "evidence",
        "errorCode",
        "createdAt",
        "finishedAt",
        "errorContext",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (CREATED_AT.replace(microsecond=123456), "2026-10-19T01:00:00.123456Z"),
        (
            datetime(2026, 10, 19, 10, tzinfo=timezone(timedelta(hours=9))),
            "2026-10-19T01:00:00Z",
        ),
    ],
)
def test_event_timestamps_aware_values_serialize_as_utc(
    accepted: Accepted, state: RequestState, value: datetime, expected: str
) -> None:
    """마이크로초 또는 KST 시각을 넣으면 → 모든 공개 시각을 UTC Z로 직렬화한다."""
    acceptance = accepted_event(
        replace(
            accepted,
            conversation=replace(
                accepted.conversation, created_at=value, updated_at=value
            ),
        )
    )
    message = replace(state.assistant_message, created_at=value)
    snapshot = request_snapshot_event(
        replace(state, assistant_message=message, created_at=value, finished_at=value)
    )
    completion = completed_event(
        IDS,
        8,
        assistant_message=message,
        answer="합성 답변",
        evidence=(),
        finished_at=value,
    )
    assert acceptance["data"]["conversation"]["createdAt"] == expected
    assert acceptance["data"]["conversation"]["updatedAt"] == expected
    assert snapshot["data"]["createdAt"] == expected
    assert snapshot["data"]["finishedAt"] == expected
    assert snapshot["data"]["assistantMessage"]["createdAt"] == expected
    assert completion["data"]["finishedAt"] == expected
    assert completion["data"]["assistantMessage"]["createdAt"] == expected


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_accepted_event_naive_timestamp_raises_value_error(
    accepted: Accepted, field: str
) -> None:
    """대화 시각에 시간대가 없으면 → 접수 이벤트 생성을 거절한다."""
    conversation = replace(
        accepted.conversation, **{field: CREATED_AT.replace(tzinfo=None)}
    )
    with pytest.raises(ValueError):
        accepted_event(replace(accepted, conversation=conversation))


@pytest.mark.parametrize("field", ["created_at", "finished_at", "assistant_message"])
def test_snapshot_naive_timestamp_raises_value_error(
    state: RequestState, field: str
) -> None:
    """상태나 AI 메시지 시각에 시간대가 없으면 → 상태 이벤트 생성을 거절한다."""
    value: Any = CREATED_AT.replace(tzinfo=None)
    if field == "assistant_message":
        value = replace(state.assistant_message, created_at=value)
    with pytest.raises(ValueError):
        request_snapshot_event(replace(state, **{field: value}))


@pytest.mark.parametrize("field", ["created_at", "finished_at"])
def test_completed_event_naive_timestamp_raises_value_error(
    state: RequestState, field: str
) -> None:
    """완료 이벤트 시각에 시간대가 없으면 → 완료 이벤트 생성을 거절한다."""
    message = state.assistant_message
    finished_at = FINISHED_AT
    if field == "created_at":
        message = replace(message, created_at=CREATED_AT.replace(tzinfo=None))
    else:
        finished_at = FINISHED_AT.replace(tzinfo=None)
    with pytest.raises(ValueError):
        completed_event(
            IDS,
            8,
            assistant_message=message,
            answer="합성 답변",
            evidence=(),
            finished_at=finished_at,
        )


def test_completed_event_final_answer_overrides_message_content(
    state: RequestState,
) -> None:
    """완료 답변을 전달하면 → 메시지 메타데이터와 최종 answer를 함께 반환한다."""
    event = completed_event(
        IDS,
        8,
        assistant_message=state.assistant_message,
        answer="저장한 최종 답변",
        evidence=(),
        finished_at=FINISHED_AT,
    )
    assert event == {
        "type": "completed",
        "clientRequestId": "11111111-1111-4111-8111-111111111111",
        "requestId": "33333333-3333-4333-8333-333333333333",
        "conversationId": "22222222-2222-4222-8222-222222222222",
        "eventSeq": 8,
        "data": {
            "status": "completed",
            "assistantMessage": {
                "id": "55555555-5555-4555-8555-555555555555",
                "role": "assistant",
                "sequenceNo": 2,
                "content": "저장한 최종 답변",
                "createdAt": "2026-10-19T01:00:00Z",
            },
            "evidence": [],
            "finishedAt": "2026-10-19T01:00:02Z",
        },
    }
    assert json.loads(json.dumps(event)) == event


def test_completed_event_user_role_raises_value_error(state: RequestState) -> None:
    """사용자 메시지를 완료 답변으로 전달하면 → ValueError로 거절한다."""
    with pytest.raises(ValueError):
        completed_event(
            IDS,
            8,
            assistant_message=state.user_message,
            answer="합성 답변",
            evidence=(),
            finished_at=FINISHED_AT,
        )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            RequestFailure("AI_UNAVAILABLE"),
            {
                "code": "AI_UNAVAILABLE",
                "message": "답변을 생성하지 못했습니다.",
                "retryable": True,
                "accepted": True,
            },
        ),
        (
            RequestFailure("HEALTH_DATA_UNAVAILABLE", "DATA_NOT_READY", ("sleeps",)),
            {
                "code": "HEALTH_DATA_UNAVAILABLE",
                "message": "수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
                "retryable": True,
                "accepted": True,
                "reason": "DATA_NOT_READY",
                "metrics": ["sleeps"],
            },
        ),
        (
            RequestFailure("HEALTH_DATA_FORBIDDEN"),
            {
                "code": "HEALTH_DATA_FORBIDDEN",
                "message": "건강데이터 조회 권한을 확인해 주세요.",
                "retryable": False,
                "accepted": True,
            },
        ),
    ],
)
def test_accepted_error_public_failure_matches_exact_contract(
    failure: RequestFailure, expected: dict[str, Any]
) -> None:
    """접수 후 실패를 보내면 → 공개 메시지와 해당 코드의 필드만 포함한다."""
    event = accepted_error_event(IDS, 4, failure)
    assert event == {
        "type": "error",
        "clientRequestId": "11111111-1111-4111-8111-111111111111",
        "requestId": "33333333-3333-4333-8333-333333333333",
        "conversationId": "22222222-2222-4222-8222-222222222222",
        "eventSeq": 4,
        "data": expected,
    }
    assert json.loads(json.dumps(event)) == event


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("reason", ["DATA_NOT_READY", "LOOKUP_FAILED"])
@pytest.mark.parametrize(
    ("metrics", "not_ready_message"),
    [
        (("steps",), "걸음 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요."),
        (("heart_rates",), "심박 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요."),
        (
            ("oxygen_saturations",),
            "산소포화도 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        ),
        (("sleeps",), "수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요."),
        (("exercises",), "운동 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요."),
        ((), "건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요."),
        (
            ("sleeps", "steps"),
            "건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        ),
        (
            ("sleeps", "sleeps"),
            "수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        ),
        (
            ("sleeps", "steps", "sleeps"),
            "건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        ),
    ],
)
def test_health_failure_rest_and_ws_use_same_reason_message(
    metrics: tuple[PublicMetric, ...],
    not_ready_message: str,
    reason: HealthReason,
    accepted: bool,
) -> None:
    """REST와 WS는 사유·중복 제거한 지표 수에 맞는 문구를 쓰고 WS 문맥은 보존한다."""
    expected_message = (
        not_ready_message
        if reason == "DATA_NOT_READY"
        else "건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    error_type = (
        HealthDataNotReadyError if reason == "DATA_NOT_READY" else HealthDataLookupError
    )
    rest_error = health_api_error(error_type(metrics))
    failure = RequestFailure("HEALTH_DATA_UNAVAILABLE", reason, metrics)
    assert rest_error.message == expected_message
    assert failure.event_data(accepted=accepted) == {
        "code": "HEALTH_DATA_UNAVAILABLE",
        "message": expected_message,
        "retryable": True,
        "accepted": accepted,
        "reason": reason,
        "metrics": list(metrics),
    }
    assert failure.error_context() == {"reason": reason, "metrics": list(metrics)}


@pytest.mark.parametrize(
    ("code", "expected_code", "expected_reason", "expected_metrics"),
    [
        ("llm_unavailable", "AI_UNAVAILABLE", None, ()),
        ("llm_timeout", "AI_UNAVAILABLE", None, ()),
        ("rate_limited", "AI_UNAVAILABLE", None, ()),
        ("invalid_request", "AI_UNAVAILABLE", None, ()),
        ("user_not_linked", "CANDIY_USER_NOT_LINKED", None, ()),
        (
            "data_not_ready",
            "HEALTH_DATA_UNAVAILABLE",
            "DATA_NOT_READY",
            ("sleeps", "steps"),
        ),
        (
            "data_unavailable",
            "HEALTH_DATA_UNAVAILABLE",
            "LOOKUP_FAILED",
            ("sleeps", "steps"),
        ),
        ("forbidden", "HEALTH_DATA_FORBIDDEN", None, ()),
        ("internal", "INTERNAL_ERROR", None, ()),
        (None, "INTERNAL_ERROR", None, ()),
        ("unknown", "INTERNAL_ERROR", None, ()),
    ],
)
def test_coach_failure_internal_code_maps_to_public_contract(
    code: str | None,
    expected_code: str,
    expected_reason: HealthReason | None,
    expected_metrics: tuple[PublicMetric, ...],
) -> None:
    """내부 오류를 변환하면 → 대응표를 따르고 건강 지표만 순서대로 중복 제거한다."""
    failure = failure_from_coach_error(
        code, ["sleeps", "heart_rate", "sleeps", "steps"]
    )
    assert failure.code == expected_code
    assert failure.reason == expected_reason
    assert failure.metrics == expected_metrics


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
)
def test_coach_failure_public_metric_preserves_contract_value(metric: str) -> None:
    """허용된 공개 지표가 들어오면 → 철자 변경 없이 보존한다."""
    assert failure_from_coach_error("data_unavailable", [metric]).metrics == (metric,)


@pytest.mark.parametrize(
    ("code", "reason", "metrics"),
    [
        ("UNKNOWN", None, ()),
        ("HEALTH_DATA_UNAVAILABLE", None, ()),
        ("AI_UNAVAILABLE", "DATA_NOT_READY", ()),
        ("AI_UNAVAILABLE", None, ("sleeps",)),
        ("HEALTH_DATA_UNAVAILABLE", "UNKNOWN", ()),
        ("HEALTH_DATA_UNAVAILABLE", "LOOKUP_FAILED", ("heart_rate",)),
    ],
)
def test_request_failure_invalid_combination_raises_value_error(
    code: str, reason: Any, metrics: Any
) -> None:
    """오류 코드나 건강 문맥이 계약에 맞지 않으면 → ValueError로 거절한다."""
    with pytest.raises(ValueError):
        RequestFailure(code, reason, metrics)


def test_failure_context_health_failure_only_returns_context() -> None:
    """건강 실패와 일반 실패를 비교하면 → 건강 실패만 저장할 문맥을 반환한다."""
    health = RequestFailure("HEALTH_DATA_UNAVAILABLE", "LOOKUP_FAILED")
    assert health.error_context() == {"reason": "LOOKUP_FAILED", "metrics": []}
    general = RequestFailure("AI_UNAVAILABLE")
    assert general.error_context() is None
    assert general.event_data(accepted=False) == {
        "code": "AI_UNAVAILABLE",
        "message": "답변을 생성하지 못했습니다.",
        "retryable": True,
        "accepted": False,
    }


def test_status_event_unknown_stage_raises_value_error() -> None:
    """미지원 공개 stage를 전달하면 → 이벤트 생성을 거절한다."""
    with pytest.raises(ValueError):
        status_event(IDS, 2, "unknown")


def test_request_ids_stored_state_preserves_identifiers(state: RequestState) -> None:
    """저장된 요청에서 식별자를 만들면 → 세 UUID를 그대로 보존한다."""
    assert RequestIds.from_state(state) == IDS


async def test_sink_mixed_internal_events_sends_ordered_public_events() -> None:
    """내부 이벤트가 섞여 오면 → 공개 status와 delta만 빈틈 없는 번호로 보낸다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    sink.send_stage("reading_history")
    for event in [
        {"type": "status", "stage": "thinking"},
        {"type": "status", "stage": "tool_call"},
        {"type": "status", "stage": "tool_done"},
        {"type": "status", "stage": "thinking"},
        {"type": "status", "stage": "answering"},
        {"type": "token", "delta": "지난주"},
        {"type": "evidence", "items": [{"synthetic": True}]},
        {"type": "token", "delta": "에는"},
        {"type": "done"},
    ]:
        await sink.emit(event)
    assert [(e["type"], e["eventSeq"], e["data"]) for e in channel.events] == [
        ("status", 2, {"stage": "reading_history"}),
        ("status", 3, {"stage": "generating_answer"}),
        ("status", 4, {"stage": "fetching_health_data"}),
        ("status", 5, {"stage": "generating_answer"}),
        ("delta", 6, {"text": "지난주"}),
        ("delta", 7, {"text": "에는"}),
    ]
    assert sink.answer == "지난주에는"
    assert sink.next_event_seq == 8
    for event in channel.events:
        assert event["clientRequestId"] == "11111111-1111-4111-8111-111111111111"
        assert event["requestId"] == "33333333-3333-4333-8333-333333333333"
        assert event["conversationId"] == "22222222-2222-4222-8222-222222222222"
        assert json.loads(json.dumps(event)) == event


async def test_sink_duplicate_stage_skips_until_different_stage_sent() -> None:
    """같은 stage 사이에 delta만 있으면 → 생략하고 다른 stage 뒤 반복은 보낸다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    sink.send_stage("generating_answer")
    sink.send_stage("generating_answer")
    await sink.emit({"type": "token", "delta": "합성"})
    sink.send_stage("generating_answer")
    sink.send_stage("fetching_health_data")
    sink.send_stage("generating_answer")
    assert [(e["eventSeq"], e["data"]) for e in channel.events] == [
        (2, {"stage": "generating_answer"}),
        (3, {"text": "합성"}),
        (4, {"stage": "fetching_health_data"}),
        (5, {"stage": "generating_answer"}),
    ]
    assert sink.next_event_seq == 6


@pytest.mark.parametrize(
    ("code", "metrics", "expected"),
    [
        (
            "data_unavailable",
            ["sleeps", 7, None, "heart_rate"],
            ("sleeps", "heart_rate"),
        ),
        ("data_not_ready", ("steps", False), ("steps",)),
        (None, None, ()),
        (42, "sleeps", ()),
        ("internal", {"sleeps": True}, ()),
    ],
)
async def test_sink_internal_error_records_only_code_and_string_metrics(
    code: Any, metrics: Any, expected: tuple[str, ...]
) -> None:
    """내부 error가 오면 → 송신 없이 코드와 문자열 지표만 보관한다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    await sink.emit(
        {
            "type": "error",
            "code": code,
            "metrics": metrics,
            "message": "합성 내부 메시지",
            "retryable": False,
        }
    )
    assert sink.coach_error == (str(code), expected)
    assert channel.events == []
    assert sink.next_event_seq == 2


@pytest.mark.parametrize(
    "event",
    [
        {"type": "status"},
        {"type": "token", "delta": 42},
        {"type": "status", "stage": "unknown"},
        {"delta": "합성"},
        {"type": "token"},
        {"type": "token", "delta": ""},
        {"type": "status", "stage": []},
        {"type": []},
        {"type": "unknown"},
        None,
        [],
        "synthetic",
    ],
)
async def test_sink_malformed_event_ignores_without_raising(event: Any) -> None:
    """필드가 빠지거나 타입이 잘못되면 → 예외와 송신 없이 무시한다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    await sink.emit(event)
    assert channel.events == []
    assert sink.answer == ""
    assert sink.next_event_seq == 2
    assert sink.first_status_at is None
    assert sink.first_delta_at is None
    assert sink.coach_error is None


@pytest.mark.parametrize(
    "event",
    [
        {"type": "token", "delta": "synthetic-private-delta"},
        {"type": "status", "stage": "thinking"},
    ],
)
async def test_sink_channel_exception_logs_only_type_and_location(
    caplog: pytest.LogCaptureFixture, event: dict[str, Any]
) -> None:
    """채널 송신이 실패하면 → 예외를 전파하지 않고 종류와 예외가 난 곳(가짜 채널)의 위치만 기록한다."""
    channel = RecordingChannel()
    channel.error = RuntimeError("synthetic-private-exception")
    sink = PublicEventSink(channel, IDS)
    with caplog.at_level(logging.WARNING, logger="app.coaching.events"):
        await sink.emit(event)
    assert channel.events == []
    assert sink.next_event_seq == 2
    assert sink.first_status_at is None
    assert sink.first_delta_at is None
    assert sink.answer == event.get("delta", "")
    assert len(caplog.records) == 1
    assert re.fullmatch(
        r"coaching event sink failed: RuntimeError at test_coaching_events\.py:\d+",
        caplog.records[0].getMessage(),
    )
    assert caplog.records[0].name == "app.coaching.events"
    assert "synthetic-private-delta" not in caplog.text
    assert "synthetic-private-exception" not in caplog.text
    assert caplog.records[0].exc_info is None
    channel.error = None
    await sink.emit({"type": "token", "delta": "이어서"})
    assert channel.events[0]["eventSeq"] == 2
    assert sink.answer == event.get("delta", "") + "이어서"


@pytest.mark.parametrize("terminal", ["completed", "error"])
async def test_sink_terminal_event_ignores_every_later_event(
    state: RequestState, terminal: str
) -> None:
    """첫 종료 이벤트를 보내면 → 이후 stage·내부 이벤트·종료 호출을 무시한다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    sink.send_stage("reading_history")
    await sink.emit({"type": "token", "delta": "합성 부분 답변"})
    if terminal == "completed":
        sink.send_completed(
            state.assistant_message, "합성 최종 답변", FINISHED_AT, evidence=()
        )
    else:
        sink.send_error(RequestFailure("AI_UNAVAILABLE"))
    assert channel.events[-1]["type"] == terminal
    assert channel.events[-1]["eventSeq"] == 4
    if terminal == "completed":
        assert (
            channel.events[-1]["data"]["assistantMessage"]["content"]
            == "합성 최종 답변"
        )
    else:
        assert channel.events[-1]["data"] == {
            "code": "AI_UNAVAILABLE",
            "message": "답변을 생성하지 못했습니다.",
            "retryable": True,
            "accepted": True,
        }
    sink.send_stage("generating_answer")
    await sink.emit({"type": "token", "delta": "늦은 답변"})
    await sink.emit({"type": "error", "code": "internal"})
    sink.send_error(RequestFailure("INTERNAL_ERROR"))
    sink.send_completed(state.assistant_message, "늦은 완료", FINISHED_AT, evidence=())
    assert len(channel.events) == 3
    assert sink.answer == "합성 부분 답변"
    assert sink.coach_error is None
    assert sink.next_event_seq == 5


async def test_sink_injected_clock_preserves_first_status_and_delta_times() -> None:
    """여러 status와 delta를 보내면 → 최초 송신 시각만 주입한 시계 값으로 기록한다."""
    ticks = iter([0.0, 1.5, 9.0, 20.0])
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS, monotonic=ticks.__next__)
    assert sink.first_status_at is None
    assert sink.first_delta_at is None
    sink.send_stage("reading_history")
    sink.send_stage("reading_history")
    await sink.emit({"type": "token", "delta": "첫"})
    sink.send_stage("generating_answer")
    await sink.emit({"type": "token", "delta": "다음"})
    assert sink.first_status_at == 0.0
    assert sink.first_delta_at == 1.5


async def test_sink_separate_requests_send_only_to_own_channel() -> None:
    """두 연결의 sink를 번갈아 쓰면 → 생성자에 받은 연결에만 이벤트를 보낸다."""
    first_channel = RecordingChannel()
    second_channel = RecordingChannel()
    first = PublicEventSink(first_channel, IDS)
    second = PublicEventSink(
        second_channel,
        RequestIds(
            UUID("66666666-6666-4666-8666-666666666666"),
            UUID("77777777-7777-4777-8777-777777777777"),
            CONVERSATION_ID,
        ),
    )
    await first.emit({"type": "token", "delta": "첫 연결"})
    await second.emit({"type": "token", "delta": "둘째 연결"})
    assert len(first_channel.events) == len(second_channel.events) == 1
    assert first_channel.events[0]["data"] == {"text": "첫 연결"}
    assert second_channel.events[0]["data"] == {"text": "둘째 연결"}
    assert (
        first_channel.events[0]["requestId"] == "33333333-3333-4333-8333-333333333333"
    )
    assert (
        second_channel.events[0]["requestId"] == "77777777-7777-4777-8777-777777777777"
    )


async def test_sink_split_evidence_sends_cumulative_items_and_contiguous_sequence() -> (
    None
):
    """근거를 두 번 나눠 수신하면 → 두 번째에는 누적 전체와 연속 번호를 보낸다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    await sink.emit({"type": "evidence", "items": [synthetic_evidence().event_item()]})
    await sink.emit({"type": "token", "delta": "합성 답변"})
    await sink.emit({"type": "evidence", "items": [synthetic_evidence(2).event_item()]})
    first = {
        "id": "66666666-6666-4666-8666-666666666666",
        "position": 1,
        "metric": "steps",
        "periodStart": "2026-10-12",
        "periodEnd": "2026-10-18",
        "snapshot": SYNTHETIC_SNAPSHOT,
    }
    second = {
        "id": "77777777-7777-4777-8777-777777777777",
        "position": 2,
        "metric": "steps",
        "periodStart": "2026-10-12",
        "periodEnd": "2026-10-18",
        "snapshot": SYNTHETIC_SNAPSHOT,
    }
    assert [event["eventSeq"] for event in channel.events] == [2, 3, 4]
    assert channel.events[0]["data"] == {"items": [first]}
    assert channel.events[1]["data"] == {"text": "합성 답변"}
    assert channel.events[2]["data"] == {"items": [first, second]}


async def test_sink_invalid_evidence_keeps_accumulation_and_generation(
    state: RequestState,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """잠정 근거 형식 오류 → 번호·누적을 유지하고 뒤의 정상 근거·답변을 전송한다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    invalid = synthetic_evidence().event_item()
    invalid["snapshot"]["user_id"] = "88888888-8888-4888-8888-888888888888"
    with caplog.at_level(logging.WARNING, logger="app.coaching.events"):
        await sink.emit({"type": "evidence", "items": [invalid]})
    assert channel.events == []
    assert sink.next_event_seq == 2
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == "coaching evidence skipped=1 metrics=steps"
    assert "88888888-8888-4888-8888-888888888888" not in caplog.text
    assert "합성 요약 문장" not in caplog.text
    assert caplog.records[0].exc_info is None
    await sink.emit({"type": "evidence", "items": [synthetic_evidence().event_item()]})
    assert [item["position"] for item in channel.events[-1]["data"]["items"]] == [1]
    await sink.emit({"type": "evidence", "items": [invalid]})
    await sink.emit({"type": "evidence", "items": [synthetic_evidence(2).event_item()]})
    assert [item["position"] for item in channel.events[-1]["data"]["items"]] == [1, 2]
    await sink.emit({"type": "token", "delta": "계속 생성"})
    sink.send_completed(state.assistant_message, "계속 생성", FINISHED_AT, evidence=())
    assert [event["type"] for event in channel.events] == [
        "evidence",
        "evidence",
        "delta",
        "completed",
    ]
    assert [event["eventSeq"] for event in channel.events] == [2, 3, 4, 5]


async def test_sink_partial_evidence_sends_only_valid_items_and_logs_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """중간 근거 [정상 1, 틀림 2] → position 1만 eventSeq 2로 보내고 skipped=1 로그를 남긴다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    bad = synthetic_evidence(2).event_item()
    bad["snapshot"]["user_id"] = "88888888-8888-4888-8888-888888888888"
    with caplog.at_level(logging.WARNING, logger="app.coaching.events"):
        await sink.emit(
            {
                "type": "evidence",
                "items": [synthetic_evidence().event_item(), bad],
            }
        )
    assert len(channel.events) == 1
    assert channel.events[0]["eventSeq"] == 2
    assert [item["position"] for item in channel.events[0]["data"]["items"]] == [1]
    assert sink.next_event_seq == 3
    assert [record.getMessage() for record in caplog.records] == [
        "coaching evidence skipped=1 metrics=steps"
    ]
    assert "88888888-8888-4888-8888-888888888888" not in caplog.text
    assert "합성 요약 문장" not in caplog.text
    assert "66666666-6666-4666-8666-666666666666" not in caplog.text
    assert caplog.records[0].exc_info is None


async def test_sink_valid_then_mixed_evidence_accumulates_without_renumber() -> None:
    """중간 [정상 1] 뒤 [틀림 2, 정상 3] → 둘째 이벤트의 position은 [1, 3]이다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    bad = synthetic_evidence(2).event_item()
    bad["snapshot"]["user_id"] = "88888888-8888-4888-8888-888888888888"
    third = synthetic_evidence(3, id="55555555-5555-4555-8555-555555555555")
    await sink.emit({"type": "evidence", "items": [synthetic_evidence().event_item()]})
    await sink.emit({"type": "evidence", "items": [bad, third.event_item()]})
    assert [e["eventSeq"] for e in channel.events] == [2, 3]
    assert [item["position"] for item in channel.events[1]["data"]["items"]] == [1, 3]


@pytest.mark.parametrize("items", [None, [], (), "synthetic", {}])
async def test_sink_empty_or_nonlist_evidence_ignores_without_logging(
    items, caplog
) -> None:
    """빈 목록 또는 list가 아닌 근거 → 번호 소비·송신·로그 없이 무시한다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    await sink.emit({"type": "evidence", "items": items})
    assert channel.events == []
    assert sink.next_event_seq == 2
    assert caplog.records == []


@pytest.mark.parametrize("terminal", ["completed", "error"])
async def test_sink_evidence_after_terminal_ignores_all_items(
    state, terminal, caplog
) -> None:
    """종료 후 정상·잘못된 근거 수신 → 추가 송신·번호 소비·로그가 없다."""
    channel = RecordingChannel()
    sink = PublicEventSink(channel, IDS)
    if terminal == "completed":
        sink.send_completed(state.assistant_message, "완료", FINISHED_AT, evidence=())
    else:
        sink.send_error(RequestFailure("AI_UNAVAILABLE"))
    await sink.emit({"type": "evidence", "items": [synthetic_evidence().event_item()]})
    await sink.emit({"type": "evidence", "items": [{}]})
    assert len(channel.events) == 1
    assert sink.next_event_seq == 3
    assert caplog.records == []
