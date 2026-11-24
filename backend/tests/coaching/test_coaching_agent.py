"""실제 run_coach와 모의 HTTP 스트림으로 백엔드 연결 계약을 검증한다."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching.agent import CoachAgent
from app.coaching.connection import EventChannel
from app.coaching.history import HistorySnapshot, HistoryTurn, StoredSummary
from app.coaching.runner import CoachingRunner, GenerationJob
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.config import Settings
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.coaching.fakes import (
    NOW,
    CountingModelFactory,
    FakeSessionFactory,
    FakeStore,
    RecordingChannel,
    fixed_now,
)

SSE_HEADERS = {"content-type": "text/event-stream"}


@pytest.fixture(scope="module", autouse=True)
def disable_external_tracing() -> Iterator[None]:
    """실제 AI 테스트 → 외부 추적을 끄고 OS의 LLM 설정 영향을 격리한다."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
        for field in Settings.model_fields.values():
            if isinstance(field.alias, str):
                monkeypatch.delenv(field.alias, raising=False)
        yield


def sse_chunk(
    delta: dict[str, Any], *, finish_reason: str | None = None
) -> dict[str, Any]:
    """OpenAI 호환 스트리밍 응답의 합성 청크를 만든다."""
    return {
        "id": "synthetic-completion",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gemma4-31b",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse_body(chunks: Sequence[dict[str, Any]]) -> bytes:
    """청크를 SSE 프레임과 DONE 종료 표식으로 직렬화한다."""
    return (
        b"".join(
            f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
            for chunk in chunks
        )
        + b"data: [DONE]\n\n"
    )


def text_body(*parts: str) -> bytes:
    """답변 조각을 각각 독립 청크로 내보내는 합성 SSE 본문을 만든다."""
    return sse_body(
        [
            sse_chunk({"role": "assistant", "content": ""}),
            *(sse_chunk({"content": part}) for part in parts),
            sse_chunk({}, finish_reason="stop"),
        ]
    )


def tool_body() -> bytes:
    """지난 7일 걸음 요약 도구의 인자를 두 청크로 나누어 전송한다."""
    arguments = json.dumps({"metric": "steps", "period": "last_7_days"})
    middle = len(arguments) // 2
    return sse_body(
        [
            sse_chunk({"role": "assistant", "content": None}),
            sse_chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "synthetic-tool-call",
                            "type": "function",
                            "function": {
                                "name": "get_metric_summary",
                                "arguments": arguments[:middle],
                            },
                        }
                    ]
                }
            ),
            sse_chunk(
                {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": arguments[middle:]}}
                    ]
                }
            ),
            sse_chunk({}, finish_reason="tool_calls"),
        ]
    )


class ScriptedHTTP:
    """LLM 요청 본문을 기록하고 미리 정한 응답을 순서대로 반환한다."""

    def __init__(self, responses: Sequence[httpx.Response]) -> None:
        """모의 응답 대기열과 요청 본문 목록을 준비한다."""
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """합성 LLM 주소만 허용하고 요청 JSON과 응답 순서를 기록한다."""
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        self.bodies.append(json.loads(request.content))
        return self.responses.pop(0)


class RealAgentHarness:
    """실제 에이전트와 가짜 저장소를 조립하고 합성 질문 작업을 만든다."""

    def __init__(self, client: httpx.AsyncClient, concurrency: int) -> None:
        """환경 파일 없이 AI 설정과 공유 모델을 만들고 실행기를 준비한다."""
        settings = Settings(
            _env_file=None,
            LLM_API_KEY="synthetic-llm-key",
            LLM_BASE_URL="https://llm.test/v1",
            LLM_MAX_CONCURRENCY=concurrency,
            LLM_MAX_RETRIES=0,
        )
        self.model_factory = CountingModelFactory(
            create_chat_model(settings, http_client=client, recorder=MetricsRecorder())
        )
        self.agent = CoachAgent(
            model_factory=self.model_factory,
            port_factory=sample_health_data,
            close=client.aclose,
        )
        self.store = FakeStore()
        self.factory = FakeSessionFactory()
        self.runner = CoachingRunner(
            session_factory=cast(
                Callable[[], AbstractAsyncContextManager[AsyncSession]], self.factory
            ),
            agent=self.agent,
            store=self.store,
            clock=fixed_now,
            finalize_timeout=0.1,
        )
        assert self.runner.is_ready()

    def job(
        self,
        question: str = "지난주 걸음 기록을 알려 줘",
        *,
        user_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[GenerationJob, RecordingChannel]:
        """UUIDv4 합성 회원·요청과 독립 채널을 생성한다."""
        user_id = user_id or uuid4()
        state = self.store.seed_accepted(
            user_id=user_id, conversation_id=conversation_id, content=question
        )
        channel = RecordingChannel()
        return GenerationJob(
            user_id, state, question, cast(EventChannel, channel), 0, 0.01
        ), channel


@asynccontextmanager
async def real_agent(
    handler: Callable[[httpx.Request], httpx.Response], *, concurrency: int = 1
) -> AsyncIterator[RealAgentHarness]:
    """MockTransport 전용 클라이언트를 열고 테스트 뒤 작업·모델 자원을 닫는다."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        harness = RealAgentHarness(client, concurrency)
        try:
            async with asyncio.timeout(0.9):
                yield harness
        finally:
            await harness.runner.aclose(timeout=0.05)
            await harness.agent.aclose()


async def test_real_agent_tool_round_streams_public_events() -> None:
    """B1 도구 왕복 뒤 근거 이벤트와 두 답변 조각의 순번·최종 저장이 일치한다."""
    parts = ("합성 걸음 기록을 ", "확인했어요.")
    handler = ScriptedHTTP(
        [
            httpx.Response(200, content=tool_body(), headers=SSE_HEADERS),
            httpx.Response(200, content=text_body(*parts), headers=SSE_HEADERS),
        ]
    )
    async with real_agent(handler) as harness:
        job, channel = harness.job("지난주 걸음 기록이 며칠 있어?")
        await harness.runner.submit(job)
        assert [event["type"] for event in channel.events] == [
            "status",
            "status",
            "status",
            "status",
            "evidence",
            "delta",
            "delta",
            "completed",
        ]
        assert [
            event["data"]["stage"]
            for event in channel.events
            if event["type"] == "status"
        ] == [
            "reading_history",
            "generating_answer",
            "fetching_health_data",
            "generating_answer",
        ]
        assert [event["eventSeq"] for event in channel.events] == list(range(2, 10))
        assert [
            event["data"]["text"]
            for event in channel.events
            if event["type"] == "delta"
        ] == list(parts)
        completed = channel.events[-1]["data"]
        assert completed["assistantMessage"]["content"] == "".join(parts)
        assert completed["assistantMessage"]["id"] == str(
            job.request.assistant_message.id
        )
        evidence_event = channel.events[4]
        assert len(evidence_event["data"]["items"]) == 1
        item = evidence_event["data"]["items"][0]
        assert set(item) == {
            "id",
            "position",
            "metric",
            "periodStart",
            "periodEnd",
            "snapshot",
        }
        assert item["position"] == 1
        assert item["metric"] == "steps"
        assert str(UUID(item["id"])) == item["id"]
        assert item["periodStart"] == item["snapshot"]["period"]["start"]
        assert item["periodEnd"] == item["snapshot"]["period"]["end"]
        assert set(item["snapshot"]) == {
            "metric",
            "period",
            "unit",
            "summary",
            "days",
            "in_progress",
            "data_status",
            "detail",
        }
        assert "user_id" not in item["snapshot"]
        assert completed["evidence"] == evidence_event["data"]["items"]
        stored_evidence = harness.store.state(job.request.id).evidence
        assert len(stored_evidence) == 1
        assert stored_evidence[0].id == UUID(item["id"])
        assert harness.store.state(job.request.id).status == "completed"
        assert harness.store.state(job.request.id).assistant_message.content == "".join(
            parts
        )
        assert len(handler.bodies) == 2
        assert handler.bodies[0]["stream"] is True
        assert handler.bodies[1]["messages"][-1]["role"] == "tool"
        assert harness.factory.active == 0


async def test_real_agent_llm_500_maps_to_public_failure() -> None:
    """B2 LLM 500·재시도 0 → AI_UNAVAILABLE을 한 번 보내고 실패를 저장한다."""
    handler = ScriptedHTTP(
        [
            httpx.Response(
                500,
                json={
                    "error": {
                        "message": "synthetic-server-failure",
                        "type": "server_error",
                    }
                },
            )
        ]
    )
    async with real_agent(handler) as harness:
        job, channel = harness.job()
        await harness.runner.submit(job)
        state = harness.store.state(job.request.id)
        assert state.status == "failed"
        assert state.error_code == "AI_UNAVAILABLE"
        assert state.error_context is None
        assert channel.events[-1]["type"] == "error"
        assert channel.events[-1]["data"]["code"] == "AI_UNAVAILABLE"
        assert channel.events[-1]["data"]["accepted"] is True
        assert channel.events[-1]["data"]["retryable"] is True
        assert "reason" not in channel.events[-1]["data"]
        assert len(handler.bodies) == 1


async def test_real_agent_context_history_survives_tool_round_with_usage() -> None:
    """실제 도구 왕복은 요약·원문을 한 번씩 넣고 마지막 사용량과 ID 비노출을 보존한다."""
    handler = ScriptedHTTP(
        [
            httpx.Response(200, content=tool_body(), headers=SSE_HEADERS),
            httpx.Response(
                200, content=text_body("합성 완료 답변"), headers=SSE_HEADERS
            ),
        ]
    )
    old = HistoryTurn(
        "history-request",
        "history-question",
        "history-answer",
        3,
        4,
        "1" * 1000,
        "2" * 400,
    )
    history = HistorySnapshot(StoredSummary("3" * 100, 2), (old,), 5)
    question = "지난주 걸음 기록이 며칠 있어?"
    async with real_agent(handler) as harness:
        result = await harness.agent.run_with_history(
            ctx=CoachContext(
                user_id="synthetic-user",
                conversation_id="synthetic-conversation",
                request_id="synthetic-request",
            ),
            question=question,
            history=history,
            sink=AsyncMock(),
            now=NOW,
            message_id="synthetic-answer",
        )
        assert result.finish_reason == "stop" and result.tool_rounds == 1
        assert result.history_usage is not None
        assert result.history_usage.summary_used is True
        assert result.history_usage.included_turns == 1
        assert result.history_usage.excluded_turns == 0
        assert result.history_usage.history_tokens == 1400
        assert result.history_usage.summary_tokens == 100
        assert len(handler.bodies) == 2
        for body in handler.bodies:
            messages = body["messages"]
            assert messages[0]["content"].count("3" * 100) == 1
            for content in (old.question, old.answer, question):
                assert (
                    sum(message.get("content") == content for message in messages) == 1
                )
            serialized = json.dumps(body)
            assert all(
                identifier not in serialized
                for identifier in (
                    old.request_id,
                    old.question_message_id,
                    old.answer_message_id,
                )
            )


class ConcurrentHTTP:
    """동시에 열린 HTTP 응답 스트림 수를 세어 공용 limiter를 검증한다."""

    def __init__(self, expected: int) -> None:
        """관측 카운터와 두 스트림 동시 진입용 이벤트를 준비한다."""
        self.expected = expected
        self.active = 0
        self.maximum = 0
        self.bodies: list[dict[str, Any]] = []
        self.overlap = asyncio.Event()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """합성 요청을 기록하고 열림·닫힘을 추적할 지연 스트림을 반환한다."""
        assert str(request.url) == "https://llm.test/v1/chat/completions"
        self.bodies.append(json.loads(request.content))
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        if self.active == 2:
            self.overlap.set()
        return httpx.Response(200, stream=DelayedSSE(self), headers=SSE_HEADERS)


class DelayedSSE(httpx.AsyncByteStream):
    """짧은 비동기 지연을 넣고 aclose 때 열린 스트림 카운터를 줄인다."""

    def __init__(self, tracker: ConcurrentHTTP) -> None:
        """공유 관측기와 중복 close 방지 상태를 보관한다."""
        self.tracker = tracker
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """동시성 2에서는 겹침을 확인한 뒤 짧은 지연과 합성 SSE를 전송한다."""
        if self.tracker.expected == 2:
            async with asyncio.timeout(0.3):
                await self.tracker.overlap.wait()
        await asyncio.sleep(0.02)
        yield text_body("합성 동시 요청 답변")

    async def aclose(self) -> None:
        """스트림을 한 번만 닫고 열린 스트림 수를 줄인다."""
        if not self.closed:
            self.closed = True
            self.tracker.active -= 1


@pytest.mark.parametrize("concurrency", [1, 2])
async def test_real_agent_shared_limiter_bounds_streams(concurrency: int) -> None:
    """B3 동시 작업 두 개 → 설정 1에서는 직렬로, 설정 2에서는 실제로 겹쳐 실행한다."""
    handler = ConcurrentHTTP(concurrency)
    # pytest의 함수별 이벤트 루프에서 LIMITER도 새 풀을 만든다.
    async with real_agent(handler, concurrency=concurrency) as harness:
        pairs = [harness.job(), harness.job()]
        tasks = [harness.runner.submit(job) for job, _ in pairs]
        await asyncio.gather(*tasks)
        assert handler.maximum == concurrency
        assert handler.active == 0
        assert len(handler.bodies) == 2
        assert harness.model_factory.calls == 1
        for job, channel in pairs:
            assert harness.store.state(job.request.id).status == "completed"
            assert channel.events[-1]["type"] == "completed"


async def test_real_agent_prompt_contains_only_completed_history() -> None:
    """B4 완료·실패 이력 → 이전 완료 문답만 이번 질문 앞에 순서대로 전달한다."""
    handler = ScriptedHTTP(
        [httpx.Response(200, content=text_body("합성 최종 답변"), headers=SSE_HEADERS)]
    )
    async with real_agent(handler) as harness:
        user_id = uuid4()
        previous = harness.store.seed_accepted(
            user_id=user_id, content="합성 이전 질문"
        )
        async with harness.factory() as session:
            await harness.store.complete(
                cast(AsyncSession, session),
                request_id=previous.id,
                answer="합성 이전 답변",
                now=NOW,
            )
            failed = harness.store.seed_accepted(
                user_id=user_id,
                conversation_id=previous.conversation_id,
                content="합성 실패 질문",
            )
            await harness.store.fail(
                cast(AsyncSession, session),
                request_id=failed.id,
                error_code="AI_UNAVAILABLE",
                error_context=None,
                partial_answer="합성 실패 부분 답변",
                now=NOW,
            )
            await session.commit()
        job, channel = harness.job(
            "지난주 걸음 기록을 설명해 줘",
            user_id=user_id,
            conversation_id=previous.conversation_id,
        )
        await harness.runner.submit(job)
        messages = handler.bodies[0]["messages"]
        assert messages[0]["role"] == "system"
        assert [(message["role"], message["content"]) for message in messages[1:]] == [
            ("user", "합성 이전 질문"),
            ("assistant", "합성 이전 답변"),
            ("user", job.question),
        ]
        assert channel.events[-1]["type"] == "completed"
        assert harness.store.state(job.request.id).status == "completed"
