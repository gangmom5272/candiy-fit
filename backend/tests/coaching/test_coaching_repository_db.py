"""Alembic을 적용한 전용 PostgreSQL에서 합성 데이터로 저장 계약을 검사한다."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.coaching import repository
from app.coaching.models import CoachingConversation, CoachingMessage, CoachingRequest
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=10)


def database_url() -> URL:
    raw_url = os.environ.get("AUTH_TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail("AUTH_TEST_DATABASE_URL에 Alembic을 적용한 전용 DB를 지정하세요")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "candiy_auth_test"
    ):
        pytest.fail("postgresql+asyncpg 및 candiy_auth_test로 시작하는 DB가 필요합니다")
    return url


def member() -> User:
    return User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id=str(uuid4()),
        display_name="합성회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url())
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                async with AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint"
                ) as session:
                    yield session
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def members(db: AsyncSession) -> tuple[UUID, UUID]:
    first, second = member(), member()
    db.add_all([first, second])
    await db.flush()
    return first.id, second.id


async def accept_question(
    db: AsyncSession,
    user_id: UUID,
    *,
    conversation_id: UUID | None = None,
    client_request_id: UUID | None = None,
    content: str = "합성 질문",
    now: datetime = NOW,
) -> repository.Accepted:
    result = await repository.accept(
        db,
        user_id=user_id,
        client_request_id=client_request_id or uuid4(),
        conversation_id=conversation_id,
        content=content,
        now=now,
    )
    assert isinstance(result, repository.Accepted)
    return result


async def storage_counts(db: AsyncSession, user_id: UUID) -> tuple[int, int, int]:
    conversations = await db.scalar(
        select(func.count())
        .select_from(CoachingConversation)
        .where(CoachingConversation.user_id == user_id)
    )
    requests = await db.scalar(
        select(func.count())
        .select_from(CoachingRequest)
        .where(CoachingRequest.user_id == user_id)
    )
    messages = await db.scalar(
        select(func.count())
        .select_from(CoachingMessage)
        .join(CoachingRequest, CoachingMessage.request_id == CoachingRequest.id)
        .where(CoachingRequest.user_id == user_id)
    )
    return conversations, requests, messages


async def test_accept_new_conversation_stores_request_and_message_pair(db, members):
    content = "합성 질문  내부 공백과 🏃"
    accepted = await accept_question(db, members[0], content=content)
    request = accepted.request

    assert request.status == "accepted"
    assert request.created_at == NOW
    assert request.deadline_at == NOW + timedelta(seconds=120)
    assert request.finished_at is None
    assert request.error_code is None
    assert request.error_context is None
    assert request.user_message.role == "user"
    assert request.user_message.sequence_no == 1
    assert request.user_message.content == content
    assert request.assistant_message.role == "assistant"
    assert request.assistant_message.sequence_no == 2
    assert request.assistant_message.content == ""
    assert request.user_message.created_at == NOW
    assert request.assistant_message.created_at == NOW
    assert len({request.id, request.user_message.id, request.assistant_message.id}) == 3
    assert accepted.conversation.id == request.conversation_id
    assert accepted.conversation.title == content
    assert accepted.conversation.created_at == accepted.conversation.updated_at == NOW
    conversation = await db.get(CoachingConversation, request.conversation_id)
    assert conversation.next_sequence == 3
    assert conversation.summary is None
    assert conversation.summary_until_seq is None
    assert conversation.summary_updated_at is None
    db.expunge_all()
    assert (
        await repository.get_request_state(
            db, user_id=members[0], request_id=request.id
        )
        == request
    )
    assert await storage_counts(db, members[0]) == (1, 1, 2)


async def test_title_uses_first_80_unicode_code_points(db, members):
    content = "가🙂" * 50
    accepted = await accept_question(db, members[0], content=content)

    assert len(content) == 100
    assert accepted.conversation.title == content[:80]
    assert len(accepted.conversation.title) == 80
    assert accepted.request.user_message.content == content


async def test_completed_conversation_allocates_next_pair(db, members):
    first = await accept_question(db, members[0])
    assert await repository.complete(
        db, request_id=first.request.id, answer="답", now=LATER
    )
    second = await accept_question(
        db, members[0], conversation_id=first.conversation.id, now=LATER
    )

    assert second.request.user_message.sequence_no == 3
    assert second.request.assistant_message.sequence_no == 4
    assert second.conversation.id == first.conversation.id
    assert second.conversation.title == first.conversation.title
    assert second.conversation.created_at == NOW
    assert second.conversation.updated_at == LATER
    assert (
        await db.scalar(
            select(CoachingConversation.next_sequence).where(
                CoachingConversation.id == first.conversation.id
            )
        )
        == 5
    )


async def test_accept_hides_other_members_and_missing_conversations(db, members):
    owned = await accept_question(db, members[0])
    for conversation_id in (owned.conversation.id, uuid4()):
        async with db.begin_nested() as attempt:
            result = await repository.accept(
                db,
                user_id=members[1],
                client_request_id=uuid4(),
                conversation_id=conversation_id,
                content="합성 질문",
                now=LATER,
            )
            assert isinstance(result, repository.ConversationNotFound)
            await attempt.rollback()
    assert await storage_counts(db, members[1]) == (0, 0, 0)


@pytest.mark.parametrize("existing_conversation", [False, True])
async def test_duplicate_precedes_busy_and_does_not_add_rows(
    db, members, existing_conversation
):
    conversation_id = None
    if existing_conversation:
        seed = await accept_question(db, members[0])
        await repository.complete(db, request_id=seed.request.id, answer="답", now=NOW)
        conversation_id = seed.conversation.id
    first = await accept_question(db, members[0], conversation_id=conversation_id)
    before = await storage_counts(db, members[0])

    async with db.begin_nested() as attempt:
        result = await repository.accept(
            db,
            user_id=members[0],
            client_request_id=first.request.client_request_id,
            conversation_id=conversation_id,
            content=first.request.user_message.content,
            now=LATER,
        )
        assert isinstance(result, repository.Duplicate)
        assert result.request == first.request
        await attempt.rollback()

    assert await storage_counts(db, members[0]) == before


@pytest.mark.parametrize("changed_field", ["content", "conversation_id"])
async def test_reused_client_request_id_with_changed_input_conflicts(
    db, members, changed_field
):
    first = await accept_question(db, members[0])
    async with db.begin_nested() as attempt:
        result = await repository.accept(
            db,
            user_id=members[0],
            client_request_id=first.request.client_request_id,
            conversation_id=(
                first.conversation.id if changed_field == "conversation_id" else None
            ),
            content="다른 질문" if changed_field == "content" else "합성 질문",
            now=LATER,
        )
        assert isinstance(result, repository.Conflict)
        await attempt.rollback()
    assert await storage_counts(db, members[0]) == (1, 1, 2)


async def test_client_request_id_is_scoped_to_member(db, members):
    client_request_id = uuid4()
    first = await accept_question(db, members[0], client_request_id=client_request_id)
    second = await accept_question(db, members[1], client_request_id=client_request_id)

    assert first.request.id != second.request.id


@pytest.mark.parametrize("generating", [False, True])
async def test_busy_rolls_back_sequence_and_does_not_block_another_conversation(
    db, members, generating
):
    first = await accept_question(db, members[0])
    if generating:
        await repository.mark_generating(db, request_id=first.request.id)
    async with db.begin_nested() as attempt:
        result = await repository.accept(
            db,
            user_id=members[0],
            client_request_id=uuid4(),
            conversation_id=first.conversation.id,
            content="새 합성 질문",
            now=LATER,
        )
        assert isinstance(result, repository.Busy)
        await attempt.rollback()

    conversation = await db.get(CoachingConversation, first.conversation.id)
    assert conversation.next_sequence == 3
    assert conversation.updated_at == NOW
    other = await accept_question(db, members[0], now=LATER)
    assert other.conversation.id != first.conversation.id
    assert await storage_counts(db, members[0]) == (2, 2, 4)


async def test_mark_generating_only_transitions_from_accepted(db, members):
    accepted = await accept_question(db, members[0])
    assert await repository.mark_generating(db, request_id=accepted.request.id)
    assert not await repository.mark_generating(db, request_id=accepted.request.id)
    state = await repository.get_request_state(
        db, user_id=members[0], request_id=accepted.request.id
    )
    assert state.status == "generating"
    assert state.finished_at is None


@pytest.mark.parametrize("generating", [False, True])
async def test_complete_is_terminal_and_updates_answer_and_conversation(
    db, members, generating
):
    accepted = await accept_question(db, members[0])
    if generating:
        await repository.mark_generating(db, request_id=accepted.request.id)
    assert await repository.complete(
        db, request_id=accepted.request.id, answer="합성 답변", now=LATER
    )
    assert not await repository.complete(
        db,
        request_id=accepted.request.id,
        answer="늦은 답변",
        now=LATER + timedelta(seconds=1),
    )
    assert not await repository.mark_generating(db, request_id=accepted.request.id)
    assert not await repository.fail(
        db,
        request_id=accepted.request.id,
        error_code="GENERATION_TIMEOUT",
        error_context=None,
        partial_answer="늦은 부분 답변",
        now=LATER + timedelta(seconds=1),
    )
    state = await repository.get_request_state(
        db, user_id=members[0], request_id=accepted.request.id
    )
    assert state.status == "completed"
    assert state.finished_at == LATER
    assert state.error_code is None
    assert state.error_context is None
    assert state.assistant_message.content == "합성 답변"
    assert state.assistant_message.created_at == NOW
    assert state.user_message == accepted.request.user_message
    assert (
        await db.scalar(
            select(CoachingConversation.updated_at).where(
                CoachingConversation.id == accepted.conversation.id
            )
        )
        == LATER
    )


@pytest.mark.parametrize("generating", [False, True])
@pytest.mark.parametrize("with_context", [False, True])
async def test_fail_preserves_error_and_partial_answer_and_rejects_late_completion(
    db, members, generating, with_context
):
    accepted = await accept_question(db, members[0])
    if generating:
        await repository.mark_generating(db, request_id=accepted.request.id)
    context = (
        {"reason": "LOOKUP_FAILED", "metrics": ["sleeps"]} if with_context else None
    )
    assert await repository.fail(
        db,
        request_id=accepted.request.id,
        error_code="HEALTH_DATA_UNAVAILABLE",
        error_context=MappingProxyType(context) if context is not None else None,
        partial_answer="합성 부분 답변",
        now=LATER,
    )
    assert not await repository.complete(
        db, request_id=accepted.request.id, answer="늦은 답변", now=LATER
    )
    assert not await repository.mark_generating(db, request_id=accepted.request.id)
    assert not await repository.fail(
        db,
        request_id=accepted.request.id,
        error_code="GENERATION_TIMEOUT",
        error_context=None,
        partial_answer="덮어쓰기",
        now=LATER + timedelta(seconds=1),
    )
    db.expunge_all()
    state = await repository.get_request_state(
        db, user_id=members[0], request_id=accepted.request.id
    )
    assert state.status == "failed"
    assert state.error_code == "HEALTH_DATA_UNAVAILABLE"
    assert state.error_context == context
    assert state.finished_at == LATER
    assert state.assistant_message.content == "합성 부분 답변"
    assert state.assistant_message.created_at == NOW
    assert state.user_message == accepted.request.user_message
    assert (
        await db.scalar(
            select(CoachingConversation.updated_at).where(
                CoachingConversation.id == accepted.conversation.id
            )
        )
        == LATER
    )


async def test_request_lookup_hides_other_members_and_missing_requests(db, members):
    accepted = await accept_question(db, members[0])
    assert (
        await repository.get_request_state(
            db, user_id=members[0], request_id=accepted.request.id
        )
        == accepted.request
    )
    assert (
        await repository.get_request_state(
            db, user_id=members[1], request_id=accepted.request.id
        )
        is None
    )
    assert (
        await repository.get_request_state(db, user_id=members[0], request_id=uuid4())
        is None
    )


async def test_missing_request_cannot_transition(db, members):
    request_id = uuid4()
    assert not await repository.mark_generating(db, request_id=request_id)
    assert not await repository.complete(
        db, request_id=request_id, answer="답", now=NOW
    )
    assert not await repository.fail(
        db,
        request_id=request_id,
        error_code="GENERATION_TIMEOUT",
        error_context=None,
        partial_answer="",
        now=NOW,
    )
    assert await storage_counts(db, members[0]) == (0, 0, 0)


async def test_history_contains_only_completed_pairs_in_sequence_order(db, members):
    first = await accept_question(db, members[0], content="첫 합성 질문")
    await repository.complete(db, request_id=first.request.id, answer="첫 답", now=NOW)
    failed = await accept_question(
        db, members[0], conversation_id=first.conversation.id
    )
    await repository.fail(
        db,
        request_id=failed.request.id,
        error_code="GENERATION_TIMEOUT",
        error_context=None,
        partial_answer="실패한 부분 답변",
        now=NOW,
    )
    completed = await accept_question(
        db, members[0], conversation_id=first.conversation.id, content="세 번째 질문"
    )
    await repository.complete(
        db, request_id=completed.request.id, answer="세 번째 답", now=NOW
    )
    pending = await accept_question(
        db, members[0], conversation_id=first.conversation.id
    )
    other = await accept_question(db, members[1])
    await repository.complete(
        db, request_id=other.request.id, answer="다른 답", now=NOW
    )

    for generating in (False, True):
        if generating:
            await repository.mark_generating(db, request_id=pending.request.id)
        history = await repository.list_history(
            db, conversation_id=first.conversation.id, before_sequence_no=100
        )
        assert [message.sequence_no for message in history] == [1, 2, 5, 6]
        assert [message.content for message in history] == [
            "첫 합성 질문",
            "첫 답",
            "세 번째 질문",
            "세 번째 답",
        ]
        assert [message.role for message in history] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
    earlier = await repository.list_history(
        db, conversation_id=first.conversation.id, before_sequence_no=6
    )
    assert [message.sequence_no for message in earlier] == [1, 2, 5]
    assert (
        await repository.list_history(
            db, conversation_id=first.conversation.id, before_sequence_no=1
        )
        == []
    )
    assert (
        await repository.list_history(db, conversation_id=uuid4(), before_sequence_no=9)
        == []
    )


async def test_interrupt_changes_only_in_progress_requests(db, members):
    accepted = await accept_question(db, members[0])
    generating = await accept_question(db, members[1])
    await repository.mark_generating(db, request_id=generating.request.id)
    assistant = await db.get(CoachingMessage, generating.request.assistant_message.id)
    assistant.content = "저장된 합성 조각"
    await db.flush()
    completed = await accept_question(db, members[0])
    await repository.complete(
        db, request_id=completed.request.id, answer="완료", now=NOW
    )
    failed = await accept_question(db, members[1])
    await repository.fail(
        db,
        request_id=failed.request.id,
        error_code="HEALTH_DATA_UNAVAILABLE",
        error_context={"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
        partial_answer="기존 부분 답변",
        now=NOW,
    )
    terminal_states = [
        await repository.get_request_state(
            db, user_id=user_id, request_id=item.request.id
        )
        for user_id, item in ((members[0], completed), (members[1], failed))
    ]

    assert await repository.interrupt_in_progress(db, now=LATER) == 2
    assert (
        await repository.interrupt_in_progress(db, now=LATER + timedelta(seconds=1))
        == 0
    )
    for user_id, item, answer in (
        (members[0], accepted, ""),
        (members[1], generating, "저장된 합성 조각"),
    ):
        state = await repository.get_request_state(
            db, user_id=user_id, request_id=item.request.id
        )
        assert state.status == "failed"
        assert state.error_code == "GENERATION_INTERRUPTED"
        assert state.error_context is None
        assert state.finished_at == LATER
        assert state.assistant_message.content == answer
        assert (
            await db.scalar(
                select(CoachingConversation.updated_at).where(
                    CoachingConversation.id == item.conversation.id
                )
            )
            == LATER
        )
    for user_id, item, previous in zip(
        (members[0], members[1]), (completed, failed), terminal_states, strict=True
    ):
        assert (
            await repository.get_request_state(
                db, user_id=user_id, request_id=item.request.id
            )
            == previous
        )
        assert (
            await db.scalar(
                select(CoachingConversation.updated_at).where(
                    CoachingConversation.id == item.conversation.id
                )
            )
            == NOW
        )


@pytest.mark.parametrize("generating", [False, True])
async def test_interrupt_created_before_excludes_boundary_and_new_requests(
    db: AsyncSession, members: tuple[UUID, UUID], generating: bool
) -> None:
    """기동 시각으로 정리 → 그 시각 이전 진행 요청만 닫고 경계·이후 요청은 보존한다."""
    old = await accept_question(db, members[0], now=NOW)
    boundary = await accept_question(db, members[0], now=LATER)
    newer = await accept_question(db, members[1], now=LATER + timedelta(seconds=1))
    if generating:
        for item in (old, boundary, newer):
            await repository.mark_generating(db, request_id=item.request.id)
    before = [
        await repository.get_request_state(
            db, user_id=user_id, request_id=item.request.id
        )
        for user_id, item in ((members[0], boundary), (members[1], newer))
    ]
    finished_at = LATER + timedelta(seconds=2)
    assert (
        await repository.interrupt_in_progress(
            db, now=finished_at, created_before=LATER
        )
        == 1
    )
    state = await repository.get_request_state(
        db, user_id=members[0], request_id=old.request.id
    )
    assert state.status == "failed"
    assert state.error_code == "GENERATION_INTERRUPTED"
    assert state.finished_at == finished_at
    for user_id, item, expected in zip(
        (members[0], members[1]), (boundary, newer), before, strict=True
    ):
        assert (
            await repository.get_request_state(
                db, user_id=user_id, request_id=item.request.id
            )
            == expected
        )
        assert (
            await db.scalar(
                select(CoachingConversation.updated_at).where(
                    CoachingConversation.id == item.conversation.id
                )
            )
            == item.conversation.updated_at
        )


async def test_interrupt_rejects_naive_created_before(
    db: AsyncSession, members: tuple[UUID, UUID]
) -> None:
    """시간대 없는 created_before → ValueError를 내고 진행 요청을 바꾸지 않는다."""
    accepted = await accept_question(db, members[0])
    with pytest.raises(ValueError, match="timezone-aware"):
        await repository.interrupt_in_progress(
            db, now=LATER, created_before=LATER.replace(tzinfo=None)
        )
    assert (
        await repository.get_request_state(
            db, user_id=members[0], request_id=accepted.request.id
        )
        == accepted.request
    )


async def test_failed_request_without_error_code_violates_constraint(db, members):
    accepted = await accept_question(db, members[0])
    await repository.complete(db, request_id=accepted.request.id, answer="답", now=NOW)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                CoachingRequest(
                    id=uuid4(),
                    conversation_id=accepted.conversation.id,
                    user_id=members[0],
                    client_request_id=uuid4(),
                    input_hash=repository.input_hash(accepted.conversation.id, "질문"),
                    status="failed",
                    created_at=NOW,
                    deadline_at=NOW + timedelta(seconds=120),
                    finished_at=LATER,
                )
            )
            await db.flush()


@pytest.mark.parametrize("invalid", ["role", "sequence_no"])
async def test_invalid_message_role_and_duplicate_sequence_violate_constraints(
    db, members, invalid
):
    first = await accept_question(db, members[0])
    await repository.complete(db, request_id=first.request.id, answer="답", now=NOW)
    second = await accept_question(
        db, members[0], conversation_id=first.conversation.id
    )
    # 기존 역할 행을 지워 request_id/role 고유 제약과 독립적으로 검사한다.
    await db.execute(
        delete(CoachingMessage).where(
            CoachingMessage.id == second.request.user_message.id
        )
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                CoachingMessage(
                    id=uuid4(),
                    conversation_id=first.conversation.id,
                    request_id=second.request.id,
                    role="system" if invalid == "role" else "user",
                    sequence_no=3 if invalid == "role" else 1,
                    content="합성 메시지",
                    created_at=NOW,
                )
            )
            await db.flush()


@pytest.fixture
async def committed_members() -> AsyncIterator[tuple[AsyncEngine, tuple[UUID, UUID]]]:
    engine = create_async_engine(database_url())
    users = (member(), member())
    user_ids = (users[0].id, users[1].id)
    try:
        async with AsyncSession(engine) as session:
            session.add_all(users)
            await session.commit()
        yield engine, user_ids
    finally:
        try:
            async with engine.begin() as conn:
                conversation_ids = select(CoachingConversation.id).where(
                    CoachingConversation.user_id.in_(user_ids)
                )
                await conn.execute(
                    delete(CoachingMessage).where(
                        CoachingMessage.conversation_id.in_(conversation_ids)
                    )
                )
                await conn.execute(
                    delete(CoachingRequest).where(CoachingRequest.user_id.in_(user_ids))
                )
                await conn.execute(
                    delete(CoachingConversation).where(
                        CoachingConversation.user_id.in_(user_ids)
                    )
                )
                await conn.execute(delete(User).where(User.id.in_(user_ids)))
        finally:
            await engine.dispose()


async def race_accepts(
    engine: AsyncEngine,
    user_id: UUID,
    conversation_id: UUID | None,
    client_request_ids: tuple[UUID, UUID],
) -> list[repository.AcceptResult]:
    barrier = asyncio.Barrier(2)

    async def attempt(client_request_id: UUID) -> repository.AcceptResult:
        async with engine.connect() as conn, AsyncSession(bind=conn) as session:
            await barrier.wait()
            result = await repository.accept(
                session,
                user_id=user_id,
                client_request_id=client_request_id,
                conversation_id=conversation_id,
                content="동시 합성 질문",
                now=LATER,
            )
            if isinstance(result, repository.Accepted):
                await session.commit()
            else:
                await session.rollback()
            return result

    async with asyncio.timeout(20), asyncio.TaskGroup() as group:
        tasks = [group.create_task(attempt(value)) for value in client_request_ids]
    return [task.result() for task in tasks]


async def test_concurrent_new_request_is_accepted_once_and_duplicated(
    committed_members,
):
    engine, user_ids = committed_members
    client_request_id = uuid4()
    results = await race_accepts(
        engine, user_ids[0], None, (client_request_id, client_request_id)
    )

    assert sum(isinstance(item, repository.Accepted) for item in results) == 1
    assert sum(isinstance(item, repository.Duplicate) for item in results) == 1
    accepted = next(item for item in results if isinstance(item, repository.Accepted))
    duplicate = next(item for item in results if isinstance(item, repository.Duplicate))
    assert duplicate.request == accepted.request
    async with AsyncSession(engine) as session:
        assert await storage_counts(session, user_ids[0]) == (1, 1, 2)


async def test_concurrent_requests_in_same_conversation_accept_only_one(
    committed_members,
):
    engine, user_ids = committed_members
    async with AsyncSession(engine) as session:
        first = await accept_question(session, user_ids[0])
        await repository.complete(
            session, request_id=first.request.id, answer="합성 답변", now=NOW
        )
        await session.commit()
    results = await race_accepts(
        engine, user_ids[0], first.conversation.id, (uuid4(), uuid4())
    )

    assert sum(isinstance(item, repository.Accepted) for item in results) == 1
    assert sum(isinstance(item, repository.Busy) for item in results) == 1
    accepted = next(item for item in results if isinstance(item, repository.Accepted))
    assert accepted.request.user_message.sequence_no == 3
    assert accepted.request.assistant_message.sequence_no == 4
    async with AsyncSession(engine) as session:
        assert await storage_counts(session, user_ids[0]) == (1, 2, 4)
        assert (
            await session.scalar(
                select(CoachingConversation.next_sequence).where(
                    CoachingConversation.id == first.conversation.id
                )
            )
            == 5
        )
