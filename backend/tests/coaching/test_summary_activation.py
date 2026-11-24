"""실제 요약 함수의 기본 연결과 완료 날짜·공유 모델 경계를 검사한다."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from functools import partial
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.coaching import agent as agent_module
from app.coaching import summary_adapter
from app.coaching.agent import CoachAgent, default_agent
from app.coaching.history import HistorySnapshot, HistoryTurn, StoredSummary
from app.coaching.lifecycle import coaching_lifespan
from app.coaching.runner import GenerationJob
from app.coaching.schemas import failure_from_coach_error
from app.coaching.summary import SummaryManager
from app.core.config import Settings
from coach_ai.agent.context import CoachContext
from coach_ai.config import Settings as AISettings
from coach_ai.history.budget import INPUT_TOO_LARGE_MESSAGE
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.coaching.fakes import NOW, RecordingChannel, fixed_now
from tests.coaching.summary_fakes import (
    SummaryFakeStore,
    SummarySessionFactory,
    TestClock,
)
from tests.coaching.test_coaching_agent import SSE_HEADERS, text_body

SUMMARY_TEXT = (
    "대화 주제:\n- 합성 목표 확인\n"
    "사용자가 직접 밝힌 목표·선호 및 정정:\n- 없음\n"
    "주요 논의 내용:\n- 합성 기록 확인\n"
    "아직 해결하지 못한 질문:\n- 없음"
)


def summary_response(
    text: str = SUMMARY_TEXT, *, finish_reason: str = "stop"
) -> httpx.Response:
    """실제 생성 검증을 통과하거나 의도대로 실패하는 모의 LLM 응답을 만든다."""
    return httpx.Response(
        200,
        json={
            "id": "synthetic-summary",
            "object": "chat.completion",
            "created": 0,
            "model": "gemma4-31b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
        },
    )


@asynccontextmanager
async def real_summary_agent(
    monkeypatch: pytest.MonkeyPatch, handler: Callable, *, concurrency: int = 1
) -> AsyncIterator[CoachAgent]:
    """합성 설정·메모리 계측·모의 HTTP로 실제 답변·요약 모델을 공유한다."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    for field in AISettings.model_fields.values():
        if isinstance(field.alias, str):
            monkeypatch.delenv(field.alias, raising=False)
    source = AISettings(
        _env_file=None,
        LLM_API_KEY="synthetic-llm-key",
        LLM_BASE_URL="https://llm.test/v1",
        LLM_MAX_CONCURRENCY=concurrency,
        LLM_MAX_RETRIES=2,
    )
    read_settings = Mock(return_value=source)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(agent_module, "get_ai_settings", read_settings)
        monkeypatch.setattr(agent_module, "make_http_client", Mock(return_value=client))
        monkeypatch.setattr(
            agent_module,
            "create_chat_model",
            partial(create_chat_model, recorder=MetricsRecorder()),
        )
        agent = default_agent(Settings(_env_file=None, app_env="test"), Mock())
        assert agent.ensure_ready()
        try:
            async with asyncio.timeout(5):
                yield agent
        finally:
            await agent.aclose()
        read_settings.assert_called_once_with()
        assert source.llm_max_concurrency == concurrency


def seeded_summary_store() -> tuple[SummaryFakeStore, SummarySessionFactory, object]:
    """이미 완료된 3,101토큰의 합성 쌍을 요약 대상과 독립 세션으로 준비한다."""
    store = SummaryFakeStore()
    state = store.seed_accepted(user_id=uuid4(), content="1" * 3101)
    store._finish(state.id, content="", now=NOW, error_code=None, error_context=None)
    return store, SummarySessionFactory(store), state


def test_default_summary_factory_is_enabled_and_lazy() -> None:
    """기본 요약 조립은 활성 함수만 만들고 준비 전 모델에는 접근하지 않는다."""
    provider = Mock()
    functions = summary_adapter.default_summary_functions(model_provider=provider)
    assert functions is not None
    provider.assert_not_called()


def test_answer_date_survives_ai_plan_roundtrip() -> None:
    """완료 날짜는 내부 타입에서 AI 계획을 왕복해도 사라지지 않는다."""
    turn = HistoryTurn("r", "q", "a", 1, 2, "1" * 3101, "", date(2026, 10, 29))
    functions = summary_adapter.ai_summary_functions(model_provider=Mock())
    plan = functions.plan_summary(None, (turn,))
    assert plan is not None and plan.targets == (turn,)
    assert summary_adapter.to_ai_turn(plan.targets[0]).answered_on == date(2026, 10, 29)


def test_input_too_large_uses_ai_public_message() -> None:
    """입력 초과는 AI 공통 안내와 재시도 불가 표시를 그대로 공개한다."""
    data = failure_from_coach_error("input_too_large", ()).event_data(accepted=True)
    assert data["message"] == INPUT_TOO_LARGE_MESSAGE
    assert data["retryable"] is False


@pytest.mark.parametrize("concurrency", [1, 2, 3])
async def test_default_model_has_answer_capacity_during_summary(
    monkeypatch: pytest.MonkeyPatch, concurrency: int
) -> None:
    """명시 동시성 1도 2로 올리고 더 큰 설정은 보존하며 SDK 재시도를 끈다."""
    async with real_summary_agent(
        monkeypatch, Mock(), concurrency=concurrency
    ) as agent:
        assert agent.get_model().max_concurrency == max(2, concurrency)
        assert agent.get_model().max_retries == 0


async def test_lifespan_real_summary_saves_and_next_answer_reuses(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """기본 lifespan은 완료 뒤 실제 HTTP 요약을 저장하고 다음 답변에서 재사용한다."""
    store = SummaryFakeStore()
    sessions = SummarySessionFactory(store)
    owner = uuid4()
    previous = store.seed_accepted(user_id=owner, content="합성 기존 질문")
    store._finish(previous.id, content="", now=NOW, error_code=None, error_context=None)
    stored = StoredSummary("합성 기존 요약", 2)
    store.summaries[previous.conversation_id] = stored
    accepted_at = datetime(2026, 10, 28, 14, 59, 59, tzinfo=UTC)
    finished_at = datetime(2026, 10, 28, 15, 0, 30, tzinfo=UTC)
    for tokens in (1000, 1000):
        state = store.seed_accepted(
            user_id=owner,
            conversation_id=previous.conversation_id,
            content="1" * tokens,
            now=accepted_at,
        )
        store._finish(
            state.id,
            content="",
            now=finished_at,
            error_code=None,
            error_context=None,
        )
    entered, release = asyncio.Event(), asyncio.Event()
    bodies = []
    loop = asyncio.get_running_loop()

    async def http(request: httpx.Request) -> httpx.Response:
        """요약은 완료 이벤트 뒤 같은 루프·모델에서 실행되고 DB 세션은 닫혀 있다."""
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        assert asyncio.get_running_loop() is loop and sessions.active == 0
        body = json.loads(request.content)
        bodies.append(body)
        if body["stream"] is False:
            entered.set()
            await release.wait()
            return summary_response()
        return httpx.Response(200, content=text_body("합성 답변"), headers=SSE_HEADERS)

    async with (
        real_summary_agent(monkeypatch, http) as agent,
        coaching_lifespan(
            FastAPI(),
            settings,
            sessions,
            agent=agent,
            cleanup=AsyncMock(return_value=0),
            clock=fixed_now,
        ) as runner,
    ):
        runner._store, runner._clock = store, fixed_now
        manager = runner._summaries
        assert manager is not None and manager._functions is not None
        manager._store = store
        current = store.seed_accepted(
            user_id=owner,
            conversation_id=previous.conversation_id,
            content="2" * 400,
        )
        channel = RecordingChannel()
        await runner.submit(
            GenerationJob(owner, current, current.user_message.content, channel, 0, 0)
        )
        await entered.wait()
        assert channel.events[-1]["type"] == "completed"
        assert store.summaries[current.conversation_id] == stored
        release.set()
        await asyncio.gather(*manager.tasks)
        assert store.summaries[current.conversation_id] == StoredSummary(
            SUMMARY_TEXT, 4
        )
        next_request = store.seed_accepted(
            user_id=owner,
            conversation_id=current.conversation_id,
            content="합성 다음 질문",
        )
        await runner.submit(
            GenerationJob(
                owner,
                next_request,
                next_request.user_message.content,
                channel,
                0,
                0,
            )
        )
        await asyncio.gather(*manager.tasks)
        assert [event["type"] for event in channel.events].count("completed") == 2
        assert len(bodies) == 3 and store.saves == 1
        summary_body = bodies[1]
        assert summary_body["max_tokens"] == 800
        assert "temperature" not in summary_body and "tools" not in summary_body
        assert "call_kind" not in summary_body
        assert "합성 기존 요약" in summary_body["messages"][1]["content"]
        assert "[질문 1] 2026-10-28 (수)" in summary_body["messages"][1]["content"]
        assert SUMMARY_TEXT in bodies[2]["messages"][0]["content"]
        assert str(owner) not in json.dumps(bodies)


@pytest.mark.parametrize("error", [429, 500, "timeout"])
async def test_real_summary_retries_only_in_manager(
    monkeypatch: pytest.MonkeyPatch, error: int | str
) -> None:
    """실제 HTTP 일시 실패 두 번은 SDK·래퍼 재전송 없이 관리자 간격으로 재시도한다."""
    store, sessions, state = seeded_summary_store()
    clock = TestClock()
    bodies = []

    def http(request: httpx.Request) -> httpx.Response:
        """시도 사이 대기를 먼저 검사해 내부 재시도를 잡고 세 번째에 성공한다."""
        assert len(clock.delays) == len(bodies) and sessions.active == 0
        bodies.append(json.loads(request.content))
        if len(bodies) <= 2:
            if error == "timeout":
                raise httpx.ReadTimeout("synthetic-timeout", request=request)
            return httpx.Response(error, json={"error": {"message": "synthetic-error"}})
        return summary_response()

    async with real_summary_agent(monkeypatch, http) as agent:
        manager = SummaryManager(
            session_factory=sessions,
            store=store,
            clock=fixed_now,
            functions=summary_adapter.default_summary_functions(
                model_provider=agent.get_model
            ),
            monotonic=clock.now,
            sleep=clock.sleep,
        )
        try:
            await manager.submit(
                user_id=store.owners[state.conversation_id],
                conversation_id=state.conversation_id,
            )
            assert len(bodies) == 3 and bodies[0] == bodies[1] == bodies[2]
            assert clock.delays == [30, 120]
            assert store.summaries[state.conversation_id] == StoredSummary(
                SUMMARY_TEXT, 2
            )
        finally:
            await manager.aclose()


@pytest.mark.parametrize(
    "failure", ["invalid_format", "truncated", "config", "input_too_large"]
)
async def test_real_summary_permanent_failure_keeps_completed_answer(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """실제 생성의 형식·잘림·설정·입력 오류는 완료 상태와 기존 요약을 보존한다."""
    store, sessions, state = seeded_summary_store()
    calls = 0

    def http(request: httpx.Request) -> httpx.Response:
        """요약 오류를 합성 HTTP로 재현하고 예상 밖 자동 재시도를 센다."""
        nonlocal calls
        calls += 1
        if failure == "config":
            return httpx.Response(401, json={"error": {"message": "synthetic-auth"}})
        if failure == "input_too_large":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "maximum context length exceeded",
                    }
                },
            )
        return summary_response(
            "형식 없는 합성 글" if failure == "invalid_format" else SUMMARY_TEXT,
            finish_reason="length" if failure == "truncated" else "stop",
        )

    async with real_summary_agent(monkeypatch, http) as agent:
        manager = SummaryManager(
            session_factory=sessions,
            store=store,
            functions=summary_adapter.default_summary_functions(
                model_provider=agent.get_model
            ),
        )
        try:
            await manager.submit(
                user_id=store.owners[state.conversation_id],
                conversation_id=state.conversation_id,
            )
            assert calls == 1 and store.saves == 0
            assert store.state(state.id).status == "completed"
            assert state.conversation_id not in store.summaries
            await manager.submit(
                user_id=store.owners[state.conversation_id],
                conversation_id=state.conversation_id,
            )
            assert calls == 1 and store.saves == 0
        finally:
            await manager.aclose()


async def test_real_summary_does_not_block_answer_and_stays_single(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """요약 하나가 진행 중이어도 같은 모델의 답변은 완료되고 두 번째 요약은 기다린다."""
    entered, release, answered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    summary_calls = 0
    active = maximum = 0

    async def http(request: httpx.Request) -> httpx.Response:
        """겹친 요약 수를 세고 답변은 요약 해제 전에 실제 SSE로 반환한다."""
        nonlocal summary_calls, active, maximum
        body = json.loads(request.content)
        if body["stream"]:
            assert active == 1
            answered.set()
            return httpx.Response(
                200, content=text_body("합성 동시 답변"), headers=SSE_HEADERS
            )
        summary_calls += 1
        active += 1
        maximum = max(maximum, active)
        entered.set()
        try:
            await release.wait()
            return summary_response()
        finally:
            active -= 1

    async with real_summary_agent(monkeypatch, http) as agent:
        functions = summary_adapter.default_summary_functions(
            model_provider=agent.get_model
        )
        plan = functions.plan_summary(
            None, (HistoryTurn("r", "q", "a", 1, 2, "1" * 3101, ""),)
        )
        assert plan is not None
        first = asyncio.create_task(functions.generate_summary(None, plan))
        second = answer_task = None
        try:
            await entered.wait()
            second = asyncio.create_task(functions.generate_summary(None, plan))
            answer_task = asyncio.create_task(
                agent.run_with_history(
                    ctx=CoachContext(user_id="u", conversation_id="c", request_id="r"),
                    question="합성 질문",
                    history=HistorySnapshot(None, (), 1),
                    sink=AsyncMock(),
                    now=NOW,
                    message_id="a",
                )
            )
            await answered.wait()
            assert (await answer_task).finish_reason == "stop"
            assert summary_calls == 1 and not first.done() and not second.done()
            release.set()
            await asyncio.gather(first, second)
            assert summary_calls == 2 and maximum == 1
        finally:
            release.set()
            tasks = [task for task in (first, second, answer_task) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
