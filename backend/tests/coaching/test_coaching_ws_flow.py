"""앱 수명을 유지한 실제 WS 경로에서 접수·생성·끊김·정리를 검사한다."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from threading import Event as ThreadEvent
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.testclient import WebSocketTestSession

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.auth.tokens import issue_access_token
from app.coaching.agent import CoachAgent
from app.coaching.lifecycle import coaching_lifespan
from app.coaching.repository import RequestState
from app.coaching.runner import CoachingRunner, utc_now
from app.coaching.service import CoachingQuestionHandler
from app.core.config import Settings
from app.main import create_app
from coach_ai.agent.graph import CoachResult
from tests.coaching.fakes import (
    CountingModelFactory,
    DelayedRunCoach,
    FakeSessionFactory,
    FakeStore,
    RecordingPortFactory,
    ScriptedRunCoach,
    fixed_now,
)
from tests.coaching.test_coaching_cleanup import bounded_lifespan
from tests.coaching.test_ws_connection import (
    authenticate,
    expect_close_within,
    receive_json_within,
)

USER = uuid4()
WS_PATH = "/api/v1/coaching/ws"
CONTENT = "합성 WS 질문 비공개 문자열 " * 6
ANSWER = "합성 WS 답변 비공개 문자열 전체"
PARTIAL = "합성 WS 답변 "


class Flow:
    """한 테스트 앱에서 공유할 합성 저장소·실행·토큰을 보관한다."""

    def __init__(self, settings: Settings, run: ScriptedRunCoach | None = None) -> None:
        """외부 I/O 없는 의존성과 합성 사용자의 액세스 토큰을 준비한다."""
        self.store = FakeStore()
        self.factory = FakeSessionFactory()
        self.run = (
            run
            if run is not None
            else DelayedRunCoach(
                [
                    {"type": "status", "stage": "answering"},
                    {"type": "token", "delta": ANSWER},
                ],
                result=CoachResult(answer=ANSWER, evidence=[], finish_reason="stop"),
            )
        )
        self.model = CountingModelFactory()
        self.commit_entered = ThreadEvent()
        self.token = issue_access_token(USER, settings, now=utc_now()).token


@contextmanager
def running_flow(
    settings: Settings,
    *,
    run: ScriptedRunCoach | None = None,
    delay_commit: bool = False,
) -> Iterator[tuple[TestClient, Flow]]:
    """TestClient 수명 동안 앱 루프를 유지하고 실제 처리기·실행기를 등록한다."""
    flow = Flow(settings, run)
    app = create_app(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """합성 의존성을 앱 루프에서 조립하고 종료 때 실행기·에이전트를 닫는다."""
        if delay_commit:
            gate = asyncio.Event()
            flow.factory.commit_gate = gate
            loop = asyncio.get_running_loop()

            def on_commit() -> None:
                """접수 commit 진입을 알리고 앱 루프에서 잠시 뒤 게이트를 연다."""
                if not gate.is_set():
                    flow.commit_entered.set()
                    loop.call_later(0.35, gate.set)

            flow.factory.on_commit = on_commit
        agent = CoachAgent(
            model_factory=flow.model, port_factory=RecordingPortFactory(), run=flow.run
        )
        runner = CoachingRunner(
            session_factory=flow.factory, agent=agent, store=flow.store, clock=fixed_now
        )
        application.state.coaching_runner = runner
        application.state.coaching_question_handler = CoachingQuestionHandler(
            session_factory=flow.factory,
            runner=runner,
            store=flow.store,
            clock=fixed_now,
        )
        try:
            yield
        finally:
            application.state.coaching_question_handler = None
            try:
                await runner.aclose()
            finally:
                await agent.aclose()

    app.router.lifespan_context = lifespan
    with TestClient(app) as client:
        yield client, flow


def frame(
    *,
    client_id: UUID | None = None,
    conversation_id: str | None = None,
    content: str = CONTENT,
) -> dict[str, Any]:
    """UUIDv4와 합성 본문으로 WS 질문 프레임을 만든다."""
    return {
        "type": "question",
        "clientRequestId": str(client_id or uuid4()),
        "conversationId": conversation_id,
        "content": content,
    }


def wait_until(predicate: Callable[[], bool]) -> None:
    """앱 루프의 합성 저장 상태가 조건을 만족하는지 최대 3초 확인한다."""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("3초 내 합성 저장 상태가 기대 조건을 만족하지 못했습니다")


def wait_completed(flow: Flow, request_id: str) -> RequestState:
    """요청 종료를 짧게 기다리고 전체 답변이 저장된 completed 상태를 반환한다."""
    request_uuid = UUID(request_id)
    wait_until(lambda: flow.store.state(request_uuid).status == "completed")
    state = flow.store.state(request_uuid)
    assert state.assistant_message.content == ANSWER
    assert state.finished_at is not None
    return state


def receive_completed(websocket: WebSocketTestSession) -> list[dict[str, Any]]:
    """최대 5초 안에 단일 요청의 completed까지 이벤트를 모은다."""
    events = []
    deadline = time.monotonic() + 5
    while len(events) < 20:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "5초 내 completed를 받지 못했습니다"
        event = receive_json_within(websocket, timeout=remaining)
        events.append(event)
        assert event["type"] != "error"
        if event["type"] == "completed":
            return events
    pytest.fail("completed 전에 예상보다 많은 이벤트를 받았습니다")


def receive_events(websocket: WebSocketTestSession, count: int) -> list[dict[str, Any]]:
    """프레임 묶음을 총 5초 기한 안에 수신한다."""
    deadline = time.monotonic() + 5
    events = []
    for _ in range(count):
        remaining = deadline - time.monotonic()
        assert remaining > 0, "5초 내 이벤트 묶음을 받지 못했습니다"
        events.append(receive_json_within(websocket, timeout=remaining))
    return events


def streaming_run() -> DelayedRunCoach:
    """답변 조각 사이에 지연을 넣어 첫 delta 이후에도 생성을 계속한다."""
    return DelayedRunCoach(
        [
            {"type": "status", "stage": "answering"},
            {"type": "token", "delta": PARTIAL},
            {"type": "token", "delta": ANSWER[len(PARTIAL) :]},
        ],
        result=CoachResult(answer=ANSWER, evidence=[], finish_reason="stop"),
        event_interval=0.2,
    )


def assert_pre_accept(event: dict[str, Any], sent: dict[str, Any], code: str) -> None:
    """접수 거절 → 올바른 요청 ID·순번·접수 여부로 오류를 보낸다."""
    assert event["type"] == "error"
    assert event["data"]["code"] == code
    assert event["data"]["accepted"] is False
    assert event["clientRequestId"] == sent["clientRequestId"]
    assert event["eventSeq"] == 0
    assert event["requestId"] is None
    assert event["conversationId"] is None


def test_normal_flow_order_storage_and_private_logs(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """정상 질문 → 1~5 순서·ID·제목·완료 저장을 보장하고 본문을 로그에 남기지 않는다."""
    sent = frame(content=f"  {CONTENT}  ")
    with caplog.at_level(logging.DEBUG), running_flow(settings) as (client, flow):
        with client.websocket_connect(WS_PATH) as websocket:
            authenticate(websocket, flow.token)
            websocket.send_json(sent)
            events = receive_completed(websocket)
        assert [e["type"] for e in events] == [
            "accepted",
            "status",
            "status",
            "delta",
            "completed",
        ]
        assert [e["eventSeq"] for e in events] == [1, 2, 3, 4, 5]
        assert events[1]["data"] == {"stage": "reading_history"}
        assert events[2]["data"] == {"stage": "generating_answer"}
        assert events[3]["data"] == {"text": ANSWER}
        assert events[0]["data"]["conversation"]["title"] == CONTENT.strip()[:80]
        for key in ("clientRequestId", "requestId", "conversationId"):
            assert {event[key] for event in events} == {events[0][key]}
        assert events[0]["clientRequestId"] == sent["clientRequestId"]
        wait_completed(flow, events[0]["requestId"])
        assert flow.run.calls[0]["question"] == CONTENT.strip()
    assert CONTENT.strip() not in caplog.text
    assert ANSWER not in caplog.text
    assert PARTIAL not in caplog.text
    assert flow.token not in caplog.text


def test_completed_duplicate_returns_snapshot_without_ai(settings: Settings) -> None:
    """완료 뒤 동일 전송 → completed snapshot만 반환하고 AI 호출은 한 번이다."""
    with (
        running_flow(settings) as (client, flow),
        client.websocket_connect(WS_PATH) as websocket,
    ):
        authenticate(websocket, flow.token)
        sent = frame()
        websocket.send_json(sent)
        events = receive_completed(websocket)
        websocket.send_json(sent)
        snapshot = receive_json_within(websocket)
        assert snapshot["type"] == "request_snapshot"
        assert snapshot["eventSeq"] == 0
        assert snapshot["clientRequestId"] == sent["clientRequestId"]
        assert snapshot["requestId"] == events[0]["requestId"]
        assert snapshot["data"]["status"] == "completed"
        assert snapshot["data"]["assistantMessage"]["content"] == ANSWER
        assert len(flow.run.calls) == 1


def test_buffered_new_conversations_each_complete_in_order(settings: Settings) -> None:
    """수신 전 새 대화 두 질문 전송 → 각각 accepted와 독립 순번으로 완료한다."""
    with (
        running_flow(settings) as (client, flow),
        client.websocket_connect(WS_PATH) as websocket,
    ):
        authenticate(websocket, flow.token)
        sent = [frame(), frame(content="두 번째 합성 질문")]
        for question in sent:
            websocket.send_json(question)
        events = receive_events(websocket, 10)
        conversation_ids = set()
        for question in sent:
            own = [
                e for e in events if e["clientRequestId"] == question["clientRequestId"]
            ]
            assert [e["type"] for e in own] == [
                "accepted",
                "status",
                "status",
                "delta",
                "completed",
            ]
            assert [e["eventSeq"] for e in own] == [1, 2, 3, 4, 5]
            assert len({e["requestId"] for e in own}) == 1
            assert len({e["conversationId"] for e in own}) == 1
            conversation_ids.add(own[0]["conversationId"])
            wait_completed(flow, own[0]["requestId"])
        assert len(conversation_ids) == 2
        assert len(flow.run.calls) == 2


def test_buffered_same_id_yields_one_acceptance_and_snapshot(
    settings: Settings,
) -> None:
    """수신 전 동일 질문 두 번 전송 → accepted·진행 snapshot·AI 한 번이다."""
    with (
        running_flow(settings) as (client, flow),
        client.websocket_connect(WS_PATH) as websocket,
    ):
        authenticate(websocket, flow.token)
        sent = frame()
        websocket.send_json(sent)
        websocket.send_json(sent)
        events = receive_events(websocket, 6)
        assert events[0]["type"] == "accepted"
        snapshots = [e for e in events if e["type"] == "request_snapshot"]
        assert len(snapshots) == 1
        assert snapshots[0]["eventSeq"] == 0
        assert snapshots[0]["data"]["status"] in {"accepted", "generating"}
        live = [e for e in events if e["type"] != "request_snapshot"]
        assert [e["eventSeq"] for e in live] == [1, 2, 3, 4, 5]
        assert all(e["clientRequestId"] == sent["clientRequestId"] for e in events)
        wait_completed(flow, events[0]["requestId"])
        assert len(flow.run.calls) == 1
        assert len(flow.store.requests) == 1


def test_buffered_existing_conversation_rejects_second_until_completed(
    settings: Settings,
) -> None:
    """기존 대화에 두 질문 전송 → 첫 질문만 접수하고 완료 뒤 거절된 질문을 접수한다."""
    with (
        running_flow(settings) as (client, flow),
        client.websocket_connect(WS_PATH) as websocket,
    ):
        authenticate(websocket, flow.token)
        websocket.send_json(frame())
        initial = receive_completed(websocket)[0]
        first = frame(conversation_id=initial["conversationId"])
        second = frame(
            conversation_id=initial["conversationId"], content="다음 합성 질문"
        )
        websocket.send_json(first)
        websocket.send_json(second)
        events = receive_events(websocket, 6)
        first_events = [
            e for e in events if e["clientRequestId"] == first["clientRequestId"]
        ]
        assert [e["type"] for e in first_events] == [
            "accepted",
            "status",
            "status",
            "delta",
            "completed",
        ]
        errors = [
            e for e in events if e["clientRequestId"] == second["clientRequestId"]
        ]
        assert len(errors) == 1
        assert_pre_accept(errors[0], second, "CONVERSATION_BUSY")
        assert flow.factory.rollbacks == 1
        websocket.send_json(second)
        assert receive_completed(websocket)[0]["type"] == "accepted"
        assert len(flow.run.calls) == 3


def test_disconnect_after_delta_still_stores_and_reconnects(settings: Settings) -> None:
    """delta 수신 후 연결 종료 → 전체 답변을 저장하고 재연결에서 완료 snapshot을 받는다."""
    with running_flow(settings, run=streaming_run()) as (client, flow):
        sent = frame()
        with client.websocket_connect(WS_PATH) as websocket:
            authenticate(websocket, flow.token)
            websocket.send_json(sent)
            events = receive_events(websocket, 4)
            assert events[-1]["type"] == "delta"
            assert events[-1]["data"] == {"text": PARTIAL}
            assert flow.store.state(UUID(events[0]["requestId"])).status == "generating"
            websocket.close()
        wait_completed(flow, events[0]["requestId"])
        assert flow.run.cancelled is False
        with client.websocket_connect(WS_PATH) as reconnected:
            authenticate(reconnected, flow.token)
            reconnected.send_json(sent)
            snapshot = receive_json_within(reconnected)
            assert snapshot["type"] == "request_snapshot"
            assert snapshot["eventSeq"] == 0
            assert snapshot["data"]["status"] == "completed"
            assert snapshot["data"]["assistantMessage"]["content"] == ANSWER
        assert len(flow.run.calls) == 1


@pytest.mark.parametrize("emit_error", [False, True])
def test_input_too_large_sends_nonretryable_error_and_restores_failure(
    settings: Settings, emit_error: bool
) -> None:
    """입력 초과는 범위 축소를 안내하고 같은 전송 ID에는 재생성 없이 실패를 복원한다."""
    events = [{"type": "token", "delta": PARTIAL}]
    if emit_error:
        events.append({"type": "error", "code": "input_too_large"})
    run = ScriptedRunCoach(
        events,
        result=CoachResult(PARTIAL, [], "error", error_code="input_too_large"),
    )
    with running_flow(settings, run=run) as (client, flow):
        sent = frame()
        with client.websocket_connect(WS_PATH) as websocket:
            authenticate(websocket, flow.token)
            websocket.send_json(sent)
            received = receive_events(websocket, 4)
            assert [item["type"] for item in received] == [
                "accepted",
                "status",
                "delta",
                "error",
            ]
            assert received[-1]["data"] == {
                "code": "AI_UNAVAILABLE",
                "message": "질문과 조회한 기록이 너무 길어 답변을 만들 수 없어요. 질문을 나눠서 다시 물어봐 주세요.",
                "retryable": False,
                "accepted": True,
            }
            state = flow.store.state(UUID(received[0]["requestId"]))
            assert state.status == "failed" and state.error_code == "AI_UNAVAILABLE"
            assert (
                state.error_context is None
                and state.assistant_message.content == PARTIAL
            )
        with client.websocket_connect(WS_PATH) as reconnected:
            authenticate(reconnected, flow.token)
            reconnected.send_json(sent)
            snapshot = receive_json_within(reconnected)
            assert snapshot["type"] == "request_snapshot" and snapshot["eventSeq"] == 0
            assert snapshot["data"]["status"] == "failed"
            assert snapshot["data"]["errorCode"] == "AI_UNAVAILABLE"
            assert snapshot["data"]["errorContext"] is None
            assert snapshot["data"]["assistantMessage"]["content"] == PARTIAL
            assert (
                "retryable" not in snapshot["data"]
                and "message" not in snapshot["data"]
            )
        assert len(flow.run.calls) == 1


def test_two_connections_keep_live_events_on_original_channel(
    settings: Settings,
) -> None:
    """동일 회원의 다른 연결 → BUSY·snapshot만 받고 원래 요청의 실시간 이벤트는 받지 않는다."""
    with (
        running_flow(settings, run=streaming_run()) as (client, flow),
        client.websocket_connect(WS_PATH) as first,
        client.websocket_connect(WS_PATH) as second,
    ):
        authenticate(first, flow.token)
        authenticate(second, flow.token)
        sent = frame()
        first.send_json(sent)
        accepted = receive_json_within(first)
        assert accepted["type"] == "accepted"
        busy = frame(conversation_id=accepted["conversationId"])
        second.send_json(busy)
        assert_pre_accept(receive_json_within(second), busy, "CONVERSATION_BUSY")
        second.send_json(sent)
        snapshot = receive_json_within(second)
        assert snapshot["type"] == "request_snapshot"
        assert snapshot["eventSeq"] == 0
        assert snapshot["data"]["status"] in {"accepted", "generating"}
        assert snapshot["requestId"] == accepted["requestId"]
        tail = receive_completed(first)
        assert any(event["type"] == "delta" for event in tail)
        wait_completed(flow, accepted["requestId"])
        second.send_json(
            {"type": "undefined", "clientRequestId": sent["clientRequestId"]}
        )
        assert_pre_accept(receive_json_within(second), sent, "INVALID_MESSAGE")
        assert len(flow.run.calls) == 1


@pytest.mark.parametrize(
    ("error_type", "code"),
    [(ConnectionError, "PERSISTENCE_FAILED"), (ValueError, "INTERNAL_ERROR")],
)
def test_accept_failure_keeps_connection_for_next_question(
    settings: Settings, error_type: type[Exception], code: str
) -> None:
    """접수 저장 장애·예상 밖 예외 → 접수 전 오류 뒤 같은 연결의 다음 질문이 성공한다."""
    with (
        running_flow(settings) as (client, flow),
        client.websocket_connect(WS_PATH) as websocket,
    ):
        authenticate(websocket, flow.token)
        flow.store.errors["accept"] = error_type("합성 예외 문구")
        failed = frame()
        websocket.send_json(failed)
        assert_pre_accept(receive_json_within(websocket), failed, code)
        assert flow.store.requests == {}
        assert flow.run.calls == []
        assert flow.factory.rollbacks == 1
        flow.store.errors.clear()
        websocket.send_json(frame())
        events = receive_completed(websocket)
        assert events[0]["type"] == "accepted"
        wait_completed(flow, events[0]["requestId"])


def test_token_expiry_closes_connection_but_keeps_accepted_generation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 번째 질문 재검증 만료 → AUTH_EXPIRED·1008로 닫아도 첫 생성은 완료한다."""
    verifier = Mock(
        side_effect=[
            AuthenticatedUser(USER),
            AuthenticatedUser(USER),
            AuthenticationError("AUTH_EXPIRED"),
        ]
    )
    monkeypatch.setattr("app.coaching.router.authenticate_token", verifier)
    run = DelayedRunCoach(
        [{"type": "status", "stage": "answering"}, {"type": "token", "delta": ANSWER}],
        result=CoachResult(answer=ANSWER, evidence=[], finish_reason="stop"),
        delay=0.5,
    )
    with running_flow(settings, run=run) as (client, flow):
        with client.websocket_connect(WS_PATH) as websocket:
            authenticate(websocket, flow.token)
            websocket.send_json(frame())
            events = receive_events(websocket, 4)
            assert events[0]["type"] == "accepted"
            assert events[-1]["type"] == "delta"
            websocket.send_json(frame())
            assert receive_json_within(websocket) == {
                "type": "connection_error",
                "data": {"code": "AUTH_EXPIRED", "message": "로그인이 필요합니다."},
            }
            expect_close_within(websocket, 1008)
        wait_completed(flow, events[0]["requestId"])
        assert len(flow.store.requests) == 1
        assert len(flow.run.calls) == 1
        assert verifier.call_count == 3
        assert flow.run.cancelled is False


def test_disconnect_during_commit_still_generates_after_gate_opens(
    settings: Settings,
) -> None:
    """commit 대기 중 클라이언트 close → 앱 루프가 commit을 마치고 전체 답변을 저장한다."""
    with (
        running_flow(settings, delay_commit=True) as (client, flow),
        client.websocket_connect(WS_PATH) as websocket,
    ):
        authenticate(websocket, flow.token)
        websocket.send_json(frame())
        assert flow.commit_entered.wait(1)
        assert flow.run.calls == []
        assert flow.factory.commit_gate is not None
        assert not flow.factory.commit_gate.is_set()
        websocket.close()
        # TestClient 컨텍스트 종료의 강제 취소 전에 실제 disconnect 프레임을 처리시킨다.
        wait_until(
            lambda: any(
                s.status == "completed" for s in list(flow.store.requests.values())
            )
        )
        state = next(iter(flow.store.requests.values()))
        wait_completed(flow, str(state.id))
        assert flow.factory.commits == 3
        assert len(flow.run.calls) == 1
        assert flow.run.cancelled is False


async def test_real_app_lifespan_registers_and_clears_without_external_io(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실제 앱 시작·종료 → DB·LLM 접속과 AI 설정 로딩 없이 처리기를 등록·해제한다."""
    connect = Mock(side_effect=AssertionError("DB 연결 금지"))
    ai_settings = Mock(side_effect=AssertionError("AI 설정 로딩 금지"))
    send = AsyncMock(side_effect=AssertionError("HTTP 요청 금지"))
    monkeypatch.setattr(AsyncEngine, "connect", connect)
    monkeypatch.setattr("app.coaching.agent.get_ai_settings", ai_settings)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    app = create_app(settings)
    async with bounded_lifespan(app.router.lifespan_context(app)):
        assert isinstance(app.state.coaching_question_handler, CoachingQuestionHandler)
        assert isinstance(app.state.coaching_runner, CoachingRunner)
        assert app.state.coaching_runner.active_count == 0
        provider_http = app.state.social_provider.http
        assert not provider_http.is_closed
    assert app.state.coaching_question_handler is None
    assert provider_http.is_closed
    connect.assert_not_called()
    ai_settings.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize("runner_fails", [False, True])
async def test_coaching_lifespan_always_closes_runner_then_agent(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, runner_fails: bool
) -> None:
    """활성 요약 관리자를 연결하고 정상·실패 종료 모두 실행기 뒤 에이전트를 닫는다."""
    app = FastAPI()
    factory = FakeSessionFactory()
    order = []

    async def close_runner() -> None:
        """실행기 종료 → 이미 접수가 차단됐는지 확인하고 선택적으로 실패한다."""
        assert app.state.coaching_question_handler is None
        order.append("runner")
        if runner_fails:
            raise RuntimeError("합성 종료 실패")

    async def close_agent() -> None:
        """에이전트 종료 → 실행기 종료 시도 뒤 호출됨을 기록한다."""
        order.append("agent")

    runner = Mock(spec=CoachingRunner)
    runner.aclose = AsyncMock(side_effect=close_runner)
    agent = Mock(spec=CoachAgent)
    agent.aclose = AsyncMock(side_effect=close_agent)
    constructor = Mock(return_value=runner)
    monkeypatch.setattr("app.coaching.lifecycle.CoachingRunner", constructor)

    async def enter_and_exit() -> None:
        """직접 lifespan 진입 → 주입한 실행기를 yield하고 실제 처리기를 등록한다."""
        async with bounded_lifespan(
            coaching_lifespan(app, settings, factory, agent=agent)
        ) as yielded:
            assert yielded is runner
            assert app.state.coaching_runner is runner
            assert isinstance(
                app.state.coaching_question_handler, CoachingQuestionHandler
            )

    if runner_fails:
        with pytest.raises(RuntimeError, match="합성 종료 실패"):
            await enter_and_exit()
    else:
        await enter_and_exit()
    constructor.assert_called_once()
    arguments = constructor.call_args.kwargs
    summaries = arguments["summaries"]
    assert arguments["session_factory"] is factory
    assert arguments["agent"] is agent
    assert summaries.tasks == frozenset()
    assert summaries._functions is not None
    agent.get_model.assert_not_called()
    await summaries.aclose()
    runner.aclose.assert_awaited_once()
    agent.aclose.assert_awaited_once()
    assert order == ["runner", "agent"]
    assert app.state.coaching_question_handler is None
    assert factory.opened == 0
