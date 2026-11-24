"""합성 HTTP·저장소로 DB 건강 포트의 코칭 조립과 오류 전달을 검증한다."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from functools import partial
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.coaching import agent as agent_module
from app.coaching.agent import CoachAgent, default_agent
from app.coaching.health_port import HealthQueryPort
from app.coaching.lifecycle import coaching_lifespan
from app.coaching.runner import CoachingRunner, GenerationJob
from app.coaching.schemas import request_snapshot_event, request_state_data
from app.core.config import Settings
from app.health import errors as health_errors
from app.health import service as health_service
from app.health.schemas import HealthMetricResult
from coach_ai.config import Settings as AISettings
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.ports import HealthDataPort, HealthDataUnavailableError
from tests.coaching.fakes import (
    MODEL,
    CountingModelFactory,
    FakeSessionFactory,
    FakeStore,
    RecordingChannel,
    fixed_now,
)
from tests.coaching.test_coaching_agent import SSE_HEADERS, ScriptedHTTP, tool_body
from tests.coaching.test_coaching_cleanup import bounded_lifespan
from tests.coaching.test_coaching_runner import bounded_test
from tests.coaching.test_health_port import START, FakeLookup
from tests.health.test_calculations import NOW, TODAY, calculate, steps
from tests.health.test_schemas import USER


@pytest.fixture(scope="module", autouse=True)
def disable_external_tracing() -> Iterator[None]:
    """외부 추적을 끄고 OS의 LLM 설정 영향을 격리한다."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
        for field in AISettings.model_fields.values():
            if isinstance(field.alias, str):
                monkeypatch.delenv(field.alias, raising=False)
        yield


def synthetic_ai_settings(*, concurrency: int = 1) -> AISettings:
    """환경 파일을 읽지 않고 합성 LLM 주소·키와 재시도 없는 설정을 만든다."""
    return AISettings(
        _env_file=None,
        LLM_API_KEY="synthetic-llm-key",
        LLM_BASE_URL="https://llm.test/v1",
        LLM_MAX_CONCURRENCY=concurrency,
        LLM_MAX_RETRIES=0,
    )


def wire_mock_llm(
    monkeypatch: pytest.MonkeyPatch,
    client: httpx.AsyncClient,
    *,
    concurrency: int = 1,
) -> None:
    """기본 에이전트의 지연 모델을 합성 설정·모의 HTTP·메모리 계측에 연결한다."""
    monkeypatch.setattr(
        agent_module,
        "get_ai_settings",
        Mock(return_value=synthetic_ai_settings(concurrency=concurrency)),
    )
    monkeypatch.setattr(agent_module, "make_http_client", Mock(return_value=client))
    monkeypatch.setattr(
        agent_module,
        "create_chat_model",
        partial(create_chat_model, recorder=MetricsRecorder()),
    )


def health_now() -> datetime:
    """합성 건강 기록의 기준 UTC 시각을 반환한다."""
    return NOW


@pytest.mark.parametrize("app_env", ["dev", "prod"])
async def test_missing_engine_fails_before_startup_tasks(app_env):
    factory = Mock()
    settings = Settings(_env_file=None, app_env=app_env)
    with pytest.raises(RuntimeError, match="앱 엔진"):
        default_agent(settings, factory)
    cleanup = AsyncMock()
    async with asyncio.timeout(1):
        with pytest.raises(RuntimeError, match="앱 엔진"):
            async with coaching_lifespan(FastAPI(), settings, factory, cleanup=cleanup):
                pytest.fail("engine 없는 dev·prod는 시작하면 안 된다")
    factory.assert_not_called()
    cleanup.assert_not_awaited()


async def test_lifespan_passes_explicit_engine_without_opening_db():
    engine = Mock()
    factory = Mock()
    async with coaching_lifespan(
        FastAPI(),
        Settings(_env_file=None, app_env="dev"),
        factory,
        engine=engine,
        cleanup=AsyncMock(return_value=0),
    ) as runner:
        port = runner._agent._port_factory(str(USER), NOW)
        assert port.engine is engine
        assert port.session_factory is factory
        engine.connect.assert_not_called()
        factory.assert_not_called()


class WiredAgentHarness:
    """주어진 에이전트를 합성 코칭 저장소·세션·요청에 연결한다."""

    def __init__(
        self, agent: CoachAgent, *, clock: Callable[[], datetime] = fixed_now
    ) -> None:
        """코칭 저장을 DB에서 분리하고 지정 시각으로 실행기를 준비한다."""
        self.agent = agent
        self.clock = clock
        self.store = FakeStore()
        self.factory = FakeSessionFactory()
        self.runner = CoachingRunner(
            session_factory=self.factory,
            agent=agent,
            store=self.store,
            clock=clock,
            finalize_timeout=0.1,
            interrupt_timeout=0.1,
        )

    def job(
        self,
        user_id: UUID,
        *,
        question: str = "지난 7일 걸음 기록을 알려 줘",
        conversation_id: UUID | None = None,
    ) -> tuple[GenerationJob, RecordingChannel]:
        """합성 회원의 새 요청과 독립 이벤트 채널을 지정 시각에 생성한다."""
        state = self.store.seed_accepted(
            user_id=user_id,
            conversation_id=conversation_id,
            content=question,
            now=self.clock(),
        )
        channel = RecordingChannel()
        return GenerationJob(user_id, state, question, channel, 0, 0.01), channel


@asynccontextmanager
async def wired_runner(
    agent: CoachAgent, *, clock: Callable[[], datetime] = fixed_now
) -> AsyncIterator[WiredAgentHarness]:
    """에이전트를 준비하고 검증 종료 시 실행기와 에이전트 순서로 자원을 닫는다."""
    harness = WiredAgentHarness(agent, clock=clock)
    try:
        assert harness.runner.is_ready()
        yield harness
    finally:
        try:
            await harness.runner.aclose(timeout=0.1)
        finally:
            await agent.aclose()


@bounded_test
async def test_default_agent_db_connection_refused_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """주입한 DB 팩토리의 접속 거부 → 한 번 조회하고 steps 조회 실패로 변환한다."""
    factory = Mock(side_effect=ConnectionRefusedError)
    handler = ScriptedHTTP([])
    monkeypatch.setattr(health_service, "_now", health_now)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        wire_mock_llm(monkeypatch, client)
        agent = default_agent(
            Settings(_env_file=None, app_env="dev"), factory, engine=Mock()
        )
        try:
            assert agent._port_factory is not None
            port = agent._port_factory(str(USER), NOW)
            with pytest.raises(HealthDataUnavailableError) as caught:
                await port.get_metric_result(str(USER), "steps", START, TODAY)
            assert caught.value.metrics == ("steps",)
            factory.assert_called_once_with()
            assert not handler.bodies
        finally:
            await agent.aclose()


@bounded_test
async def test_coaching_lifespan_dev_without_db_closes_runner_then_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dev 기본 조립으로 앱 진입·종료 → DB를 열지 않고 실행기 다음 에이전트를 닫는다."""
    app = FastAPI()
    factory = FakeSessionFactory()
    cleanup = AsyncMock(return_value=0)
    client = AsyncMock()
    wire_mock_llm(monkeypatch, client)
    monkeypatch.setattr(agent_module, "create_chat_model", Mock(return_value=MODEL))
    closed: list[str] = []
    async with bounded_lifespan(
        coaching_lifespan(
            app,
            Settings(_env_file=None, app_env="dev"),
            factory,
            engine=Mock(),
            cleanup=cleanup,
        )
    ) as runner:
        assert app.state.coaching_runner is runner
        assert app.state.coaching_runner.is_ready() is True
        assert factory.opened == 0
        close_runner = runner.aclose
        close_agent = runner._agent.aclose

        async def record_runner_close() -> None:
            """실제 실행기의 종료가 끝난 순서를 기록한다."""
            await close_runner()
            closed.append("runner")

        async def record_agent_close() -> None:
            """실제 에이전트의 종료가 끝난 순서를 기록한다."""
            await close_agent()
            closed.append("agent")

        monkeypatch.setattr(runner, "aclose", record_runner_close)
        monkeypatch.setattr(runner._agent, "aclose", record_agent_close)
        await asyncio.sleep(0)
        cleanup.assert_awaited_once()
    assert closed == ["runner", "agent"]
    assert factory.opened == factory.active == 0
    client.aclose.assert_awaited_once()


@bounded_test
async def test_coaching_lifespan_dev_port_queries_lifespan_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dev lifespan이 만든 포트로 조회 → lifespan에 넘긴 세션 팩토리를 한 번 쓰고 조회 실패로 바꾼다."""
    app = FastAPI()
    factory = Mock(side_effect=ConnectionRefusedError)
    client = AsyncMock()
    wire_mock_llm(monkeypatch, client)
    monkeypatch.setattr(agent_module, "create_chat_model", Mock(return_value=MODEL))
    async with bounded_lifespan(
        coaching_lifespan(
            app,
            Settings(_env_file=None, app_env="dev"),
            factory,
            engine=Mock(),
            cleanup=AsyncMock(return_value=0),
        )
    ) as runner:
        factory.assert_not_called()
        monkeypatch.setattr(health_service, "_now", health_now)
        assert runner._agent._port_factory is not None
        port = runner._agent._port_factory(str(USER), NOW)
        with pytest.raises(HealthDataUnavailableError) as caught:
            await port.get_metric_result(str(USER), "steps", START, TODAY)
        assert caught.value.metrics == ("steps",)
        factory.assert_called_once_with()


async def assert_wired_failure(
    outcome: health_errors.HealthDataError | HealthMetricResult,
    *,
    code: str,
    message: str,
    retryable: bool,
    reason: str | None = None,
) -> None:
    """계측을 격리한 실제 에이전트의 실패 문구·이벤트·저장·재조회 문맥을 검사한다."""
    handler = ScriptedHTTP(
        [httpx.Response(200, content=tool_body(), headers=SSE_HEADERS)]
    )
    lookup = FakeLookup(outcome)
    port = HealthQueryPort(object(), lookup=lookup)

    def shared_port(user_id: str, now: datetime) -> HealthDataPort:
        """요청마다 같은 가짜 조회 함수가 연결된 건강 포트를 반환한다."""
        return port

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        agent = CoachAgent(
            model_factory=CountingModelFactory(
                create_chat_model(
                    synthetic_ai_settings(),
                    http_client=client,
                    recorder=MetricsRecorder(),
                )
            ),
            port_factory=shared_port,
            close=client.aclose,
        )
        async with wired_runner(agent) as harness:
            job, channel = harness.job(USER)
            await harness.runner.submit(job)
            assert [event["type"] for event in channel.events] == [
                "status",
                "status",
                "status",
                "error",
            ]
            assert [event["data"]["stage"] for event in channel.events[:-1]] == [
                "reading_history",
                "generating_answer",
                "fetching_health_data",
            ]
            expected: dict[str, Any] = {
                "code": code,
                "message": message,
                "retryable": retryable,
                "accepted": True,
            }
            context = None
            if reason is not None:
                context = {"reason": reason, "metrics": ["steps"]}
                expected.update(context)
            assert channel.events[-1]["data"] == expected
            if reason is None:
                assert "reason" not in channel.events[-1]["data"]
                assert "metrics" not in channel.events[-1]["data"]
            state = harness.store.state(job.request.id)
            assert state.status == "failed"
            assert state.error_code == code
            assert state.error_context == context
            assert state.assistant_message.content == ""
            for restored in (
                request_state_data(state).model_dump(mode="json"),
                request_snapshot_event(state)["data"],
            ):
                assert restored["errorCode"] == code
                assert restored["errorContext"] == context
                assert restored["assistantMessage"]["content"] == ""
            assert len(handler.bodies) == 1
            assert len(lookup.calls) == 1
            assert harness.factory.active == 0


@bounded_test
async def test_wired_port_data_not_ready_saves_unavailable() -> None:
    """집계 준비 전 → DATA_NOT_READY 오류만 보내고 실패 사유를 저장한다."""
    await assert_wired_failure(
        health_errors.HealthDataNotReadyError(("steps",)),
        code="HEALTH_DATA_UNAVAILABLE",
        message="걸음 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        retryable=True,
        reason="DATA_NOT_READY",
    )


@bounded_test
async def test_wired_port_lookup_failed_saves_unavailable() -> None:
    """건강 조회 실패 → LOOKUP_FAILED 오류만 보내고 실패 사유를 저장한다."""
    await assert_wired_failure(
        health_errors.HealthDataLookupError(("steps",)),
        code="HEALTH_DATA_UNAVAILABLE",
        message="건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        retryable=True,
        reason="LOOKUP_FAILED",
    )


@bounded_test
async def test_wired_port_access_denied_saves_forbidden() -> None:
    """건강 조회 권한 거부 → reason·metrics 없는 오류와 실패 상태를 저장한다."""
    await assert_wired_failure(
        health_errors.HealthDataForbiddenError(("steps",)),
        code="HEALTH_DATA_FORBIDDEN",
        message="건강데이터 조회 권한을 확인해 주세요.",
        retryable=False,
    )


@bounded_test
async def test_wired_port_other_owner_blocks_tool_result_and_saves_forbidden() -> None:
    """다른 회원 소유의 결과 → LLM에 도구 결과를 보내지 않고 권한 거부로 저장한다."""
    other = calculate(steps()).model_copy(
        update={"user_id": UUID("00000000-0000-4000-8000-000000000002")}
    )
    await assert_wired_failure(
        other,
        code="HEALTH_DATA_FORBIDDEN",
        message="건강데이터 조회 권한을 확인해 주세요.",
        retryable=False,
    )
