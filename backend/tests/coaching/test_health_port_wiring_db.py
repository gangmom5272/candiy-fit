"""합성 전용 DB와 모의 LLM으로 기본 코칭 에이전트의 건강 조회 연결을 검증한다."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.coaching.agent import default_agent
from app.coaching.health_port import HealthQueryPort
from app.coaching.runner import GenerationJob
from app.core.config import Settings
from app.health import service as health_service
from coach_ai.config import Settings as AISettings
from tests.coaching.fakes import RecordingChannel
from tests.coaching.test_coaching_agent import (
    SSE_HEADERS,
    ScriptedHTTP,
    text_body,
    tool_body,
)
from tests.coaching.test_health_port_wiring import (
    WiredAgentHarness,
    health_now,
    wire_mock_llm,
    wired_runner,
)
from tests.health.test_calculations import NOW
from tests.health.test_service_db import seed

pytestmark = pytest.mark.db
HealthDB = tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID]]
HealthSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


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


@pytest.fixture(autouse=True)
def fixed_health_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """건강 조회 서비스의 현재 시각을 합성 기록의 기준 시각으로 고정한다."""
    monkeypatch.setattr(health_service, "_now", health_now)


@asynccontextmanager
async def db_wired_runner(
    monkeypatch: pytest.MonkeyPatch,
    factory: HealthSessionFactory,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    engine: AsyncEngine,
    concurrency: int = 1,
) -> AsyncIterator[WiredAgentHarness]:
    """실제 기본 에이전트에 건강 DB 팩토리와 모의 HTTP·코칭 저장소를 연결한다."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        wire_mock_llm(monkeypatch, client, concurrency=concurrency)
        agent = default_agent(
            Settings(_env_file=None, app_env="dev"),
            cast(async_sessionmaker[AsyncSession], factory),
            engine=engine,
        )
        async with wired_runner(agent, clock=health_now) as harness:
            yield harness


def assert_tool_average(body: dict[str, Any]) -> None:
    """LLM에 전달한 마지막 도구 메시지의 평균을 seed와 독립적인 기대값으로 검사한다."""
    message = body["messages"][-1]
    assert message["role"] == "tool"
    # seed의 10-16 7980, 10-17 6450, 10-19 8120, 10-21 7310의 평균이다.
    # 10-18은 0보, 10-20은 기록 없음, 10-22는 오늘 진행 중이므로 평균에서 제외한다.
    assert json.loads(message["content"])["result"]["avg"]["display"] == "7,465걸음"


def assert_lookup_failed(
    harness: WiredAgentHarness, job: GenerationJob, channel: RecordingChannel
) -> None:
    """조회 실패의 공개 순서·리터럴 메시지·접수 여부와 저장된 실패 사유를 검사한다."""
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
    assert channel.events[-1]["data"] == {
        "code": "HEALTH_DATA_UNAVAILABLE",
        "message": "건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "retryable": True,
        "accepted": True,
        "reason": "LOOKUP_FAILED",
        "metrics": ["steps"],
    }
    state = harness.store.state(job.request.id)
    assert state.status == "failed"
    assert state.error_code == "HEALTH_DATA_UNAVAILABLE"
    assert state.error_context == {"reason": "LOOKUP_FAILED", "metrics": ["steps"]}
    assert state.assistant_message.content == ""


async def test_wired_port_seeded_member_completes_with_db_summary(
    health_db: HealthDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """연동된 합성 회원의 질문 → DB 걸음 평균을 LLM에 전달하고 완료를 저장한다."""
    async with asyncio.timeout(1.9):
        factory, (owner, _) = health_db
        await seed(factory, owner)
        handler = ScriptedHTTP(
            [
                httpx.Response(200, content=tool_body(), headers=SSE_HEADERS),
                httpx.Response(
                    200, content=text_body("합성 걸음 답변"), headers=SSE_HEADERS
                ),
            ]
        )
        async with db_wired_runner(
            monkeypatch, factory, handler, engine=factory.kw["bind"]
        ) as harness:
            job, channel = harness.job(owner)
            await harness.runner.submit(job)
            assert "fetching_health_data" in [
                event["data"]["stage"]
                for event in channel.events
                if event["type"] == "status"
            ]
            assert channel.events[-1]["type"] == "completed"
            assert harness.store.state(job.request.id).status == "completed"
            assert len(handler.bodies) == 2
            assert_tool_average(handler.bodies[1])
            assert harness.factory.active == 0


async def test_wired_port_member_without_sync_state_saves_not_ready(
    health_db: HealthDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상태 행 없는 회원의 질문 → DATA_NOT_READY error를 보내고 실패 사유를 저장한다."""
    async with asyncio.timeout(1.9):
        factory, (seeded_owner, owner) = health_db
        await seed(factory, seeded_owner)
        handler = ScriptedHTTP(
            [httpx.Response(200, content=tool_body(), headers=SSE_HEADERS)]
        )
        async with db_wired_runner(
            monkeypatch, factory, handler, engine=factory.kw["bind"]
        ) as harness:
            job, channel = harness.job(owner)
            await harness.runner.submit(job)
            event = channel.events[-1]
            assert event["type"] == "error"
            assert event["data"]["reason"] == "DATA_NOT_READY"
            assert event["data"]["metrics"] == ["steps"]
            saved = harness.store.state(job.request.id)
            assert saved.status == "failed"
            assert saved.error_context == {
                "reason": "DATA_NOT_READY",
                "metrics": ["steps"],
            }
            assert saved.assistant_message.content == ""
            assert len(handler.bodies) == 1


async def test_wired_port_concurrent_members_use_distinct_sessions_and_complete(
    health_db: HealthDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 합성 회원의 동시 질문 → 같은 포트에서 서로 다른 세션을 열고 모두 완료한다."""
    async with asyncio.timeout(1.9):
        factory, owners = health_db
        for owner in owners:
            await seed(factory, owner)
        sessions: list[AsyncSession] = []
        both_open = asyncio.Event()
        bodies: list[dict[str, Any]] = []

        @asynccontextmanager
        async def record_sessions() -> AsyncIterator[AsyncSession]:
            """서로 겹쳐 열린 건강 조회 세션을 기록하고 두 번째 진입 뒤 조회를 진행한다."""
            async with factory() as session:
                sessions.append(session)
                if len(sessions) >= 2:
                    both_open.set()
                await both_open.wait()
                yield session

        def respond_by_message(request: httpx.Request) -> httpx.Response:
            """요청 순서와 무관하게 마지막 메시지가 도구 결과면 답변, 아니면 도구를 반환한다."""
            assert str(request.url) == "https://llm.test/v1/chat/completions"
            body = json.loads(request.content)
            bodies.append(body)
            content = (
                text_body("합성 동시 질문 답변")
                if body["messages"][-1]["role"] == "tool"
                else tool_body()
            )
            return httpx.Response(200, content=content, headers=SSE_HEADERS)

        async with db_wired_runner(
            monkeypatch,
            record_sessions,
            respond_by_message,
            engine=factory.kw["bind"],
            concurrency=2,
        ) as harness:
            port_factory = harness.agent._port_factory
            assert port_factory is not None
            shared = port_factory(str(owners[0]), NOW)
            assert isinstance(shared, HealthQueryPort)
            assert port_factory(str(owners[1]), NOW) is shared
            pairs = [
                harness.job(owner, question=f"합성 질문 {index}: 지난 7일 걸음 기록")
                for index, owner in enumerate(owners)
            ]
            await asyncio.gather(*(harness.runner.submit(job) for job, _ in pairs))
            assert both_open.is_set()
            assert len(sessions) >= 2
            assert len({id(session) for session in sessions}) == len(sessions)
            assert len(bodies) == 4
            for job, channel in pairs:
                assert channel.events[-1]["type"] == "completed"
                assert harness.store.state(job.request.id).status == "completed"
                job_bodies = [
                    body
                    for body in bodies
                    if any(
                        message["role"] == "user" and message["content"] == job.question
                        for message in body["messages"]
                    )
                ]
                assert len(job_bodies) == 2
                assert_tool_average(job_bodies[1])
            assert harness.factory.active == 0


async def test_wired_port_db_recovers_next_question_completes(
    health_db: HealthDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    """첫 질문의 DB 접속 거부 뒤 복구 → 같은 회원의 다음 질문은 같은 포트에서 완료한다."""
    async with asyncio.timeout(1.9):
        factory, (owner, _) = health_db
        await seed(factory, owner)
        calls = 0

        def recovering_factory() -> AsyncSession:
            """첫 건강 조회만 접속 거부로 끝내고 다음 호출은 실제 새 세션을 반환한다."""
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionRefusedError
            return factory()

        handler = ScriptedHTTP(
            [
                httpx.Response(200, content=tool_body(), headers=SSE_HEADERS),
                httpx.Response(200, content=tool_body(), headers=SSE_HEADERS),
                httpx.Response(
                    200, content=text_body("합성 복구 답변"), headers=SSE_HEADERS
                ),
            ]
        )
        async with db_wired_runner(
            monkeypatch, recovering_factory, handler, engine=factory.kw["bind"]
        ) as harness:
            assert calls == 0
            port_factory = harness.agent._port_factory
            assert port_factory is not None
            shared = port_factory(str(owner), NOW)
            first, failed_channel = harness.job(owner)
            await harness.runner.submit(first)
            assert_lookup_failed(harness, first, failed_channel)
            assert len(handler.bodies) == 1
            assert calls == 1
            second, completed_channel = harness.job(
                owner, conversation_id=first.request.conversation_id
            )
            await harness.runner.submit(second)
            assert port_factory(str(owner), NOW) is shared
            assert completed_channel.events[-1]["type"] == "completed"
            assert harness.store.state(second.request.id).status == "completed"
            assert harness.store.state(first.request.id).status == "failed"
            assert len(handler.bodies) == 3
            assert_tool_average(handler.bodies[2])
            assert calls == 2
            assert harness.factory.active == 0
