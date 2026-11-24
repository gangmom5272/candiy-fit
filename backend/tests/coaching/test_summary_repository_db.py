"""전용 PostgreSQL에서 요약 스냅샷·권한·경쟁·원문 보존을 확인한다."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.history import HistorySnapshot, SummaryPlan, SummaryResult
from app.coaching.models import CoachingConversation, CoachingMessage, CoachingRequest
from app.coaching.runner import CoachingRunner, GenerationJob
from app.coaching.summary import SummaryManager
from app.coaching.summary_adapter import default_summary_functions
from app.core.db import create_sessionmaker
from app.users.models import User
from coach_ai.agent.context import CoachContext
from coach_ai.history.types import StoredSummary
from tests.coaching.fakes import (
    NOW,
    CountingModelFactory,
    RecordingChannel,
    RecordingPortFactory,
    ScriptedRunCoach,
    fixed_now,
)
from tests.coaching.summary_fakes import SyntheticSummary
from tests.coaching.test_coaching_agent import SSE_HEADERS, text_body
from tests.coaching.test_coaching_repository_db import database_url, member
from tests.coaching.test_summary_activation import (
    SUMMARY_TEXT,
    real_summary_agent,
    summary_response,
)

pytestmark = pytest.mark.db


@pytest.fixture
async def summary_db() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID]]
]:
    """Alembic 전용 DB에 합성 회원을 commit하고 이 테스트 행만 정리한다."""
    engine = create_async_engine(database_url())
    factory = create_sessionmaker(engine)
    users = (member(), member())
    owners = (users[0].id, users[1].id)
    async with factory.begin() as session:
        session.add_all(users)
    try:
        yield factory, owners
    finally:
        async with factory.begin() as session:
            conversations = select(CoachingConversation.id).where(
                CoachingConversation.user_id.in_(owners)
            )
            await session.execute(
                delete(CoachingMessage).where(
                    CoachingMessage.conversation_id.in_(conversations)
                )
            )
            await session.execute(
                delete(CoachingRequest).where(CoachingRequest.user_id.in_(owners))
            )
            await session.execute(
                delete(CoachingConversation).where(
                    CoachingConversation.user_id.in_(owners)
                )
            )
            await session.execute(delete(User).where(User.id.in_(owners)))
        await engine.dispose()


async def pair(
    session: AsyncSession,
    owner: UUID,
    conversation_id: UUID | None = None,
    *,
    status: str = "completed",
) -> repository.RequestState:
    """실제 접수·종결 함수로 정상·실패·진행 합성 쌍을 만든다."""
    accepted = await repository.accept(
        session,
        user_id=owner,
        client_request_id=uuid4(),
        conversation_id=conversation_id,
        content="합성 질문",
        now=NOW,
    )
    assert isinstance(accepted, repository.Accepted)
    if status == "completed":
        await repository.complete(
            session, request_id=accepted.request.id, answer="합성 정상 답변", now=NOW
        )
    elif status == "failed":
        await repository.fail(
            session,
            request_id=accepted.request.id,
            error_code="AI_UNAVAILABLE",
            error_context=None,
            partial_answer="합성 실패 부분 답변",
            now=NOW,
        )
    return accepted.request


def summary_values(snapshot: HistorySnapshot) -> tuple[SummaryPlan, SummaryResult]:
    """계수 정책 없이 받은 합성 완료 쌍을 모두 포함하는 고정 계획·결과를 만든다."""
    plan = SummaryPlan(
        snapshot.turns, snapshot.turns[-1].answer_seq, 2500, "synthetic-v1"
    )
    return plan, SummaryResult(
        "합성 저장 요약", plan.until_seq, 10, plan.policy_version
    )


async def snapshot(
    session: AsyncSession, owner: UUID, conversation_id: UUID, cutoff: int | None = None
) -> HistorySnapshot:
    """소유권 있는 대화의 스냅샷이 존재함을 확인해 반환한다."""
    result = await repository.find_history_context(
        session,
        user_id=owner,
        conversation_id=conversation_id,
        before_sequence_no=cutoff,
    )
    assert result is not None
    return result


async def save(
    session: AsyncSession, owner: UUID, conversation_id: UUID, source: HistorySnapshot
) -> bool:
    """스냅샷의 고정 합성 계획을 저장하되 commit은 테스트가 수행한다."""
    plan, result = summary_values(source)
    return await repository.save_summary(
        session,
        user_id=owner,
        conversation_id=conversation_id,
        snapshot=source,
        plan=plan,
        result=result,
        now=NOW,
    )


async def test_snapshot_owns_cutoff_pairs_and_one_select(summary_db) -> None:
    """한 SELECT에서 실패·현재 쌍을 빼고 식별자·번호·소유권을 보존한다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
        await pair(session, owners[0], first.conversation_id, status="failed")
        last = await pair(session, owners[0], first.conversation_id)
        pending = await pair(
            session, owners[0], first.conversation_id, status="accepted"
        )
    statements: list[str] = []

    def observe(connection, cursor, statement, parameters, context, many) -> None:
        """실행한 SQL 개수만 관측하며 파라미터나 원문을 기록하지 않는다."""
        statements.append(statement)

    engine = factory.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        async with factory() as session:
            found = await snapshot(
                session,
                owners[0],
                first.conversation_id,
                pending.user_message.sequence_no,
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    assert len(statements) == 1
    assert [(turn.question_seq, turn.answer_seq) for turn in found.turns] == [
        (1, 2),
        (5, 6),
    ]
    assert found.turns[0].request_id == str(first.id)
    assert found.turns[-1].answer_message_id == str(last.assistant_message.id)
    assert found.pending_sequence_no is None
    async with factory() as session:
        current = await snapshot(session, owners[0], first.conversation_id)
        assert current.pending_sequence_no == 7
        assert (
            await repository.find_history_context(
                session, user_id=owners[1], conversation_id=first.conversation_id
            )
            is None
        )
        assert (
            await repository.find_history_context(
                session, user_id=owners[0], conversation_id=uuid4()
            )
            is None
        )


async def test_save_preserves_originals_and_next_context_excludes_summarized_pairs(
    summary_db,
) -> None:
    """NULL 기준점 최초 갱신은 세 컬럼을 함께 바꾸고 원문과 최신 쌍은 유지한다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
        await pair(session, owners[0], first.conversation_id, status="failed")
        last = await pair(session, owners[0], first.conversation_id)
    async with factory() as session:
        original = (
            await session.execute(
                select(CoachingMessage.id, CoachingMessage.content).where(
                    CoachingMessage.conversation_id == first.conversation_id
                )
            )
        ).all()
        source = await snapshot(
            session, owners[0], first.conversation_id, last.user_message.sequence_no
        )
        assert await save(session, owners[0], first.conversation_id, source)
        await session.commit()
    async with factory() as session:
        current = await snapshot(session, owners[0], first.conversation_id)
        assert current.summary.until_seq == 2
        assert [turn.answer_seq for turn in current.turns] == [6]
        conversation = await session.get(CoachingConversation, first.conversation_id)
        assert conversation.summary_updated_at == NOW
        assert conversation.summary == "합성 저장 요약"
        assert (
            await session.execute(
                select(CoachingMessage.id, CoachingMessage.content).where(
                    CoachingMessage.conversation_id == first.conversation_id
                )
            )
        ).all() == original


async def test_summary_rollback_and_other_owner_do_not_write(summary_db) -> None:
    """호출자가 rollback하면 요약도 사라지고 타 사용자 저장은 항상 0건이다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
    async with factory() as session:
        source = await snapshot(session, owners[0], first.conversation_id)
        assert not await save(session, owners[1], first.conversation_id, source)
        assert await save(session, owners[0], first.conversation_id, source)
        await session.rollback()
    async with factory() as session:
        current = await snapshot(session, owners[0], first.conversation_id)
        assert current.summary is None
        assert current.turns == source.turns


async def test_concurrent_null_base_updates_have_one_winner(summary_db) -> None:
    """같은 NULL 기준점의 동시 저장은 행 잠금 뒤 한 후보만 반영된다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
    async with factory() as first_session, factory() as second_session:
        source = await snapshot(first_session, owners[0], first.conversation_id)
        assert await save(first_session, owners[0], first.conversation_id, source)
        waiting = asyncio.create_task(
            save(second_session, owners[0], first.conversation_id, source)
        )
        try:
            await asyncio.sleep(0.02)
            assert not waiting.done()
            await first_session.commit()
            assert await asyncio.wait_for(waiting, 2) is False
            await second_session.commit()
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)


async def test_advanced_base_beyond_old_cutoff_rejects_stale_candidate(
    summary_db,
) -> None:
    """경쟁 요약이 이전 조회 상한보다 앞서도 예외 없이 오래된 저장을 거부한다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
        old = await snapshot(session, owners[0], first.conversation_id)
        await pair(session, owners[0], first.conversation_id)
        latest = await snapshot(session, owners[0], first.conversation_id)
        assert await save(session, owners[0], first.conversation_id, latest)
    async with factory.begin() as session:
        assert not await save(session, owners[0], first.conversation_id, old)
        assert (
            await snapshot(session, owners[0], first.conversation_id)
        ).summary.until_seq == 4


@pytest.mark.parametrize("change", ["content", "pending", "answered_on"])
async def test_save_revalidates_original_prefix_and_unfinished_barrier(
    summary_db, change: str
) -> None:
    """조회 뒤 본문·완료 날짜·실패 종결 상태가 바뀌면 고정 계획 저장을 거부한다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
        failed = await pair(session, owners[0], first.conversation_id, status="failed")
        await pair(session, owners[0], first.conversation_id)
        source = await snapshot(session, owners[0], first.conversation_id)
    async with factory.begin() as session:
        if change == "content":
            await session.execute(
                update(CoachingMessage)
                .where(CoachingMessage.id == first.user_message.id)
                .values(content="합성 변경 질문")
            )
        elif change == "answered_on":
            await session.execute(
                update(CoachingRequest)
                .where(CoachingRequest.id == first.id)
                .values(created_at=NOW - timedelta(days=1))
            )
        else:
            await session.execute(
                update(CoachingRequest)
                .where(CoachingRequest.id == failed.id)
                .values(status="generating", finished_at=None, error_code=None)
            )
    async with factory.begin() as session:
        assert not await save(session, owners[0], first.conversation_id, source)
        assert (
            await snapshot(session, owners[0], first.conversation_id)
        ).summary is None


async def test_new_completion_does_not_expand_fixed_plan(summary_db) -> None:
    """생성 중 새 완료 쌍이 추가돼도 저장 기준점은 고정 계획의 끝 번호다."""
    factory, owners = summary_db
    async with factory.begin() as session:
        first = await pair(session, owners[0])
        source = await snapshot(session, owners[0], first.conversation_id)
    async with factory.begin() as session:
        await pair(session, owners[0], first.conversation_id)
    async with factory.begin() as session:
        assert await save(session, owners[0], first.conversation_id, source)
        current = await snapshot(session, owners[0], first.conversation_id)
        assert current.summary.until_seq == 2
        assert [turn.answer_seq for turn in current.turns] == [4]


async def test_runner_summary_roundtrip_reuses_stored_summary(summary_db) -> None:
    """실제 저장소로 답변 commit→백그라운드 요약→다음 답변의 요약 재사용을 연결한다."""
    factory, owners = summary_db
    functions = SyntheticSummary()
    functions.gate = asyncio.Event()
    summaries = SummaryManager(
        session_factory=factory, functions=functions.functions(), clock=fixed_now
    )
    script = ScriptedRunCoach()
    agent = CoachAgent(
        model_factory=CountingModelFactory(),
        port_factory=RecordingPortFactory(),
        run=script,
    )
    runner = CoachingRunner(
        session_factory=factory, agent=agent, summaries=summaries, clock=fixed_now
    )
    assert runner.is_ready()
    try:
        async with factory.begin() as session:
            first = await pair(session, owners[0], status="accepted")
        channel = RecordingChannel()
        job = GenerationJob(owners[0], first, first.user_message.content, channel, 0, 0)
        await runner.submit(job)
        await asyncio.wait_for(functions.entered.wait(), 2)
        assert channel.events[-1]["type"] == "completed"
        assert script.calls[0]["ctx"].summary is None
        assert script.calls[0]["ctx"].turns == ()
        functions.gate.set()
        await asyncio.wait_for(asyncio.gather(*summaries.tasks), 2)
        async with factory.begin() as session:
            second = await pair(
                session, owners[0], first.conversation_id, status="accepted"
            )
        await runner.submit(
            replace(job, request=second, question=second.user_message.content)
        )
        assert script.calls[1]["ctx"].summary == StoredSummary(
            text="합성 누적 요약 2", until_seq=2
        )
        assert script.calls[1]["ctx"].turns == ()
        assert "summary" not in script.calls[1] and "history" not in script.calls[1]
        assert [event["type"] for event in channel.events].count("completed") == 2
    finally:
        await runner.aclose()
        await agent.aclose()


@pytest.mark.parametrize(
    "accepted_at,finished_at,expected_day,label",
    [
        (
            datetime(2026, 10, 28, 14, 59, 59, tzinfo=UTC),
            datetime(2026, 10, 28, 15, 0, 20, tzinfo=UTC),
            date(2026, 10, 28),
            "2026-10-28 (수)",
        ),
        (
            datetime(2026, 10, 28, 15, 0, tzinfo=UTC),
            datetime(2026, 10, 28, 15, 0, 30, tzinfo=UTC),
            date(2026, 10, 29),
            "2026-10-29 (목)",
        ),
        (
            datetime(2026, 10, 28, 14, 59, 50, tzinfo=UTC),
            datetime(2026, 10, 29, 0, 0, tzinfo=UTC),
            date(2026, 10, 28),
            "2026-10-28 (수)",
        ),
    ],
)
async def test_created_at_kst_reaches_real_summary_and_reuse(
    summary_db,
    monkeypatch: pytest.MonkeyPatch,
    accepted_at: datetime,
    finished_at: datetime,
    expected_day: date,
    label: str,
) -> None:
    """접수 시각의 KST 날짜가 SQL→AI 계획→실제 생성→DB 저장·재사용에 보존된다(완료 시각 무관)."""
    factory, owners = summary_db
    async with factory.begin() as session:
        accepted = await repository.accept(
            session,
            user_id=owners[0],
            client_request_id=uuid4(),
            conversation_id=None,
            content="1" * 3101,
            now=accepted_at,
        )
        assert isinstance(accepted, repository.Accepted)
        request = accepted.request
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="",
            now=finished_at,
        )
    async with factory() as session:
        source = await snapshot(session, owners[0], request.conversation_id)
        assert source.turns[0].answered_on == expected_day
        assert request.assistant_message.created_at == accepted_at
    bodies = []

    def http(incoming: httpx.Request) -> httpx.Response:
        """실제 요약 생성과 뒤따른 답변의 합성 HTTP 본문을 기록한다."""
        assert str(incoming.url) == "https://llm.test/v1/chat/completions"
        body = json.loads(incoming.content)
        bodies.append(body)
        if body["stream"] is False:
            return summary_response()
        return httpx.Response(
            200, content=text_body("합성 다음 답변"), headers=SSE_HEADERS
        )

    async with real_summary_agent(monkeypatch, http) as agent:
        manager = SummaryManager(
            session_factory=factory,
            functions=default_summary_functions(model_provider=agent.get_model),
        )
        try:
            await manager.submit(
                user_id=owners[0], conversation_id=request.conversation_id
            )
            async with factory() as session:
                current = await snapshot(session, owners[0], request.conversation_id)
                assert current.summary.text == SUMMARY_TEXT
                assert current.summary.until_seq == 2 and current.turns == ()
                saved = await session.get(CoachingConversation, request.conversation_id)
                assert (
                    saved.updated_at == finished_at
                    and saved.summary_updated_at is not None
                )
                assert (
                    await session.get(CoachingMessage, request.user_message.id)
                ).content == "1" * 3101
            result = await agent.run_with_history(
                ctx=CoachContext(
                    user_id=str(owners[0]),
                    conversation_id=str(request.conversation_id),
                    request_id=str(uuid4()),
                ),
                question="합성 다음 질문",
                history=current,
                sink=AsyncMock(),
                now=accepted_at,
                message_id=str(uuid4()),
            )
            assert result.finish_reason == "stop"
            assert result.history_usage.summary_used is True
            assert len(bodies) == 2
            assert f"[질문 1] {label}" in bodies[0]["messages"][1]["content"]
            assert SUMMARY_TEXT in bodies[1]["messages"][0]["content"]
        finally:
            await manager.aclose()
