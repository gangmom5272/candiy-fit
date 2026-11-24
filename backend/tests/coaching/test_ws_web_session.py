"""코칭 WS의 웹 세션 쿠키 인증 경로. 쿠키 어댑터는 합성 대역으로 바꾼다."""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.coaching.connection import EventChannel
from app.coaching.schemas import PUBLIC_ERRORS, Question, request_event
from app.coaching.ws_auth import WebSessionUnavailableError, authenticate_web_cookie
from app.core.errors import TemporaryStorageError
from tests.coaching.test_ws_connection import (
    CLIENT_ID,
    OTHER_CLIENT_ID,
    READY,
    REQUEST_ID,
    USER_A,
    USER_B,
    WS_PATH,
    RecordingHandler,
    expect_close_within,
    question_frame,
    receive_json_within,
)
from tests.coaching.test_ws_connection import (
    handler as handler,
)
from tests.coaching.test_ws_connection import (
    token as token,
)
from tests.coaching.test_ws_connection import (
    ws_app as ws_app,
)

ADAPTER = "app.coaching.router.authenticate_web_cookie"
ORIGIN = "http://localhost:5173"
COOKIE = "__Host-cf_session=synthetic-cookie"
HEADERS = {"Cookie": COOKIE, "Origin": ORIGIN}
MESSAGES = {
    "AUTH_REQUIRED": "로그인이 필요합니다.",
    "AUTH_UNAVAILABLE": "인증 처리를 일시적으로 이용할 수 없습니다.",
    "INVALID_MESSAGE": "요청 내용을 확인해 주세요.",
    "INTERNAL_ERROR": "요청을 처리하지 못했습니다.",
}


class DuplicateOriginHeaders(dict[str, str]):
    """items()에서 Origin을 두 번 내보내 중복 헤더를 흉내 내는 합성 헤더 딕셔너리."""

    def items(self) -> Any:  # type: ignore[override]
        """기존 항목에 Origin을 한 번 더 붙여 돌려준다."""
        return [*super().items(), ("Origin", ORIGIN)]


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """웹 쿠키 어댑터를 USER_A를 돌려주는 AsyncMock으로 바꾼다."""
    mock = AsyncMock(return_value=AuthenticatedUser(USER_A))
    monkeypatch.setattr(ADAPTER, mock)
    return mock


def connect(app: FastAPI, headers: dict[str, str] | None = None) -> Any:
    """쿠키와 허용 Origin을 실어 WS 연결 컨텍스트를 만든다."""
    return TestClient(app).websocket_connect(
        WS_PATH, headers=HEADERS if headers is None else headers
    )


def assert_error(
    websocket: WebSocketTestSession, code: str, client_id: str | None = CLIENT_ID
) -> None:
    """접수 전 error 이벤트의 코드·메시지·retryable·accepted를 확인한다."""
    assert receive_json_within(websocket) == {
        "type": "error",
        "clientRequestId": client_id,
        "requestId": None,
        "conversationId": None,
        "eventSeq": 0,
        "data": {
            "code": code,
            "message": MESSAGES[code],
            "retryable": code != "INVALID_MESSAGE",
            "accepted": False,
        },
    }


def assert_connection_error(
    websocket: WebSocketTestSession, code: str, close_code: int
) -> None:
    """connection_error 이벤트 뒤 지정한 close 코드로 닫히는지 확인한다."""
    assert receive_json_within(websocket) == {
        "type": "connection_error",
        "data": {"code": code, "message": MESSAGES[code]},
    }
    expect_close_within(websocket, close_code)


def test_web_cookie_sends_ready_without_auth_frame(
    ws_app: FastAPI, adapter: AsyncMock
) -> None:
    """쿠키와 허용 Origin이 있고 검증 성공 → auth 프레임 없이 ready, 어댑터 1회 호출."""
    with connect(ws_app) as websocket:
        assert receive_json_within(websocket) == READY
    adapter.assert_awaited_once()


def test_web_cookie_without_origin_closes_before_accept(
    ws_app: FastAPI, adapter: AsyncMock
) -> None:
    """쿠키는 있는데 Origin이 없으면 → accept 전 close 1008, 어댑터 호출 없음."""
    with (
        pytest.raises(WebSocketDisconnect) as error,
        connect(ws_app, {"Cookie": COOKIE}),
    ):
        pytest.fail("Origin 없는 웹 연결은 accept 전에 거절돼야 한다")
    assert error.value.code == 1008
    adapter.assert_not_awaited()


@pytest.mark.parametrize("origin", ["null", "https://untrusted.example"])
def test_web_cookie_with_bad_origin_closes_before_accept(
    ws_app: FastAPI, adapter: AsyncMock, origin: str
) -> None:
    """쿠키는 있는데 Origin이 null이거나 불허이면 → accept 전 close 1008, 어댑터 호출 없음."""
    with (
        pytest.raises(WebSocketDisconnect) as error,
        connect(ws_app, {"Cookie": COOKIE, "Origin": origin}),
    ):
        pytest.fail("불허 Origin은 accept 전에 거절돼야 한다")
    assert error.value.code == 1008
    adapter.assert_not_awaited()


def test_web_cookie_with_duplicate_origin_closes_before_accept(
    ws_app: FastAPI, adapter: AsyncMock
) -> None:
    """쿠키는 있는데 허용 Origin이 둘 중복되면 → accept 전 close 1008, 어댑터 호출 없음."""
    with (
        pytest.raises(WebSocketDisconnect) as error,
        TestClient(ws_app).websocket_connect(
            WS_PATH,
            headers=DuplicateOriginHeaders(HEADERS),
        ),
    ):
        pytest.fail("중복 Origin은 accept 전에 거절돼야 한다")
    assert error.value.code == 1008
    adapter.assert_not_awaited()


@pytest.mark.parametrize("cookie", [COOKIE, "__Host-cf_session="])
def test_web_cookie_rejected_sends_auth_required_and_closes(
    ws_app: FastAPI,
    adapter: AsyncMock,
    handler: RecordingHandler,
    cookie: str,
) -> None:
    """쿠키(빈 값 포함)를 어댑터가 거절 → AUTH_REQUIRED 후 close 1008, ready·처리기 없음."""
    adapter.side_effect = AuthenticationError("AUTH_REQUIRED")
    with connect(ws_app, {"Cookie": cookie, "Origin": ORIGIN}) as websocket:
        assert_connection_error(websocket, "AUTH_REQUIRED", 1008)
    assert handler.calls == []


def test_web_cookie_storage_unavailable_closes_1011(
    ws_app: FastAPI, adapter: AsyncMock, handler: RecordingHandler
) -> None:
    """어댑터가 일시 장애 → AUTH_UNAVAILABLE 후 close 1011, ready·처리기 없음."""
    adapter.side_effect = WebSessionUnavailableError()
    with connect(ws_app) as websocket:
        assert_connection_error(websocket, "AUTH_UNAVAILABLE", 1011)
    assert handler.calls == []


def test_web_cookie_unexpected_error_closes_1011_without_cookie_in_log(
    ws_app: FastAPI,
    adapter: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """어댑터가 예상 밖 예외 → 1011로 닫고 로그에 종류·위치만 남으며 쿠키 값은 없다."""
    adapter.side_effect = RuntimeError("synthetic")
    with (
        caplog.at_level(logging.WARNING, logger="app.coaching.connection"),
        connect(ws_app) as websocket,
    ):
        assert_connection_error(websocket, "AUTH_UNAVAILABLE", 1011)
    assert "web auth failed: RuntimeError" in caplog.text
    assert "synthetic-cookie" not in caplog.text


def test_web_cookie_auth_frame_after_ready_is_invalid_message(
    ws_app: FastAPI, adapter: AsyncMock, handler: RecordingHandler
) -> None:
    """ready 뒤 auth 프레임 → INVALID_MESSAGE 접수 전 오류, 연결 유지, 이후 질문 가능."""
    with connect(ws_app) as websocket:
        assert receive_json_within(websocket) == READY
        websocket.send_json({"type": "auth", "accessToken": "synthetic"})
        assert_error(websocket, "INVALID_MESSAGE", None)
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
    assert len(handler.calls) == 1


def test_web_question_revalidates_and_uses_returned_user(
    ws_app: FastAPI, adapter: AsyncMock, handler: RecordingHandler
) -> None:
    """질문마다 재검증 → 처리기는 재검증이 돌려준 회원으로 호출되고 어댑터는 총 2회 호출된다."""
    adapter.side_effect = [AuthenticatedUser(USER_A), AuthenticatedUser(USER_B)]
    with connect(ws_app) as websocket:
        assert receive_json_within(websocket) == READY
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
    assert [call[0] for call in handler.calls] == [AuthenticatedUser(USER_B)]
    assert adapter.await_count == 2


def test_web_question_rejected_keeps_connection(
    ws_app: FastAPI, adapter: AsyncMock, handler: RecordingHandler
) -> None:
    """질문 재검증 거절 → AUTH_REQUIRED 접수 전 오류, 연결 유지, 처리기 없음, 재전송도 같은 오류."""
    adapter.side_effect = [
        AuthenticatedUser(USER_A),
        AuthenticationError("AUTH_REQUIRED"),
        AuthenticationError("AUTH_REQUIRED"),
    ]
    with connect(ws_app) as websocket:
        assert receive_json_within(websocket) == READY
        websocket.send_json(question_frame())
        assert_error(websocket, "AUTH_REQUIRED")
        websocket.send_json(question_frame())
        assert_error(websocket, "AUTH_REQUIRED")
    assert handler.calls == []


def test_web_question_storage_unavailable_then_recovers(
    ws_app: FastAPI, adapter: AsyncMock, handler: RecordingHandler
) -> None:
    """질문 재검증 일시 장애 → AUTH_UNAVAILABLE 접수 전 오류, 연결 유지, 다음 질문은 접수."""
    adapter.side_effect = [
        AuthenticatedUser(USER_A),
        WebSessionUnavailableError(),
        AuthenticatedUser(USER_A),
    ]
    with connect(ws_app) as websocket:
        assert receive_json_within(websocket) == READY
        websocket.send_json(question_frame())
        assert_error(websocket, "AUTH_UNAVAILABLE")
        assert handler.calls == []
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
    assert len(handler.calls) == 1


def test_web_question_unexpected_error_returns_internal_error(
    ws_app: FastAPI,
    adapter: AsyncMock,
    handler: RecordingHandler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """질문 재검증 중 예상 밖 예외 → INTERNAL_ERROR 접수 전 오류, 연결 유지, 로그에 종류·위치만."""
    adapter.side_effect = [
        AuthenticatedUser(USER_A),
        RuntimeError("synthetic"),
        AuthenticatedUser(USER_A),
    ]
    with (
        caplog.at_level(logging.WARNING, logger="app.coaching.connection"),
        connect(ws_app) as websocket,
    ):
        assert receive_json_within(websocket) == READY
        websocket.send_json(question_frame())
        assert_error(websocket, "INTERNAL_ERROR")
        websocket.send_json(question_frame())
        assert receive_json_within(websocket)["type"] == "delta"
    assert "web auth failed: RuntimeError" in caplog.text
    assert "synthetic-cookie" not in caplog.text
    assert len(handler.calls) == 1


class SlowHandler:
    """delta 하나를 바로, 하나를 0.3초 뒤에 보내는 합성 처리기."""

    def __init__(self) -> None:
        """호출 기록을 비운다."""
        self.calls: list[Question] = []

    async def handle(
        self, user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None:
        """첫 delta를 즉시 보내고 두 번째 delta를 지연 전송한다."""
        self.calls.append(question)

        def event(seq: int, text: str) -> dict[str, Any]:
            """합성 delta 이벤트를 만든다."""
            return request_event(
                "delta",
                client_request_id=question.client_request_id,
                request_id=REQUEST_ID,
                conversation_id=question.conversation_id,
                event_seq=seq,
                data={"text": text},
            )

        channel.send(event(2, "첫째"))
        asyncio.get_running_loop().call_later(0.3, channel.send, event(3, "둘째"))


def test_web_rejected_question_does_not_interrupt_running_stream(
    ws_app: FastAPI, adapter: AsyncMock
) -> None:
    """A 스트림 중 B 질문 거절 → A의 나머지 이벤트가 B의 error 뒤에도 도착, C도 거절된다."""
    slow = SlowHandler()
    ws_app.state.coaching_question_handler = slow
    adapter.side_effect = [
        AuthenticatedUser(USER_A),
        AuthenticatedUser(USER_A),
        AuthenticationError("AUTH_REQUIRED"),
        AuthenticationError("AUTH_REQUIRED"),
    ]
    with connect(ws_app) as websocket:
        assert receive_json_within(websocket) == READY
        websocket.send_json(question_frame())
        first = receive_json_within(websocket)
        assert first["type"] == "delta"
        assert first["data"] == {"text": "첫째"}
        websocket.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
        assert_error(websocket, "AUTH_REQUIRED", OTHER_CLIENT_ID)
        second = receive_json_within(websocket)
        assert second["type"] == "delta"
        assert second["data"] == {"text": "둘째"}
        websocket.send_json(question_frame(clientRequestId=OTHER_CLIENT_ID))
        assert_error(websocket, "AUTH_REQUIRED", OTHER_CLIENT_ID)
    assert len(slow.calls) == 1
    assert adapter.await_count == 4


def test_cookieless_connection_uses_auth_frame_and_skips_web_adapter(
    ws_app: FastAPI, adapter: AsyncMock, token: str
) -> None:
    """쿠키·Origin이 없고 auth 프레임이 유효 → ready, 웹 어댑터는 호출되지 않는다."""
    with TestClient(ws_app).websocket_connect(WS_PATH) as websocket:
        websocket.send_json({"type": "auth", "accessToken": token})
        assert receive_json_within(websocket) == READY
    adapter.assert_not_awaited()


def test_auth_unavailable_public_error_matches_rest_message() -> None:
    """AUTH_UNAVAILABLE 공개 오류 → REST 503 메시지와 같고 retryable이 True다."""
    error = PUBLIC_ERRORS["AUTH_UNAVAILABLE"]
    assert error.message == "인증 처리를 일시적으로 이용할 수 없습니다."
    assert error.retryable is True


# 어댑터: authenticate_web_cookie 자체의 변환 규칙. 아래 테스트는 어댑터를 대체하지 않는다.


class FakeSessionFactory:
    """sessionmaker 대역. 열고 닫은 횟수를 기록한다."""

    def __init__(self) -> None:
        """기록용 카운터를 0으로 둔다."""
        self.entered = 0
        self.exited = 0

    def __call__(self) -> Any:
        """async with에 쓸 컨텍스트 매니저를 돌려준다."""
        factory = self

        class _Ctx:
            """세션 대역 컨텍스트."""

            async def __aenter__(self) -> object:
                """열림을 기록하고 세션 대역(object())을 돌려준다."""
                factory.entered += 1
                return object()

            async def __aexit__(self, *exc: object) -> bool:
                """닫힘을 기록한다. 예외는 삼키지 않는다."""
                factory.exited += 1
                return False

        return _Ctx()


def fake_websocket(cookies: dict[str, str]) -> tuple[Any, FakeSessionFactory]:
    """쿠키와 sessionmaker 대역만 가진 가짜 websocket과 대역 팩토리를 만든다."""
    factory = FakeSessionFactory()
    websocket = SimpleNamespace(
        cookies=cookies,
        app=SimpleNamespace(state=SimpleNamespace(sessionmaker=factory)),
    )
    return websocket, factory


def patch_session_auth(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> AsyncMock:
    """ws_auth가 쓰는 authenticate_web_session을 AsyncMock으로 바꿔 돌려준다."""
    mock = AsyncMock(**kwargs)
    monkeypatch.setattr("app.coaching.ws_auth.authenticate_web_session", mock)
    return mock


@pytest.mark.parametrize("value", ["synthetic-cookie", ""])
async def test_ad01_valid_session_returns_member_and_closes_db(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """세션 검증 성공(빈 쿠키 포함) → 회원 반환, 쿠키 값으로 1회 호출, DB 세션은 반환 전 닫힘."""
    session = SimpleNamespace(user_id=USER_A)
    mock = patch_session_auth(monkeypatch, return_value=session)
    websocket, factory = fake_websocket({"__Host-cf_session": value})
    user = await authenticate_web_cookie(websocket)
    assert user == AuthenticatedUser(USER_A)
    mock.assert_awaited_once()
    assert mock.await_args.args[1] == value
    assert factory.entered == factory.exited == 1


@pytest.mark.parametrize("code", ["AUTH_EXPIRED", "AUTH_REQUIRED"])
async def test_ad02_ad03_rejection_becomes_auth_required(
    monkeypatch: pytest.MonkeyPatch, code: Any
) -> None:
    """AUTH_EXPIRED·AUTH_REQUIRED 거절 → 모두 AUTH_REQUIRED로 바뀌고 DB 세션은 닫힘."""
    patch_session_auth(monkeypatch, side_effect=AuthenticationError(code))
    websocket, factory = fake_websocket({"__Host-cf_session": "synthetic-cookie"})
    with pytest.raises(AuthenticationError) as raised:
        await authenticate_web_cookie(websocket)
    assert raised.value.code == "AUTH_REQUIRED"
    assert factory.exited == 1


async def test_ad04_storage_failure_becomes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TemporaryStorageError → WebSessionUnavailableError로 바뀌고 DB 세션은 닫힘."""
    patch_session_auth(monkeypatch, side_effect=TemporaryStorageError("synthetic"))
    websocket, factory = fake_websocket({"__Host-cf_session": "synthetic-cookie"})
    with pytest.raises(WebSessionUnavailableError):
        await authenticate_web_cookie(websocket)
    assert factory.exited == 1


async def test_ad05_unexpected_error_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuntimeError → 변환 없이 그대로 올라오고 DB 세션은 닫힘."""
    patch_session_auth(monkeypatch, side_effect=RuntimeError("synthetic"))
    websocket, factory = fake_websocket({"__Host-cf_session": "synthetic-cookie"})
    with pytest.raises(RuntimeError) as raised:
        await authenticate_web_cookie(websocket)
    assert type(raised.value) is RuntimeError
    assert factory.exited == 1
