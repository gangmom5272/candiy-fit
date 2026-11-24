"""전용 PostgreSQL에서 실제 commit으로 기동 정리와 종료 중단 저장을 검사한다."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.lifecycle import interrupt_stale_requests
from app.coaching.models import CoachingConversation, CoachingMessage, CoachingRequest
from app.coaching.runner import CoachingRunner, GenerationJob
from app.coaching.schemas import request_state_data
from app.users.models import User
from tests.coaching.fakes import (
    NOW,
    CountingModelFactory,
    RecordingChannel,
    RecordingPortFactory,
    ScriptedRunCoach,
    fixed_now,
)
from tests.coaching.test_coaching_flow_db import database_url, member

pytestmark = pytest.mark.db
BEFORE = NOW - timedelta(seconds=10)
LATER = NOW + timedelta(seconds=10)
PARTIAL = "합성 DB 중단 부분 답변"


@pytest.fixture
async def committed_member() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], UUID]
]:
    """허용된 전용 DB에 합성 회원을 commit하고 finally에서 관련 행을 모두 정리한다."""
    engine = create_async_engine(database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = member()
    user_id = user.id
    try:
        async with sessions() as session:
            session.add(user)
            await session.commit()
        yield sessions, user_id
    finally:
        try:
            async with engine.begin() as connection:
                conversations = select(CoachingConversation.id).where(
                    CoachingConversation.user_id == user_id
                )
                await connection.execute(
                    delete(CoachingMessage).where(
                        CoachingMessage.conversation_id.in_(conversations)
                    )
                )
                await connection.execute(
                    delete(CoachingRequest).where(CoachingRequest.user_id == user_id)
                )
                await connection.execute(
                    delete(CoachingConversation).where(
                        CoachingConversation.user_id == user_id
                    )
                )
                await connection.execute(delete(User).where(User.id == user_id))
        finally:
            await engine.dispose()


async def accept_question(
    session: AsyncSession, user_id: UUID, *, now: datetime
) -> repository.Accepted:
    """UUIDv4와 합성 질문으로 독립 대화를 접수하고 commit은 호출자에게 맡긴다."""
    result = await repository.accept(
        session,
        user_id=user_id,
        client_request_id=uuid4(),
        conversation_id=None,
        content="합성 정리 검증 질문",
        now=now,
    )
    assert isinstance(result, repository.Accepted)
    return result


async def test_startup_cleanup_commits_only_old_in_progress_requests(
    committed_member,
) -> None:
    """B1 이전 진행 두 건 정리 → 실제 commit·대화 시각을 확인하고 새 요청·종결 상태를 보존한다."""
    sessions, user_id = committed_member
    async with asyncio.timeout(10):
        async with sessions() as session:
            accepted = await accept_question(session, user_id, now=BEFORE)
            generating = await accept_question(session, user_id, now=BEFORE)
            await repository.mark_generating(session, request_id=generating.request.id)
            await session.execute(
                update(CoachingMessage)
                .where(CoachingMessage.id == generating.request.assistant_message.id)
                .values(content=PARTIAL)
            )
            newer = await accept_question(
                session, user_id, now=NOW + timedelta(seconds=1)
            )
            completed = await accept_question(session, user_id, now=BEFORE)
            await repository.complete(
                session, request_id=completed.request.id, answer="합성 완료", now=BEFORE
            )
            failed = await accept_question(session, user_id, now=BEFORE)
            await repository.fail(
                session,
                request_id=failed.request.id,
                error_code="GENERATION_TIMEOUT",
                error_context=None,
                partial_answer="합성 기존 실패",
                now=BEFORE,
            )
            await session.commit()
        async with sessions() as session:
            unchanged = [
                await repository.get_request_state(
                    session, user_id=user_id, request_id=item.request.id
                )
                for item in (newer, completed, failed)
            ]
        assert (
            await interrupt_stale_requests(
                sessions, created_before=NOW, now=LATER, timeout=3
            )
            == 2
        )
        async with sessions() as session:
            for item, answer in ((accepted, ""), (generating, PARTIAL)):
                state = await repository.get_request_state(
                    session, user_id=user_id, request_id=item.request.id
                )
                assert state.status == "failed"
                assert state.error_code == "GENERATION_INTERRUPTED"
                assert state.error_context is None
                assert state.finished_at == LATER
                assert state.assistant_message.content == answer
                assert (
                    await session.scalar(
                        select(CoachingConversation.updated_at).where(
                            CoachingConversation.id == item.conversation.id
                        )
                    )
                    == LATER
                )
            for item, expected in zip(
                (newer, completed, failed), unchanged, strict=True
            ):
                assert (
                    await repository.get_request_state(
                        session, user_id=user_id, request_id=item.request.id
                    )
                    == expected
                )
                assert (
                    await session.scalar(
                        select(CoachingConversation.updated_at).where(
                            CoachingConversation.id == item.conversation.id
                        )
                    )
                    == item.conversation.updated_at
                )
            result = await repository.accept(
                session,
                user_id=user_id,
                client_request_id=uuid4(),
                conversation_id=accepted.conversation.id,
                content="정리 후 새 합성 질문",
                now=LATER,
            )
            assert isinstance(result, repository.Accepted)
            await session.commit()
        assert (
            await interrupt_stale_requests(
                sessions, created_before=NOW, now=LATER, timeout=3
            )
            == 0
        )


async def test_shutdown_commits_interrupted_partial_answer(committed_member) -> None:
    """B2 실제 DB 생성 중 종료 → 부분 답변·중단 오류·종결 시각을 commit하고 failed로 조회한다."""
    sessions, user_id = committed_member
    run = ScriptedRunCoach([{"type": "token", "delta": PARTIAL}], forever=True)
    agent = CoachAgent(
        model_factory=CountingModelFactory(),
        port_factory=RecordingPortFactory(),
        run=run,
    )
    runner = CoachingRunner(session_factory=sessions, agent=agent, clock=fixed_now)
    channel = RecordingChannel()
    try:
        async with asyncio.timeout(10):
            async with sessions() as session:
                accepted = await accept_question(session, user_id, now=BEFORE)
                await session.commit()
            assert runner.is_ready()
            job = GenerationJob(user_id, accepted.request, "합성 질문", channel, 0, 0)
            task = runner.submit(job)
            await run.entered.wait()
            await runner.aclose(timeout=3.5)
            assert run.cancelled and task.cancelled()
            assert runner.active_count == 0
            assert not runner._saves
            async with sessions() as session:
                state = await repository.get_request_state(
                    session, user_id=user_id, request_id=accepted.request.id
                )
            assert state.status == "failed"
            assert state.error_code == "GENERATION_INTERRUPTED"
            assert state.assistant_message.content == PARTIAL
            assert state.finished_at == NOW
            assert request_state_data(state).status == "failed"
            assert channel.events[-1]["type"] == "error"
            assert channel.events[-1]["data"]["code"] == "GENERATION_INTERRUPTED"
            assert channel.events[-1]["data"]["accepted"] is True
            assert channel.events[-1]["data"]["retryable"] is True
    finally:
        try:
            await runner.aclose(timeout=3.5)
        finally:
            await agent.aclose()


async def state_of(sessions, user_id: UUID, item: repository.Accepted):
    """새 세션으로 요청의 저장 상태를 읽어 반환한다."""
    async with sessions() as session:
        return await repository.get_request_state(
            session, user_id=user_id, request_id=item.request.id
        )


async def conversation_updated_at(sessions, item: repository.Accepted) -> datetime:
    """새 세션으로 대화의 updated_at을 읽어 반환한다."""
    async with sessions() as session:
        return await session.scalar(
            select(CoachingConversation.updated_at).where(
                CoachingConversation.id == item.conversation.id
            )
        )


async def test_fail_expired_requests_closes_only_expired_in_progress(
    committed_member,
) -> None:
    """S5·S6·S7 기한 지난 accepted·generating만 TIMEOUT으로 닫고 경계 같음·미래·종결 요청은 보존한다."""
    sessions, user_id = committed_member
    async with asyncio.timeout(10):
        async with sessions() as session:
            accepted = await accept_question(session, user_id, now=BEFORE)
            generating = await accept_question(session, user_id, now=BEFORE)
            await repository.mark_generating(session, request_id=generating.request.id)
            await session.execute(
                update(CoachingMessage)
                .where(CoachingMessage.id == generating.request.assistant_message.id)
                .values(content=PARTIAL)
            )
            boundary = await accept_question(session, user_id, now=NOW)
            future = await accept_question(session, user_id, now=LATER)
            completed = await accept_question(session, user_id, now=BEFORE)
            await repository.complete(
                session, request_id=completed.request.id, answer="합성 완료", now=BEFORE
            )
            failed = await accept_question(session, user_id, now=BEFORE)
            await repository.fail(
                session,
                request_id=failed.request.id,
                error_code="GENERATION_INTERRUPTED",
                error_context=None,
                partial_answer="합성 기존 실패",
                now=BEFORE,
            )
            await session.commit()
        expired_before = boundary.request.deadline_at
        assert accepted.request.deadline_at < expired_before
        untouched_items = (boundary, future, completed, failed)
        untouched = [await state_of(sessions, user_id, i) for i in untouched_items]
        untouched_times = [
            await conversation_updated_at(sessions, i) for i in untouched_items
        ]
        async with sessions() as session:
            changed = await repository.fail_expired_requests(
                session, now=LATER, expired_before=expired_before
            )
            await session.commit()
        assert changed == 2
        for item, answer in ((accepted, ""), (generating, PARTIAL)):
            state = await state_of(sessions, user_id, item)
            assert state.status == "failed"
            assert state.error_code == "GENERATION_TIMEOUT"
            assert state.error_context is None
            assert state.finished_at == LATER
            assert state.assistant_message.content == answer
            assert request_state_data(state).status == "failed"
            assert await conversation_updated_at(sessions, item) == LATER
        for item, state, updated in zip(
            untouched_items, untouched, untouched_times, strict=True
        ):
            assert await state_of(sessions, user_id, item) == state
            assert await conversation_updated_at(sessions, item) == updated


async def test_new_question_after_expiry_is_accepted_but_busy_before(
    committed_member,
) -> None:
    """S8·S9 정리 전 같은 대화 질문 → Busy, 정리 후 → Accepted이고 순번이 이어진다."""
    sessions, user_id = committed_member
    async with asyncio.timeout(10):
        async with sessions() as session:
            first = await accept_question(session, user_id, now=BEFORE)
            await session.commit()
        async with sessions() as session:
            busy = await repository.accept(
                session,
                user_id=user_id,
                client_request_id=uuid4(),
                conversation_id=first.conversation.id,
                content="정리 전 합성 질문",
                now=LATER,
            )
            await session.rollback()
        assert isinstance(busy, repository.Busy)
        assert (await state_of(sessions, user_id, first)).status == "accepted"
        async with sessions() as session:
            assert (
                await repository.fail_expired_requests(
                    session,
                    now=LATER,
                    expired_before=first.request.deadline_at + timedelta(seconds=1),
                )
                == 1
            )
            await session.commit()
        async with sessions() as session:
            result = await repository.accept(
                session,
                user_id=user_id,
                client_request_id=uuid4(),
                conversation_id=first.conversation.id,
                content="정리 후 합성 질문",
                now=LATER,
            )
            assert isinstance(result, repository.Accepted)
            assert (
                result.request.assistant_message.sequence_no
                == first.request.assistant_message.sequence_no + 2
            )
            await session.rollback()


async def test_terminal_writes_after_expiry_are_noops(committed_member) -> None:
    """O1·O2 정리가 닫은 요청 → complete·fail은 False, 중단 정리와 재정리는 0이다."""
    sessions, user_id = committed_member
    async with asyncio.timeout(10):
        async with sessions() as session:
            item = await accept_question(session, user_id, now=BEFORE)
            await session.commit()
        expired_before = item.request.deadline_at + timedelta(seconds=1)
        async with sessions() as session:
            assert (
                await repository.fail_expired_requests(
                    session, now=LATER, expired_before=expired_before
                )
                == 1
            )
            await session.commit()
        closed = await state_of(sessions, user_id, item)
        async with sessions() as session:
            assert not await repository.complete(
                session, request_id=item.request.id, answer="늦은 답", now=LATER
            )
            assert not await repository.fail(
                session,
                request_id=item.request.id,
                error_code="GENERATION_FAILED",
                error_context=None,
                partial_answer="늦은 실패",
                now=LATER,
            )
            assert await repository.interrupt_in_progress(session, now=LATER) == 0
            assert (
                await repository.fail_expired_requests(
                    session, now=LATER, expired_before=expired_before
                )
                == 0
            )
            await session.commit()
        assert await state_of(sessions, user_id, item) == closed
        assert closed.error_code == "GENERATION_TIMEOUT"


async def test_fail_expired_requests_rejects_naive_times(committed_member) -> None:
    """S12 시간대 없는 now·expired_before → ValueError."""
    sessions, _ = committed_member
    naive = NOW.replace(tzinfo=None)
    async with sessions() as session:
        for now, before in ((naive, NOW), (NOW, naive)):
            with pytest.raises(ValueError):
                await repository.fail_expired_requests(
                    session, now=now, expired_before=before
                )
