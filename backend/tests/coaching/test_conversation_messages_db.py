"""전용 PostgreSQL에서 메시지 페이지의 순서·소유권·읽기 전용 계약을 검사한다."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth import tokens
from app.coaching import repository
from app.coaching.models import CoachingConversation, CoachingMessage, CoachingRequest
from app.core.config import Settings
from app.core.db import get_session
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 19, 1, tzinfo=UTC)


def fixed_now() -> datetime:
    """합성 토큰 검증에 사용할 고정 UTC 시각을 반환한다."""
    return NOW


def database_url() -> URL:
    """환경변수의 asyncpg 전용 테스트 DB만 허용하며 .env는 읽지 않는다."""
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
    """UUIDv4 식별자와 합성 프로필로 테스트 회원을 만든다."""
    return User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id=str(uuid4()),
        display_name="합성메시지조회회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def committed_members() -> AsyncIterator[tuple[AsyncEngine, tuple[UUID, UUID]]]:
    """합성 회원 두 명을 commit하고 메시지·요청·대화·회원 순으로 정리한다."""
    engine = create_async_engine(database_url())
    users = (member(), member())
    user_ids = users[0].id, users[1].id
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


@pytest.fixture
def messages_db(app, committed_members, monkeypatch):
    """요청마다 새 DB 세션을 열고 조회 중 트랜잭션 호출과 ORM 변경을 검사한다."""
    engine, user_ids = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request_sessions = []
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)

    async def session() -> AsyncIterator[AsyncSession]:
        """HTTP 조회 전용 세션을 제공하고 commit·rollback·flush가 없음을 확인한다."""
        async with sessions() as db_session:
            request_sessions.append(db_session)
            commit = AsyncMock(wraps=db_session.commit)
            rollback = AsyncMock(wraps=db_session.rollback)
            flush = AsyncMock(wraps=db_session.flush)
            with monkeypatch.context() as patch:
                patch.setattr(db_session, "commit", commit)
                patch.setattr(db_session, "rollback", rollback)
                patch.setattr(db_session, "flush", flush)
                try:
                    yield db_session
                finally:
                    commit.assert_not_called()
                    rollback.assert_not_called()
                    flush.assert_not_called()
                    assert not db_session.new
                    assert not db_session.dirty
                    assert not db_session.deleted

    app.dependency_overrides[get_session] = session
    try:
        yield sessions, user_ids, request_sessions
    finally:
        app.dependency_overrides.pop(get_session)


def headers(user_id: UUID, settings: Settings) -> dict[str, str]:
    """합성 회원의 고정 시각 액세스 토큰으로 인증 헤더를 만든다."""
    return {
        "Authorization": "Bearer "
        + tokens.issue_access_token(user_id, settings, now=NOW).token
    }


async def create_request(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
    *,
    conversation_id: UUID | None = None,
    index: int = 0,
    outcome: str = "completed",
) -> repository.RequestState:
    """실제 저장소로 질문을 접수하고 고정 시각을 늘려 각 상태 전이를 commit한다."""
    now = NOW + timedelta(seconds=index * 10)
    async with sessions() as session:
        result = await repository.accept(
            session,
            user_id=user_id,
            client_request_id=uuid4(),
            conversation_id=conversation_id,
            content=f"합성 질문 {index + 1}",
            now=now,
        )
        assert isinstance(result, repository.Accepted)
        await session.commit()
        state = result.request
        if outcome == "accepted":
            return state
        assert await repository.mark_generating(session, request_id=state.id)
        await session.commit()
        if outcome == "generating":
            return state
        if outcome == "completed":
            assert await repository.complete(
                session,
                request_id=state.id,
                answer=f"합성 답변 {index + 1}",
                now=now + timedelta(seconds=2),
            )
        elif outcome == "interrupted":
            assert (
                await repository.interrupt_in_progress(
                    session,
                    now=now + timedelta(seconds=2),
                    created_before=now + timedelta(seconds=1),
                )
                >= 1
            )
        else:
            assert await repository.fail(
                session,
                request_id=state.id,
                error_code="HEALTH_DATA_UNAVAILABLE"
                if outcome == "health_failure"
                else "AI_UNAVAILABLE",
                error_context={"reason": "DATA_NOT_READY", "metrics": ["sleeps"]}
                if outcome == "health_failure"
                else None,
                partial_answer="합성 부분 답변",
                now=now + timedelta(seconds=2),
            )
        await session.commit()
        return state


async def completed_conversation(sessions, user_id: UUID) -> UUID:
    """같은 대화에서 완료된 질문 세 개와 메시지 여섯 개를 저장한다."""
    first = await create_request(sessions, user_id)
    for index in (1, 2):
        await create_request(
            sessions, user_id, conversation_id=first.conversation_id, index=index
        )
    return first.conversation_id


async def stored_snapshot(sessions, conversation_id: UUID) -> tuple:
    """대화·요청·메시지의 모든 저장 컬럼을 읽어 변경 비교용 스냅샷을 만든다."""
    async with sessions() as session:
        conversation = (
            (
                await session.execute(
                    select(CoachingConversation.__table__).where(
                        CoachingConversation.id == conversation_id
                    )
                )
            )
            .mappings()
            .all()
        )
        requests = (
            (
                await session.execute(
                    select(CoachingRequest.__table__)
                    .where(CoachingRequest.conversation_id == conversation_id)
                    .order_by(CoachingRequest.id)
                )
            )
            .mappings()
            .all()
        )
        messages = (
            (
                await session.execute(
                    select(CoachingMessage.__table__)
                    .where(CoachingMessage.conversation_id == conversation_id)
                    .order_by(CoachingMessage.sequence_no)
                )
            )
            .mappings()
            .all()
        )
    return (
        tuple(dict(row) for row in conversation),
        tuple(dict(row) for row in requests),
        tuple(dict(row) for row in messages),
    )


async def read_messages(
    client,
    settings,
    sessions,
    user_id: UUID,
    conversation_id: UUID,
    *,
    limit: int | None = None,
    before: int | None = None,
) -> dict:
    """HTTP 응답을 DB 재조회 값과 비교하고 모든 저장 컬럼이 그대로인지 확인한다."""
    snapshot = await stored_snapshot(sessions, conversation_id)
    params = {}
    if limit is not None:
        params["limit"] = str(limit)
    if before is not None:
        params["beforeSequenceNo"] = str(before)
    response = await client.get(
        f"/api/v1/coaching/conversations/{conversation_id}/messages",
        params=params,
        headers=headers(user_id, settings),
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4
    assert await stored_snapshot(sessions, conversation_id) == snapshot
    async with sessions() as session:
        page = await repository.find_message_page(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=50 if limit is None else limit,
            before_sequence_no=before,
        )
    assert page is not None
    data = response.json()["data"]
    assert set(data) == {"conversation", "items", "nextBeforeSequenceNo"}
    conversation = data["conversation"]
    assert set(conversation) == {"id", "title", "summary", "createdAt", "updatedAt"}
    assert conversation["id"] == str(page.conversation.id)
    assert conversation["title"] == page.conversation.title
    assert conversation["summary"] == page.conversation.summary
    assert (
        datetime.fromisoformat(conversation["createdAt"])
        == page.conversation.created_at
    )
    assert (
        datetime.fromisoformat(conversation["updatedAt"])
        == page.conversation.updated_at
    )
    assert len(data["items"]) == len(page.messages)
    for item, stored in zip(data["items"], page.messages, strict=True):
        assert set(item) == {
            "id",
            "requestId",
            "role",
            "sequenceNo",
            "content",
            "requestStatus",
            "errorCode",
            "createdAt",
            "evidence",
            "errorContext",
        }
        assert item["id"] == str(stored.message.id)
        assert item["requestId"] == str(stored.request_id)
        assert item["role"] == stored.message.role
        assert item["sequenceNo"] == stored.message.sequence_no
        assert item["content"] == stored.message.content
        assert item["requestStatus"] == stored.request_status
        assert item["errorCode"] == stored.error_code
        assert item["errorContext"] == stored.error_context
        assert item["evidence"] == []
        assert datetime.fromisoformat(item["createdAt"]) == stored.message.created_at
    assert data["nextBeforeSequenceNo"] == (
        page.messages[0].message.sequence_no
        if page.messages and page.has_more
        else None
    )
    return data


async def test_messages_latest_then_previous_returns_ordered_pages(
    client, settings, messages_db
) -> None:
    """메시지 여섯 개를 limit=4로 조회 → 3~6과 커서 3을 받은 뒤 1~2로 끝난다."""
    sessions, (owner_id, _), request_sessions = messages_db
    conversation_id = await completed_conversation(sessions, owner_id)
    latest = await read_messages(
        client, settings, sessions, owner_id, conversation_id, limit=4
    )
    assert [item["sequenceNo"] for item in latest["items"]] == [3, 4, 5, 6]
    assert latest["nextBeforeSequenceNo"] == 3
    previous = await read_messages(
        client,
        settings,
        sessions,
        owner_id,
        conversation_id,
        limit=4,
        before=latest["nextBeforeSequenceNo"],
    )
    assert [item["sequenceNo"] for item in previous["items"]] == [1, 2]
    assert previous["nextBeforeSequenceNo"] is None
    assert len(request_sessions) == 2
    assert request_sessions[0] is not request_sessions[1]


async def test_messages_before_boundary_excludes_matching_sequence(
    client, settings, messages_db
) -> None:
    """beforeSequenceNo=4 → 순번 4를 제외한 1~3만 받고 요청의 메시지 쌍도 나뉜다."""
    sessions, (owner_id, _), _ = messages_db
    conversation_id = await completed_conversation(sessions, owner_id)
    page = await read_messages(
        client, settings, sessions, owner_id, conversation_id, before=4
    )
    assert [item["sequenceNo"] for item in page["items"]] == [1, 2, 3]
    assert [item["role"] for item in page["items"]] == ["user", "assistant", "user"]
    assert page["nextBeforeSequenceNo"] is None


@pytest.mark.parametrize(
    "limit,before,sequences", [(6, None, [1, 2, 3, 4, 5, 6]), (4, 5, [1, 2, 3, 4])]
)
async def test_messages_exact_limit_returns_null_cursor(
    client, settings, messages_db, limit, before, sequences
) -> None:
    """남은 메시지가 정확히 limit개 → 빈 다음 페이지를 만들지 않고 null 커서를 받는다."""
    sessions, (owner_id, _), _ = messages_db
    conversation_id = await completed_conversation(sessions, owner_id)
    page = await read_messages(
        client,
        settings,
        sessions,
        owner_id,
        conversation_id,
        limit=limit,
        before=before,
    )
    assert [item["sequenceNo"] for item in page["items"]] == sequences
    assert page["nextBeforeSequenceNo"] is None


async def test_messages_pending_request_preserves_empty_assistant(
    client, settings, messages_db
) -> None:
    """접수 후 생성으로 전환 → 두 메시지의 상태만 바뀌고 assistant와 오류는 비어 있다."""
    sessions, (owner_id, _), request_sessions = messages_db
    state = await create_request(sessions, owner_id, outcome="accepted")
    accepted = await read_messages(
        client, settings, sessions, owner_id, state.conversation_id
    )
    assert [
        [
            item["role"],
            item["content"],
            item["requestStatus"],
            item["errorCode"],
            item["errorContext"],
        ]
        for item in accepted["items"]
    ] == [
        ["user", "합성 질문 1", "accepted", None, None],
        ["assistant", "", "accepted", None, None],
    ]
    async with sessions() as session:
        assert await repository.mark_generating(session, request_id=state.id)
        await session.commit()
    generating = await read_messages(
        client, settings, sessions, owner_id, state.conversation_id
    )
    assert [
        [
            item["role"],
            item["content"],
            item["requestStatus"],
            item["errorCode"],
            item["errorContext"],
        ]
        for item in generating["items"]
    ] == [
        ["user", "합성 질문 1", "generating", None, None],
        ["assistant", "", "generating", None, None],
    ]
    assert len(request_sessions) == 2
    assert request_sessions[0] is not request_sessions[1]


async def test_messages_failed_requests_preserves_partial_content_and_context(
    client, settings, messages_db
) -> None:
    """완료·건강 실패·AI 실패·중단 → 각 요청의 두 메시지에 저장된 상태와 문맥을 붙인다."""
    sessions, (owner_id, _), _ = messages_db
    first = await create_request(sessions, owner_id)
    for index, outcome in enumerate(
        ("health_failure", "ai_failure", "interrupted"), start=1
    ):
        await create_request(
            sessions,
            owner_id,
            conversation_id=first.conversation_id,
            index=index,
            outcome=outcome,
        )
    page = await read_messages(
        client, settings, sessions, owner_id, first.conversation_id
    )
    assert [
        [
            item["sequenceNo"],
            item["content"],
            item["requestStatus"],
            item["errorCode"],
            item["errorContext"],
        ]
        for item in page["items"]
    ] == [
        [1, "합성 질문 1", "completed", None, None],
        [2, "합성 답변 1", "completed", None, None],
        [
            3,
            "합성 질문 2",
            "failed",
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
        ],
        [
            4,
            "합성 부분 답변",
            "failed",
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
        ],
        [5, "합성 질문 3", "failed", "AI_UNAVAILABLE", None],
        [6, "합성 부분 답변", "failed", "AI_UNAVAILABLE", None],
        [7, "합성 질문 4", "failed", "GENERATION_INTERRUPTED", None],
        [8, "", "failed", "GENERATION_INTERRUPTED", None],
    ]


@pytest.mark.parametrize("before", [None, 1])
async def test_messages_other_owner_matches_missing_without_changes(
    client, settings, messages_db, before
) -> None:
    """다른 회원의 대화 조회 → 없는 UUID와 같은 404이며 두 회원의 저장 상태를 보존한다."""
    sessions, (owner_id, other_id), _ = messages_db
    conversation_id = await completed_conversation(sessions, owner_id)
    other_state = await create_request(sessions, other_id)
    owner_before = await read_messages(
        client, settings, sessions, owner_id, conversation_id
    )
    other_before = await stored_snapshot(sessions, other_state.conversation_id)
    params = {} if before is None else {"beforeSequenceNo": str(before)}
    other = await client.get(
        f"/api/v1/coaching/conversations/{conversation_id}/messages",
        params=params,
        headers=headers(other_id, settings),
    )
    missing = await client.get(
        f"/api/v1/coaching/conversations/{uuid4()}/messages",
        params=params,
        headers=headers(other_id, settings),
    )
    assert other.status_code == missing.status_code == 404
    assert (
        other.json()
        == missing.json()
        == {
            "error": {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "대화를 찾을 수 없습니다.",
            }
        }
    )
    assert (
        await read_messages(client, settings, sessions, owner_id, conversation_id)
        == owner_before
    )
    assert await stored_snapshot(sessions, other_state.conversation_id) == other_before


async def test_messages_before_one_returns_empty_page(
    client, settings, messages_db
) -> None:
    """본인 대화에 beforeSequenceNo=1 → 빈 메시지 목록과 null 커서를 받는다."""
    sessions, (owner_id, _), _ = messages_db
    state = await create_request(sessions, owner_id)
    page = await read_messages(
        client, settings, sessions, owner_id, state.conversation_id, before=1
    )
    assert page["items"] == []
    assert page["nextBeforeSequenceNo"] is None
    assert page["conversation"]["id"] == str(state.conversation_id)


@pytest.mark.parametrize("summary", [None, "합성 요약"])
async def test_messages_summary_presence_returns_exact_conversation_fields(
    client, settings, messages_db, summary
) -> None:
    """요약 저장 유무 → 문자열 또는 null을 포함한 정확한 다섯 대화 필드를 받는다."""
    sessions, (owner_id, _), _ = messages_db
    state = await create_request(sessions, owner_id)
    if summary is not None:
        async with sessions() as session:
            await session.execute(
                update(CoachingConversation)
                .where(CoachingConversation.id == state.conversation_id)
                .values(summary="합성 요약")
            )
            await session.commit()
    page = await read_messages(
        client, settings, sessions, owner_id, state.conversation_id
    )
    assert page["conversation"] == {
        "id": str(state.conversation_id),
        "title": "합성 질문 1",
        "summary": summary,
        "createdAt": "2026-10-19T01:00:00Z",
        "updatedAt": "2026-10-19T01:00:02Z",
    }


async def test_messages_default_and_bigint_boundary_returns_latest_page(
    client, settings, messages_db
) -> None:
    """쿼리 생략과 BIGINT 상한 경계 → DB 오류 없이 같은 최신 페이지를 받는다."""
    sessions, (owner_id, _), _ = messages_db
    conversation_id = await completed_conversation(sessions, owner_id)
    default = await read_messages(client, settings, sessions, owner_id, conversation_id)
    maximum = await read_messages(
        client,
        settings,
        sessions,
        owner_id,
        conversation_id,
        before=9223372036854775807,
    )
    assert default == maximum
    assert [item["sequenceNo"] for item in maximum["items"]] == [1, 2, 3, 4, 5, 6]
    assert maximum["nextBeforeSequenceNo"] is None
    response = await client.get(
        f"/api/v1/coaching/conversations/{conversation_id}/messages",
        params={"limit": "020"},
        headers=headers(owner_id, settings),
    )
    assert response.status_code == 200
    assert response.json()["data"] == default


async def test_messages_new_question_between_pages_preserves_cursor_boundary(
    client, settings, messages_db
) -> None:
    """최신 조회 뒤 새 질문을 commit → 이전 페이지는 겹침·누락 없이 기존 경계를 유지한다."""
    sessions, (owner_id, _), _ = messages_db
    conversation_id = await completed_conversation(sessions, owner_id)
    first = await read_messages(
        client, settings, sessions, owner_id, conversation_id, limit=4
    )
    await create_request(
        sessions, owner_id, conversation_id=conversation_id, index=3, outcome="accepted"
    )
    previous = await read_messages(
        client,
        settings,
        sessions,
        owner_id,
        conversation_id,
        limit=4,
        before=first["nextBeforeSequenceNo"],
    )
    assert [item["sequenceNo"] for item in previous["items"]] == [1, 2]
    assert previous["nextBeforeSequenceNo"] is None
    assert [item["sequenceNo"] for item in previous["items"] + first["items"]] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert len({item["id"] for item in previous["items"] + first["items"]}) == 6
    latest = await read_messages(
        client, settings, sessions, owner_id, conversation_id, limit=4
    )
    assert [item["sequenceNo"] for item in latest["items"]] == [5, 6, 7, 8]
    assert latest["nextBeforeSequenceNo"] == 5
    assert latest["items"][:2] == first["items"][-2:]
    assert [item["content"] for item in latest["items"][-2:]] == ["합성 질문 4", ""]


async def test_repository_pending_orm_state_preserves_changes_without_flush(
    messages_db, monkeypatch
) -> None:
    """new·dirty·deleted가 있는 세션에서 대화·메시지·근거 SELECT만 실행하고 쓰기 없이 미저장 상태를 보존한다."""
    sessions, (owner_id, other_id), _ = messages_db
    state = await create_request(sessions, owner_id)
    snapshot = await stored_snapshot(sessions, state.conversation_id)
    async with sessions() as session:
        conversation = await session.get(CoachingConversation, state.conversation_id)
        message = await session.get(CoachingMessage, state.user_message.id)
        conversation.title = "합성 미저장 제목"
        session.add(member())
        await session.delete(message)
        pending = (set(session.new), set(session.dirty), set(session.deleted))
        execute = AsyncMock(wraps=session.execute)
        commit = AsyncMock(wraps=session.commit)
        rollback = AsyncMock(wraps=session.rollback)
        flush = AsyncMock(wraps=session.flush)
        with monkeypatch.context() as patch:
            patch.setattr(session, "execute", execute)
            patch.setattr(session, "commit", commit)
            patch.setattr(session, "rollback", rollback)
            patch.setattr(session, "flush", flush)
            page = await repository.find_message_page(
                session,
                user_id=owner_id,
                conversation_id=state.conversation_id,
                limit=50,
                before_sequence_no=None,
            )
            assert page is not None
            assert page.conversation.title == "합성 질문 1"
            assert [item.message.sequence_no for item in page.messages] == [1, 2]
            assert execute.await_count == 3
            ownership_sql = str(execute.await_args_list[0].args[0])
            message_sql = str(execute.await_args_list[1].args[0])
            assert "coaching_conversation.user_id =" in ownership_sql
            assert "user_id" not in message_sql
            evidence_sql = str(execute.await_args_list[2].args[0])
            assert "coaching_evidence" in evidence_sql
            assert "user_id" not in evidence_sql
            execute.reset_mock()
            assert (
                await repository.find_message_page(
                    session,
                    user_id=other_id,
                    conversation_id=state.conversation_id,
                    limit=50,
                    before_sequence_no=1,
                )
                is None
            )
            assert execute.await_count == 1
            commit.assert_not_called()
            rollback.assert_not_called()
            flush.assert_not_called()
            assert (
                set(session.new),
                set(session.dirty),
                set(session.deleted),
            ) == pending
            assert conversation.title == "합성 미저장 제목"
    assert await stored_snapshot(sessions, state.conversation_id) == snapshot
