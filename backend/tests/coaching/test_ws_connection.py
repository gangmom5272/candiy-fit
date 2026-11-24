import asyncio
import json
import re
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event as ThreadEvent
from threading import Thread
from time import monotonic
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.testclient import WebSocketTestSession
from starlette.types import Message
from starlette.websockets import WebSocketDisconnect

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.auth.tokens import issue_access_token
from app.coaching.connection import EventChannel
from app.coaching.schemas import FrameError, Question, decode_frame, request_event
from app.core.config import Settings
from app.main import create_app

USER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CLIENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_CLIENT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
CONVERSATION_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
REQUEST_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
WS_PATH = "/api/v1/coaching/ws"
READY = {"type": "ready", "data": {"protocolVersion": "1.0"}}
RECEIVE_TIMEOUT_SECONDS = 5.0


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedUser, Question, EventChannel]] = []

    async def handle(
        self, user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None:
        self.calls.append((user, question, channel))
        channel.send(
            request_event(
                "delta",
                client_request_id=question.client_request_id,
                request_id=REQUEST_ID,
                conversation_id=question.conversation_id,
                event_seq=2,
                data={"text": "합성 응답"},
            )
        )


@pytest.fixture
def ws_app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def token(settings: Settings) -> str:
    return issue_access_token(USER_A, settings, now=datetime.now(UTC)).token


@pytest.fixture
def handler(ws_app: FastAPI) -> RecordingHandler:
    recording = RecordingHandler()
    ws_app.state.coaching_question_handler = recording
    return recording


def question_frame(**changes: Any) -> dict[str, Any]:
    return {
        "type": "question",
        "clientRequestId": CLIENT_ID,
        "conversationId": None,
        "content": "  합성 질문  ",
        **changes,
    }


@contextmanager
def bounded_test_client(
    app: FastAPI, enter_timeout: float = 2.0, exit_timeout: float = 2.0
) -> Iterator[TestClient]:
    """lifespan 진입·종료를 각각 기한 안에 마치고 초과하면 AssertionError로 실패한다."""
    entered: Future[TestClient] = Future()
    exited: Future[None] = Future()
    exit_requested = ThreadEvent()

    def run_lifespan() -> None:
        """같은 스레드에서 lifespan을 열고 닫으며 발생한 예외를 테스트로 전달한다."""
        try:
            with TestClient(app) as client:
                entered.set_result(client)
                exit_requested.wait()
        except BaseException as error:
            if not entered.done():
                entered.set_exception(error)
            exited.set_exception(error)
        else:
            exited.set_result(None)

    # lifespan이 멈춰도 테스트 프로세스 종료가 스레드 정리를 기다리지 않는다.
    Thread(target=run_lifespan, daemon=True).start()
    try:
        if wait((entered,), timeout=enter_timeout).not_done:
            raise AssertionError(f"{enter_timeout}초 내 lifespan 진입 대기 시간 초과")
        client = entered.result()
        try:
            yield client
        finally:
            exit_requested.set()
            if wait((exited,), timeout=exit_timeout).not_done:
                raise AssertionError(
                    f"{exit_timeout}초 내 lifespan 종료 대기 시간 초과"
                )
            exited.result()
    finally:
        # 진입이 늦게 끝나더라도 종료 요청을 받아 클라이언트를 정리하도록 한다.
        exit_requested.set()


def receive_within(
    websocket: WebSocketTestSession, timeout: float = RECEIVE_TIMEOUT_SECONDS
) -> Message:
    """기한 안에 다음 서버 메시지를 받는다. 넘기면 AssertionError로 실패한다."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(websocket.receive)
        return future.result(timeout=timeout)
    except TimeoutError as error:
        raise AssertionError(
            f"{timeout}초 내 서버 메시지 수신 대기 시간 초과"
        ) from error
    finally:
        # 연결 블록을 나가며 스트림이 닫히면 대기 중인 수신 스레드도 끝난다.
        executor.shutdown(wait=False, cancel_futures=True)


def receive_json_within(
    websocket: WebSocketTestSession, timeout: float = RECEIVE_TIMEOUT_SECONDS
) -> Any:
    message = receive_within(websocket, timeout)
    assert message["type"] == "websocket.send"
    return json.loads(message["text"])


def expect_close_within(websocket: WebSocketTestSession, code: int) -> None:
    message = receive_within(websocket)
    assert message["type"] == "websocket.close"
    assert message["code"] == code


def authenticate(websocket: WebSocketTestSession, token: str) -> None:
    websocket.send_json({"type": "auth", "accessToken": token})
    assert receive_json_within(websocket) == READY


def assert_auth_failure(websocket: WebSocketTestSession, code: str) -> None:
    assert receive_json_within(websocket) == {
        "type": "connection_error",
        "data": {"code": code, "message": "로그인이 필요합니다."},
    }
    expect_close_within(websocket, 1008)


def assert_pre_accept_error(
    websocket: WebSocketTestSession, code: str, client_id: str | None
) -> None:
    messages = {
        "INVALID_MESSAGE": "요청 내용을 확인해 주세요.",
        "INVALID_QUESTION": "질문 내용을 확인해 주세요.",
        "INTERNAL_ERROR": "요청을 처리하지 못했습니다.",
    }
    assert receive_json_within(websocket) == {
        "type": "error",
        "clientRequestId": client_id,
        "requestId": None,
        "conversationId": None,
        "eventSeq": 0,
        "data": {
            "code": code,
            "message": messages[code],
            "retryable": code == "INTERNAL_ERROR",
            "accepted": False,
        },
    }


@pytest.mark.parametrize("origin", [None, "http://localhost:5173"])
def test_auth_allowed_origin_sends_ready(
    ws_app: FastAPI, token: str, origin: str | None
) -> None:
    headers = {} if origin is None else {"Origin": origin}
    with TestClient(ws_app).websocket_connect(WS_PATH, headers=headers) as websocket:
        authenticate(websocket, token)


def test_auth_forbidden_origin_never_calls_verifier(
    ws_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = Mock()
    monkeypatch.setattr("app.coaching.router.authenticate_token", verifier)
    with (
        pytest.raises(WebSocketDisconnect) as error,
        TestClient(ws_app).websocket_connect(
            WS_PATH, headers={"Origin": "https://untrusted.example"}
        ),
    ):
        pytest.fail("An untrusted Origin must be rejected before accept")
    assert error.value.code == 1008
    verifier.assert_not_called()


def test_auth_malformed_token_closes(ws_app: FastAPI) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_json({"type": "auth", "accessToken": "synthetic-unknown"})
        assert_auth_failure(websocket, "AUTH_REQUIRED")


def test_auth_duplicate_access_token_closes(ws_app: FastAPI, token: str) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_text(
            f'{{"type":"auth","accessToken":"{token}","accessToken":"{token}"}}'
        )
        assert_auth_failure(websocket, "AUTH_REQUIRED")


def test_auth_token_signed_with_other_key_closes(
    ws_app: FastAPI, settings: Settings
) -> None:
    other_settings = settings.model_copy(
        update={
            "auth_signing_key": SecretStr(
                settings.require_auth_signing_key().decode("utf-8") + "-other"
            )
        }
    )
    token = issue_access_token(USER_A, other_settings, now=datetime.now(UTC)).token
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_json({"type": "auth", "accessToken": token})
        assert_auth_failure(websocket, "AUTH_REQUIRED")


def test_auth_expired_token_closes(ws_app: FastAPI, settings: Settings) -> None:
    token = issue_access_token(
        USER_A, settings, now=datetime.now(UTC) - timedelta(seconds=901)
    ).token
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_json({"type": "auth", "accessToken": token})
        assert_auth_failure(websocket, "AUTH_EXPIRED")


def test_auth_unknown_error_code_closes_as_required(
    ws_app: FastAPI, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = AuthenticationError("AUTH_REQUIRED")
    error.code = "UNEXPECTED_SYNTHETIC_CODE"
    monkeypatch.setattr(
        "app.coaching.router.authenticate_token", Mock(side_effect=error)
    )
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_json({"type": "auth", "accessToken": token})
        assert_auth_failure(websocket, "AUTH_REQUIRED")


@pytest.mark.parametrize(
    "frame",
    [
        json.dumps(question_frame()),
        "{",
        b"binary-auth",
        '{"type":"auth"}',
        '{"type":"auth","accessToken":""}',
        '{"type":"auth","accessToken":123}',
        '{"type":"auth","access_token":"synthetic-token-a"}',
        '{"type":"auth","type":"auth","accessToken":"synthetic-token-a"}',
        '{"type":"auth","accessToken":NaN}',
        '{"type":"auth","accessToken":"synthetic-token-a","userId":null}',
        "[]",
    ],
)
def test_auth_invalid_first_frame_closes(ws_app: FastAPI, frame: str | bytes) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        if isinstance(frame, bytes):
            websocket.send_bytes(frame)
        else:
            websocket.send_text(frame)
        assert_auth_failure(websocket, "AUTH_REQUIRED")


def test_auth_timeout_closes(ws_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.coaching.router.AUTH_TIMEOUT_SECONDS", 0.1)
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        assert_auth_failure(websocket, "AUTH_REQUIRED")


def test_disconnect_before_auth_never_calls_verifier(
    ws_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = Mock()
    monkeypatch.setattr("app.coaching.router.authenticate_token", verifier)
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.close()
    verifier.assert_not_called()


@pytest.mark.parametrize(
    "frame",
    [
        "{",
        '{"type":"question","type":"question"}',
        '{"nested":{"duplicate":1,"duplicate":2}}',
        '{"content":NaN}',
        '{"content":Infinity}',
        '{"content":-Infinity}',
        "[]",
        "null",
        b"binary-question",
    ],
)
def test_invalid_json_or_binary_keeps_connection(
    ws_app: FastAPI, handler: RecordingHandler, token: str, frame: str | bytes
) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        if isinstance(frame, bytes):
            websocket.send_bytes(frame)
        else:
            websocket.send_text(frame)
        assert_pre_accept_error(websocket, "INVALID_MESSAGE", None)
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
        assert len(handler.calls) == 1


def test_question_duplicate_content_never_calls_handler(
    ws_app: FastAPI, handler: RecordingHandler, token: str
) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_text(
            '{"type":"question",'
            f'"clientRequestId":"{CLIENT_ID}",'
            '"conversationId":null,"content":"a","content":"b"}'
        )
        assert_pre_accept_error(websocket, "INVALID_MESSAGE", None)
    assert handler.calls == []


@pytest.mark.parametrize(
    "fields",
    [
        r'"content":"\ud800"',
        r'"content":"\udfff"',
        r'"content":"abc\ud800def"',
        r'"content":"a","\ud800":1',
        '"content":"a","extra":1e999',
    ],
)
def test_question_invalid_unicode_or_number_never_calls_handler(
    ws_app: FastAPI, handler: RecordingHandler, token: str, fields: str
) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_text(
            '{"type":"question",'
            f'"clientRequestId":"{CLIENT_ID}","conversationId":null,' + fields + "}"
        )
        assert handler.calls == []
        assert_pre_accept_error(websocket, "INVALID_MESSAGE", None)
        assert handler.calls == []
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
        assert len(handler.calls) == 1


def test_question_surrogate_pair_becomes_one_emoji(
    ws_app: FastAPI, handler: RecordingHandler, token: str
) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_text(
            '{"type":"question",'
            f'"clientRequestId":"{CLIENT_ID}","conversationId":null,'
            r'"content":"\ud83d\ude00"}'
        )
        assert receive_json_within(websocket)["type"] == "delta"
        assert len(handler.calls) == 1
        content = handler.calls[0][1].content
        assert content == "😀"
        assert len(content) == 1


@pytest.mark.parametrize("access_token", [r"\ud800", r"\udfff"])
def test_auth_lone_surrogate_closes_without_verification(
    ws_app: FastAPI, monkeypatch: pytest.MonkeyPatch, access_token: str
) -> None:
    verifier = Mock()
    monkeypatch.setattr("app.coaching.router.authenticate_token", verifier)
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_text(f'{{"type":"auth","accessToken":"{access_token}"}}')
        verifier.assert_not_called()
        assert_auth_failure(websocket, "AUTH_REQUIRED")
    verifier.assert_not_called()


def test_decode_frame_rejects_unescaped_surrogate() -> None:
    with pytest.raises(FrameError) as error:
        decode_frame('{"content":"\ud800"}')
    assert error.value.code == "INVALID_MESSAGE"
    assert error.value.client_request_id is None
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("type_", [None, "unknown", "auth", [], 123])
@pytest.mark.parametrize("client_id", [CLIENT_ID, "invalid-id", None])
def test_invalid_type_keeps_connection_and_valid_client_id(
    ws_app: FastAPI,
    handler: RecordingHandler,
    token: str,
    type_: Any,
    client_id: str | None,
) -> None:
    frame: dict[str, Any] = {"clientRequestId": client_id}
    if type_ is not None:
        frame["type"] = type_
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(frame)
        assert_pre_accept_error(
            websocket, "INVALID_MESSAGE", CLIENT_ID if client_id == CLIENT_ID else None
        )
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
        assert len(handler.calls) == 1


@pytest.mark.parametrize(
    ("changes", "missing", "expected_id"),
    [
        ({"userId": str(USER_B)}, None, CLIENT_ID),
        ({"client_request_id": CLIENT_ID}, "clientRequestId", None),
        ({"client_request_id": CLIENT_ID}, None, CLIENT_ID),
        ({}, "conversationId", CLIENT_ID),
        ({"conversation_id": None}, "conversationId", CLIENT_ID),
        ({}, "clientRequestId", None),
        ({}, "content", CLIENT_ID),
        ({"clientRequestId": CLIENT_ID.upper()}, None, None),
        ({"clientRequestId": CLIENT_ID.replace("-", "")}, None, None),
        ({"clientRequestId": CLIENT_ID + "\n"}, None, None),
        ({"clientRequestId": 123}, None, None),
        ({"conversationId": CONVERSATION_ID.upper()}, None, CLIENT_ID),
        ({"conversationId": CONVERSATION_ID.replace("-", "")}, None, CLIENT_ID),
        ({"content": 123}, None, CLIENT_ID),
        ({"content": None}, None, CLIENT_ID),
        ({"userId": str(USER_B), "content": " "}, None, CLIENT_ID),
        ({"content": "x" * 2001}, "conversationId", CLIENT_ID),
    ],
)
def test_question_structure_errors_precede_content_errors(
    ws_app: FastAPI,
    handler: RecordingHandler,
    token: str,
    changes: dict[str, Any],
    missing: str | None,
    expected_id: str | None,
) -> None:
    frame = question_frame(**changes)
    if missing is not None:
        del frame[missing]
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(frame)
        assert_pre_accept_error(websocket, "INVALID_MESSAGE", expected_id)
        assert handler.calls == []


@pytest.mark.parametrize("content", ["", " \t\n\u3000 ", "x" * 2001, "😀" * 2001])
def test_question_invalid_content(
    ws_app: FastAPI, handler: RecordingHandler, token: str, content: str
) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(question_frame(content=content))
        assert_pre_accept_error(websocket, "INVALID_QUESTION", CLIENT_ID)
        assert handler.calls == []


@pytest.mark.parametrize(
    "content",
    ["x" * 2000, " " + "x" * 1998 + " ", " \n" + "x" * 2000 + "\t ", "😀" * 2000],
)
def test_question_content_length_uses_trimmed_codepoints(
    ws_app: FastAPI, handler: RecordingHandler, token: str, content: str
) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(question_frame(content=content))
        assert receive_json_within(websocket)["type"] == "delta"
        assert handler.calls[0][1].content == content.strip()


@pytest.mark.parametrize("user_id", [USER_A, USER_B])
@pytest.mark.parametrize("conversation_id", [None, CONVERSATION_ID])
def test_question_passes_authenticated_user_and_normalized_question(
    ws_app: FastAPI,
    handler: RecordingHandler,
    settings: Settings,
    user_id: UUID,
    conversation_id: str | None,
) -> None:
    token = issue_access_token(user_id, settings, now=datetime.now(UTC)).token
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(question_frame(conversationId=conversation_id))
        event = receive_json_within(websocket)
        user, question, _ = handler.calls[0]
        assert user.user_id == user_id
        assert question.client_request_id == UUID(CLIENT_ID)
        assert question.conversation_id == (
            None if conversation_id is None else UUID(conversation_id)
        )
        assert question.content == "합성 질문"
        assert event == {
            "type": "delta",
            "clientRequestId": CLIENT_ID,
            "requestId": str(REQUEST_ID),
            "conversationId": conversation_id,
            "eventSeq": 2,
            "data": {"text": "합성 응답"},
        }


def test_events_are_isolated_to_requesting_connection(
    ws_app: FastAPI, handler: RecordingHandler, settings: Settings, token: str
) -> None:
    token_b = issue_access_token(USER_B, settings, now=datetime.now(UTC)).token
    client = TestClient(ws_app)
    with client.websocket_connect(WS_PATH) as connection_a:
        authenticate(connection_a, token)
        connection_a.send_json(question_frame())
        assert receive_json_within(connection_a)["clientRequestId"] == CLIENT_ID
        with client.websocket_connect(WS_PATH) as connection_b:
            authenticate(connection_b, token_b)
            connection_b.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
        assert len(handler.calls) == 2
        assert handler.calls[0][2] is not handler.calls[1][2]

    with (
        client.websocket_connect(WS_PATH) as connection_a,
        client.websocket_connect(WS_PATH) as connection_b,
    ):
        authenticate(connection_a, token)
        authenticate(connection_b, token_b)
        connection_a.send_json(question_frame())
        assert receive_json_within(connection_a)["clientRequestId"] == CLIENT_ID
        connection_b.send_text("{")
        assert_pre_accept_error(connection_b, "INVALID_MESSAGE", None)
        connection_b.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
        assert receive_json_within(connection_b)["clientRequestId"] == OTHER_CLIENT_ID
        connection_a.send_text("{")
        assert_pre_accept_error(connection_a, "INVALID_MESSAGE", None)
        connection_a.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
        assert receive_json_within(connection_a)["clientRequestId"] == OTHER_CLIENT_ID


def test_missing_handler_returns_pre_accept_error(ws_app: FastAPI, token: str) -> None:
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(question_frame())
        assert_pre_accept_error(websocket, "INTERNAL_ERROR", CLIENT_ID)


def test_handler_failure_hides_input_and_keeps_connection(
    ws_app: FastAPI,
    handler: RecordingHandler,
    token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail_handle(
        user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None:
        raise RuntimeError("SECRET-SYNTHETIC-DETAIL")

    failing_handler = AsyncMock()
    failing_handler.handle.side_effect = fail_handle
    ws_app.state.coaching_question_handler = failing_handler
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(question_frame(content="synthetic-private-question"))
        assert_pre_accept_error(websocket, "INTERNAL_ERROR", CLIENT_ID)
        ws_app.state.coaching_question_handler = handler
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
    assert re.search(
        r"coaching handler failed: RuntimeError at test_ws_connection\.py:\d+",
        caplog.text,
    )
    assert "SECRET-SYNTHETIC-DETAIL" not in caplog.text
    assert "synthetic-private-question" not in caplog.text
    assert token not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("code", ["AUTH_EXPIRED", "AUTH_REQUIRED"])
def test_question_reverification_failure_never_calls_handler(
    ws_app: FastAPI,
    handler: RecordingHandler,
    settings: Settings,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    code: Literal["AUTH_EXPIRED", "AUTH_REQUIRED"],
) -> None:
    verifier = Mock(side_effect=[AuthenticatedUser(USER_A), AuthenticationError(code)])
    monkeypatch.setattr("app.coaching.router.authenticate_token", verifier)
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_json(question_frame())
        assert_auth_failure(websocket, code)
    assert handler.calls == []
    assert verifier.call_args_list == [call(token, settings), call(token, settings)]


def test_reverification_flushes_previous_errors_before_connection_error(
    ws_app: FastAPI,
    handler: RecordingHandler,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = Mock(
        side_effect=[AuthenticatedUser(USER_A), AuthenticationError("AUTH_EXPIRED")]
    )
    monkeypatch.setattr("app.coaching.router.authenticate_token", verifier)
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        authenticate(websocket, token)
        websocket.send_text("{")
        websocket.send_json(question_frame())
        assert_pre_accept_error(websocket, "INVALID_MESSAGE", None)
        assert_auth_failure(websocket, "AUTH_EXPIRED")
    assert handler.calls == []


@pytest.mark.parametrize("failure", ["overflow", "send_failed"])
def test_send_failure_closes_with_next_question_already_buffered(
    ws_app: FastAPI,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["overflow", "send_failed"],
) -> None:
    """다음 질문이 수신 큐에 쌓인 상태에서 송신 실패 → 연결을 1011로 닫는다."""
    monkeypatch.setattr("app.coaching.connection.MAX_PENDING_EVENTS", 2)
    questions_sent = asyncio.Event()

    async def fail_handle(
        user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None:
        if question.client_request_id != UUID(CLIENT_ID):
            return
        # 두 번째 프레임이 수신 큐에 들어간 뒤 첫 질문의 송신을 실패시킨다.
        await questions_sent.wait()
        if failure == "overflow":
            for _ in range(3):
                channel.send({"type": "delta", "data": {"text": "synthetic"}})
        else:
            channel.send({"type": "delta", "unserializable": object()})

    failing_handler = AsyncMock()
    failing_handler.handle.side_effect = fail_handle

    with bounded_test_client(ws_app) as client:
        # lifespan이 실제 처리기를 등록한 뒤 가짜 처리기로 바꾼다.
        ws_app.state.coaching_question_handler = failing_handler
        with client.websocket_connect(WS_PATH) as websocket:
            try:
                authenticate(websocket, token)
                websocket.send_json(question_frame())
                websocket.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
                client.portal.call(questions_sent.set)
                deadline = monotonic() + RECEIVE_TIMEOUT_SECONDS
                while True:
                    remaining = deadline - monotonic()
                    assert remaining > 0, "Timed out waiting for close 1011"
                    message = receive_within(websocket, timeout=remaining)
                    if message["type"] == "websocket.close":
                        assert message["code"] == 1011
                        break
            finally:
                client.portal.call(questions_sent.set)

        if failure == "overflow":
            failing_handler.handle.assert_awaited_once()
        # 송신 실패는 송신 태스크에서 나중에 감지하므로 이미 받은 두 번째
        # 질문이 처리기에 전달될 수 있다. 이 경로의 호출 횟수는 단언하지 않는다.


@pytest.mark.parametrize("failure", ["overflow", "send_failed"])
def test_send_failure_closes_socket_and_preserves_accepted_work(
    ws_app: FastAPI,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Literal["overflow", "send_failed"],
) -> None:
    """수락된 작업이 진행 중일 때 송신 실패 → 연결을 1011로 닫고 작업은 보존한다."""
    monkeypatch.setattr("app.coaching.connection.MAX_PENDING_EVENTS", 2)
    accepted = ThreadEvent()
    trigger_failure = asyncio.Event()
    release_work = asyncio.Event()
    tasks: list[asyncio.Task[str]] = []
    channels: list[EventChannel] = []

    async def store_result() -> str:
        await release_work.wait()
        return "synthetic-stored-result"

    async def fail_handle(
        user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None:
        channels.append(channel)
        tasks.append(asyncio.create_task(store_result()))
        accepted.set()
        await trigger_failure.wait()
        event = {"type": "delta", "data": {"text": "SECRET-SYNTHETIC-EVENT"}}
        if failure == "overflow":
            for _ in range(3):
                channel.send(event)
        else:
            channel.send({**event, "unserializable": object()})

    failing_handler = AsyncMock()
    failing_handler.handle.side_effect = fail_handle

    async def finish_work() -> str:
        return await asyncio.wait_for(tasks[0], timeout=1.0)

    with bounded_test_client(ws_app) as client:
        # lifespan이 실제 처리기를 등록한 뒤 가짜 처리기로 바꾼다.
        ws_app.state.coaching_question_handler = failing_handler
        with client.websocket_connect(WS_PATH) as websocket:
            try:
                authenticate(websocket, token)
                websocket.send_json(
                    question_frame(content="synthetic-private-question")
                )
                assert accepted.wait(timeout=1.0)
                failing_handler.handle.assert_awaited_once()
                client.portal.call(trigger_failure.set)
                expect_close_within(websocket, 1011)
                assert not channels[0].is_open
                assert channels[0].failure == failure
                websocket.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
            finally:
                client.portal.call(trigger_failure.set)

        try:
            failing_handler.handle.assert_awaited_once()
            assert len(tasks) == 1
            assert not client.portal.call(tasks[0].cancelled)
            assert not client.portal.call(tasks[0].done)
        finally:
            client.portal.call(release_work.set)
        assert client.portal.call(finish_work) == "synthetic-stored-result"

    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.coaching.connection"
    ] == [f"coaching channel closed: {failure}"]
    assert "SECRET-SYNTHETIC-EVENT" not in caplog.text
    assert "synthetic-private-question" not in caplog.text
    assert token not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_channel_send_failure_closes_without_leaking_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        websocket = AsyncMock()
        websocket.send_text.side_effect = RuntimeError("synthetic-private-event")
        channel = EventChannel(websocket)
        channel.start()
        channel.send({"type": "delta"})
        await channel.flush(1.0)
        assert not channel.is_open
        assert channel.failure == "send_failed"
        channel.send({"type": "ignored"})
        await channel.aclose()
        await channel.aclose()
        assert channel.failure == "send_failed"
        websocket.send_text.assert_awaited_once()
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())
    assert [record.getMessage() for record in caplog.records] == [
        "coaching channel closed: send_failed"
    ]
    assert "synthetic-private-event" not in caplog.text


def test_channel_queue_overflow_closes_and_drops_later_events() -> None:
    async def scenario() -> None:
        websocket = AsyncMock()
        closed = asyncio.Event()
        websocket.close.side_effect = lambda **kwargs: closed.set()
        channel = EventChannel(websocket, max_pending=1)
        channel.start()
        channel.send({"type": "first"})
        assert channel.is_open
        assert channel.failure is None
        channel.send({"type": "overflow"})
        assert not channel.is_open
        assert channel.failure == "overflow"
        await asyncio.wait_for(closed.wait(), timeout=1.0)
        channel.send({"type": "ignored"})
        await channel.aclose()
        await channel.aclose()
        assert channel.failure == "overflow"
        websocket.send_text.assert_not_awaited()
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())


def test_channel_serialization_failure_discards_pending_events() -> None:
    async def scenario() -> None:
        websocket = AsyncMock()
        channel = EventChannel(websocket)
        channel.start()
        channel.send({"unserializable": object()})
        channel.send({"type": "discarded"})
        await channel.flush(1.0)
        assert not channel.is_open
        assert channel.failure == "send_failed"
        await channel.aclose()
        websocket.send_text.assert_not_awaited()
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())


def test_channel_normal_close_does_not_close_socket_or_signal_failure() -> None:
    async def scenario() -> None:
        websocket = AsyncMock()
        channel = EventChannel(websocket)
        channel.start()
        await asyncio.sleep(0)
        assert channel.failure is None
        await channel.aclose()
        await channel.aclose()
        assert not channel.is_open
        assert channel.failure is None
        websocket.close.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["overflow", "send_failed"])
@pytest.mark.parametrize("close_error", [WebSocketDisconnect, RuntimeError, OSError])
def test_channel_failure_ignores_socket_close_errors(
    failure: Literal["overflow", "send_failed"], close_error: type[Exception]
) -> None:
    async def scenario() -> None:
        closed = asyncio.Event()

        async def fail_close(*, code: int) -> None:
            closed.set()
            raise close_error()

        websocket = AsyncMock()
        websocket.close.side_effect = fail_close
        channel = EventChannel(websocket, max_pending=1)
        channel.start()
        channel.send({"unserializable": object()})
        if failure == "overflow":
            channel.send({"type": "overflow"})
        await asyncio.wait_for(closed.wait(), timeout=1.0)
        await channel.aclose()
        assert not channel.is_open
        assert channel.failure == failure
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["overflow", "send_failed"])
def test_channel_aclose_waits_for_failure_close_to_finish(
    failure: Literal["overflow", "send_failed"],
) -> None:
    async def scenario() -> None:
        close_completed = asyncio.Event()

        async def delayed_close(*, code: int) -> None:
            await asyncio.sleep(0.05)
            close_completed.set()

        websocket = AsyncMock()
        websocket.close.side_effect = delayed_close
        channel = EventChannel(websocket, max_pending=1)
        channel.start()
        channel.send({"unserializable": object()})
        if failure == "overflow":
            channel.send({"type": "overflow"})
        else:
            await channel.flush(1.0)
        assert channel.failure == failure
        await channel.aclose()
        assert close_completed.is_set()
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["overflow", "send_failed"])
def test_channel_aclose_cleans_up_pending_socket_close(
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["overflow", "send_failed"],
) -> None:
    async def scenario() -> None:
        close_started = asyncio.Event()
        close_finished = asyncio.Event()
        close_cancelled = asyncio.Event()
        release_close = asyncio.Event()

        async def blocked_close(*, code: int) -> None:
            close_started.set()
            try:
                await release_close.wait()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise
            finally:
                close_finished.set()

        websocket = AsyncMock()
        websocket.close.side_effect = blocked_close
        channel = EventChannel(websocket, max_pending=1)
        channel.start()
        channel.send({"unserializable": object()})
        if failure == "overflow":
            channel.send({"type": "overflow"})
        await asyncio.wait_for(close_started.wait(), timeout=1.0)
        assert not close_finished.is_set()
        # 실패 시점이 아닌 aclose 호출 시점의 상수를 사용해야 한다.
        monkeypatch.setattr("app.coaching.connection.CLOSE_WAIT_SECONDS", 0.1)
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(channel.aclose(), timeout=1.0)
        # Windows 타이머 해상도 오차는 허용하되 즉시 취소되지는 않아야 한다.
        assert asyncio.get_running_loop().time() - started >= 0.08
        await channel.aclose()
        assert close_finished.is_set()
        assert close_cancelled.is_set()
        assert not channel.is_open
        assert channel.failure == failure
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())


def test_channel_overflow_and_inflight_send_failure_close_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        send_started = asyncio.Event()
        send_failed = asyncio.Event()
        release_send = asyncio.Event()

        async def fail_cancelled_send(text: str) -> None:
            send_started.set()
            try:
                await release_send.wait()
            except asyncio.CancelledError:
                send_failed.set()
                raise RuntimeError("synthetic send failure") from None

        websocket = AsyncMock()
        websocket.send_text.side_effect = fail_cancelled_send
        channel = EventChannel(websocket, max_pending=1)
        channel.start()
        channel.send({"type": "inflight"})
        await asyncio.wait_for(send_started.wait(), timeout=1.0)
        channel.send({"type": "pending"})
        channel.send({"type": "overflow"})
        await channel.aclose()
        assert send_failed.is_set()
        assert not channel.is_open
        assert channel.failure == "overflow"
        websocket.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())
    assert [record.getMessage() for record in caplog.records] == [
        "coaching channel closed: overflow"
    ]


@pytest.mark.parametrize("waiting_for_sender", [False, True])
def test_channel_aclose_propagates_cancellation_and_cleans_up_close(
    waiting_for_sender: bool,
) -> None:
    async def scenario() -> None:
        send_started = asyncio.Event()
        sender_stopping = asyncio.Event()
        sender_finished = asyncio.Event()
        release_sender = asyncio.Event()
        close_started = asyncio.Event()
        close_finished = asyncio.Event()
        release_close = asyncio.Event()
        if not waiting_for_sender:
            release_sender.set()

        async def blocked_send(text: str) -> None:
            send_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sender_stopping.set()
                try:
                    await release_sender.wait()
                finally:
                    sender_finished.set()

        async def blocked_close(*, code: int) -> None:
            close_started.set()
            try:
                await release_close.wait()
            finally:
                close_finished.set()

        websocket = AsyncMock()
        websocket.send_text.side_effect = blocked_send
        websocket.close.side_effect = blocked_close
        channel = EventChannel(websocket, max_pending=1)
        channel.start()
        channel.send({"type": "inflight"})
        await asyncio.wait_for(send_started.wait(), timeout=1.0)

        async def fail_and_close() -> None:
            channel.send({"type": "pending"})
            channel.send({"type": "overflow"})
            await channel.aclose()

        closing = asyncio.create_task(fail_and_close())
        try:
            await asyncio.wait_for(sender_stopping.wait(), timeout=1.0)
            await asyncio.wait_for(close_started.wait(), timeout=1.0)
            assert sender_finished.is_set() is not waiting_for_sender
            assert not close_finished.is_set()
            assert not closing.done()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(closing, timeout=1.0)
            assert sender_finished.is_set()
            assert close_finished.is_set()
            assert not channel.is_open
            websocket.close.assert_awaited_once_with(code=1011)
        finally:
            release_sender.set()
            release_close.set()
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
            await channel.aclose()

    asyncio.run(scenario())


def test_channel_flush_preserves_fifo_and_unicode() -> None:
    async def scenario() -> None:
        websocket = AsyncMock()
        channel = EventChannel(websocket)
        channel.start()
        channel.start()
        channel.send({"text": "합성 첫 이벤트"})
        channel.send({"text": "합성 두 번째 이벤트"})
        await channel.flush(1.0)
        assert [call.args[0] for call in websocket.send_text.await_args_list] == [
            '{"text": "합성 첫 이벤트"}',
            '{"text": "합성 두 번째 이벤트"}',
        ]
        await channel.aclose()
        assert not channel.is_open
        channel.send({"type": "ignored"})
        channel.start()
        await channel.aclose()
        assert websocket.send_text.await_count == 2

    asyncio.run(scenario())


def test_channel_flush_timeout_and_close_cancel_only_sender() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_send(text: str) -> None:
            started.set()
            await release.wait()

        websocket = AsyncMock()
        websocket.send_text.side_effect = blocked_send
        channel = EventChannel(websocket)
        channel.start()
        handler_task = asyncio.create_task(release.wait())
        channel.send({"type": "pending"})
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await channel.flush(0.01)
        assert channel.is_open
        await channel.aclose()
        assert not channel.is_open
        assert not handler_task.done()
        release.set()
        await handler_task

    asyncio.run(scenario())
