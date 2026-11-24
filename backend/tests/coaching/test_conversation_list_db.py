"""전용 PostgreSQL에서 대화 목록의 순서·소유권·읽기 전용 계약을 검사한다."""

import base64
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
PATH = "/api/v1/coaching/conversations"


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
        display_name="합성목록조회회원",
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
def conversations_db(app, committed_members, monkeypatch):
    """요청마다 새 세션을 열어 한 SELECT와 쓰기·ORM 변경 금지를 검사한다."""
    engine, user_ids = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request_sessions = []
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)

    async def session() -> AsyncIterator[AsyncSession]:
        """조회 전용 세션을 제공하고 한 쿼리·트랜잭션 호출 없음·빈 변경 집합을 확인한다."""
        async with sessions() as db_session:
            request_sessions.append(db_session)
            execute = AsyncMock(wraps=db_session.execute)
            commit = AsyncMock(wraps=db_session.commit)
            rollback = AsyncMock(wraps=db_session.rollback)
            flush = AsyncMock(wraps=db_session.flush)
            with monkeypatch.context() as patch:
                patch.setattr(db_session, "execute", execute)
                patch.setattr(db_session, "commit", commit)
                patch.setattr(db_session, "rollback", rollback)
                patch.setattr(db_session, "flush", flush)
                try:
                    yield db_session
                finally:
                    assert execute.await_count == 1
                    statement = execute.await_args.args[0]
                    assert statement.get_execution_options()["autoflush"] is False
                    sql = str(statement)
                    assert "JOIN LATERAL" in sql
                    assert "LEFT OUTER JOIN" not in sql
                    assert "coaching_conversation.user_id =" in sql
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
        else:
            assert outcome == "failed"
            assert await repository.fail(
                session,
                request_id=state.id,
                error_code="AI_UNAVAILABLE",
                error_context=None,
                partial_answer="합성 부분 답변",
                now=now + timedelta(seconds=2),
            )
        await session.commit()
        return state


async def stored_snapshot(sessions, user_ids: tuple[UUID, UUID]) -> tuple:
    """두 합성 회원의 대화·요청·메시지 전체 컬럼을 변경 비교용으로 읽는다."""
    conversation_ids = select(CoachingConversation.id).where(
        CoachingConversation.user_id.in_(user_ids)
    )
    statements = (
        select(CoachingConversation.__table__)
        .where(CoachingConversation.user_id.in_(user_ids))
        .order_by(CoachingConversation.id),
        select(CoachingRequest.__table__)
        .where(CoachingRequest.user_id.in_(user_ids))
        .order_by(CoachingRequest.id),
        select(CoachingMessage.__table__)
        .where(CoachingMessage.conversation_id.in_(conversation_ids))
        .order_by(CoachingMessage.id),
    )
    async with sessions() as session:
        rows = []
        for statement in statements:
            result = await session.execute(statement)
            rows.append(tuple(dict(row) for row in result.mappings()))
    return tuple(rows)


def expected_cursor(updated_at: datetime, conversation_id: UUID) -> str:
    """DB 시각의 정수 마이크로초와 UUID로 구현과 독립적인 기대 커서를 만든다."""
    micros = (updated_at - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(
        microseconds=1
    )
    text = f"1.{micros}.{conversation_id.hex}".encode("ascii")
    return base64.urlsafe_b64encode(text).rstrip(b"=").decode("ascii")


async def read_conversations(
    client,
    settings,
    conversations_db,
    user_id: UUID,
    *,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    """목록 HTTP 응답을 저장 컬럼과 비교하고 두 회원의 모든 저장 상태 보존을 확인한다."""
    sessions, user_ids, _ = conversations_db
    snapshot = await stored_snapshot(sessions, user_ids)
    params = {}
    if limit is not None:
        params["limit"] = str(limit)
    if cursor is not None:
        params["cursor"] = cursor
    response = await client.get(PATH, params=params, headers=headers(user_id, settings))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4
    assert await stored_snapshot(sessions, user_ids) == snapshot
    data = response.json()["data"]
    assert set(data) == {"items", "nextCursor"}
    conversations, requests, messages = snapshot
    by_id = {row["id"]: row for row in conversations}
    requests_by_id = {row["id"]: row for row in requests}
    for item in data["items"]:
        stored = by_id[UUID(item["id"])]
        assert stored["user_id"] == user_id
        last_message = max(
            (row for row in messages if row["conversation_id"] == stored["id"]),
            key=lambda row: row["sequence_no"],
        )
        assert item == {
            "id": str(stored["id"]),
            "title": stored["title"],
            "summary": stored["summary"],
            "createdAt": stored["created_at"]
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "updatedAt": stored["updated_at"]
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "lastRequestStatus": requests_by_id[last_message["request_id"]]["status"],
        }
        assert len(item) == 6
    if data["nextCursor"] is not None:
        assert data["items"]
        last = by_id[UUID(data["items"][-1]["id"])]
        assert data["nextCursor"] == expected_cursor(last["updated_at"], last["id"])
    return data


async def test_conversations_empty_owner_returns_empty_page(
    client, settings, conversations_db
) -> None:
    """본인에게 접수된 대화가 없음 → 빈 목록과 null 커서를 받으며 저장 상태를 보존한다."""
    sessions, (owner_id, other_id), _ = conversations_db
    await create_request(sessions, other_id)
    page = await read_conversations(client, settings, conversations_db, owner_id)
    assert page == {"items": [], "nextCursor": None}


async def test_conversations_tied_microseconds_returns_every_item_once(
    client, settings, conversations_db
) -> None:
    """동률 시각과 1µs 차이 대화 → limit 2로 끝까지 읽으면 DB 키 내림차순으로 한 번씩 나온다."""
    sessions, (owner_id, _), request_sessions = conversations_db
    states = [
        await create_request(sessions, owner_id, index=index) for index in range(5)
    ]
    async with sessions() as session:
        for state, offset in zip(states, (0, 0, 0, 1, -1), strict=True):
            await session.execute(
                update(CoachingConversation)
                .where(CoachingConversation.id == state.conversation_id)
                .values(updated_at=NOW + timedelta(minutes=1, microseconds=offset))
            )
        await session.commit()
        keys = (
            await session.execute(
                select(CoachingConversation.updated_at, CoachingConversation.id).where(
                    CoachingConversation.user_id == owner_id
                )
            )
        ).all()
    expected = [
        str(row.id)
        for row in sorted(
            keys, key=lambda row: (row.updated_at, row.id.bytes), reverse=True
        )
    ]
    ids = []
    cursor = None
    for _ in range(3):
        page = await read_conversations(
            client, settings, conversations_db, owner_id, limit=2, cursor=cursor
        )
        ids.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert ids == expected
    assert len(set(ids)) == 5
    assert cursor is None
    assert len(request_sessions) == 3
    assert len({id(session) for session in request_sessions}) == 3


async def test_conversations_exact_limit_returns_null_cursor(
    client, settings, conversations_db
) -> None:
    """남은 대화가 정확히 limit개 → 다음 빈 페이지를 만들지 않고 null 커서로 끝난다."""
    sessions, (owner_id, _), _ = conversations_db
    states = [
        await create_request(sessions, owner_id, index=index) for index in range(4)
    ]
    first = await read_conversations(
        client, settings, conversations_db, owner_id, limit=2
    )
    assert first["nextCursor"] == expected_cursor(
        NOW + timedelta(seconds=22), states[2].conversation_id
    )
    last = await read_conversations(
        client,
        settings,
        conversations_db,
        owner_id,
        limit=2,
        cursor=first["nextCursor"],
    )
    assert [item["id"] for item in last["items"]] == [
        str(states[1].conversation_id),
        str(states[0].conversation_id),
    ]
    assert last["nextCursor"] is None


async def test_conversations_foreign_cursor_returns_only_own_items(
    client, settings, conversations_db
) -> None:
    """다른 회원의 커서를 사용함 → 커서 전후에서도 자기 대화만 받는다."""
    sessions, (owner_id, other_id), _ = conversations_db
    owner_states = [
        await create_request(sessions, owner_id, index=index) for index in (1, 3, 5)
    ]
    other_states = [
        await create_request(sessions, other_id, index=index) for index in (0, 2, 4)
    ]
    first = await read_conversations(
        client, settings, conversations_db, owner_id, limit=2
    )
    assert [item["id"] for item in first["items"]] == [
        str(owner_states[2].conversation_id),
        str(owner_states[1].conversation_id),
    ]
    own = await read_conversations(client, settings, conversations_db, other_id)
    assert [item["id"] for item in own["items"]] == [
        str(state.conversation_id) for state in reversed(other_states)
    ]
    foreign_cursor = await read_conversations(
        client, settings, conversations_db, other_id, cursor=first["nextCursor"]
    )
    assert [item["id"] for item in foreign_cursor["items"]] == [
        str(other_states[1].conversation_id),
        str(other_states[0].conversation_id),
    ]
    assert foreign_cursor["nextCursor"] is None


async def test_conversations_all_request_statuses_returns_all_items(
    client, settings, conversations_db
) -> None:
    """접수·생성·실패·완료 대화가 공존함 → 네 상태의 대화를 모두 반환한다."""
    sessions, (owner_id, _), _ = conversations_db
    for index, outcome in enumerate(("accepted", "generating", "failed", "completed")):
        await create_request(sessions, owner_id, index=index, outcome=outcome)
    page = await read_conversations(client, settings, conversations_db, owner_id)
    assert [item["lastRequestStatus"] for item in page["items"]] == [
        "completed",
        "failed",
        "generating",
        "accepted",
    ]
    assert page["nextCursor"] is None


async def test_conversations_updated_during_paging_skips_moved_items(
    client, settings, conversations_db
) -> None:
    """1페이지 뒤 대화가 갱신되면 → 2페이지에서 빠지고 이미 받은 대화도 다시 나오지 않는다."""
    sessions, (owner_id, _), _ = conversations_db
    states = [
        await create_request(sessions, owner_id, index=index) for index in range(4)
    ]
    first = await read_conversations(
        client, settings, conversations_db, owner_id, limit=2
    )
    assert [item["id"] for item in first["items"]] == [
        str(states[3].conversation_id),
        str(states[2].conversation_id),
    ]
    await create_request(
        sessions,
        owner_id,
        conversation_id=states[1].conversation_id,
        index=4,
        outcome="accepted",
    )
    await create_request(
        sessions,
        owner_id,
        conversation_id=states[3].conversation_id,
        index=5,
        outcome="accepted",
    )
    second = await read_conversations(
        client,
        settings,
        conversations_db,
        owner_id,
        limit=2,
        cursor=first["nextCursor"],
    )
    assert [item["id"] for item in second["items"]] == [str(states[0].conversation_id)]
    assert second["nextCursor"] is None
    assert not (
        {item["id"] for item in first["items"]}
        & {item["id"] for item in second["items"]}
    )
    latest = await read_conversations(
        client, settings, conversations_db, owner_id, limit=2
    )
    assert [item["id"] for item in latest["items"]] == [
        str(states[3].conversation_id),
        str(states[1].conversation_id),
    ]
    assert [item["lastRequestStatus"] for item in latest["items"]] == [
        "accepted",
        "accepted",
    ]


@pytest.mark.parametrize("outcome", ["failed", "generating"])
async def test_conversations_multiple_requests_uses_largest_message_sequence(
    client, settings, conversations_db, outcome
) -> None:
    """앞 요청의 created_at이 더 늦어도 → 가장 큰 메시지 순번의 뒤 요청 상태를 반환한다."""
    sessions, (owner_id, _), _ = conversations_db
    initial = await create_request(sessions, owner_id)
    first = await create_request(
        sessions, owner_id, conversation_id=initial.conversation_id, index=2
    )
    # 대화 생성 이후이면서 앞 요청보다 이른 시각으로 마지막 요청을 접수한다.
    later = await create_request(
        sessions,
        owner_id,
        conversation_id=first.conversation_id,
        index=1,
        outcome=outcome,
    )
    assert later.created_at < first.created_at
    assert later.assistant_message.sequence_no > first.assistant_message.sequence_no
    page = await read_conversations(client, settings, conversations_db, owner_id)
    assert len(page["items"]) == 1
    assert page["items"][0]["lastRequestStatus"] == outcome


@pytest.mark.parametrize("summary", [None, "합성 요약"])
async def test_conversations_summary_microseconds_returns_six_fields(
    client, settings, conversations_db, summary
) -> None:
    """요약 유무·마이크로초 시각 → 여섯 항목 필드와 문자열 또는 null 요약을 받는다."""
    sessions, (owner_id, _), _ = conversations_db
    state = await create_request(sessions, owner_id)
    async with sessions() as session:
        await session.execute(
            update(CoachingConversation)
            .where(CoachingConversation.id == state.conversation_id)
            .values(
                summary=summary,
                updated_at=NOW + timedelta(seconds=2, microseconds=654321),
            )
        )
        await session.commit()
    page = await read_conversations(client, settings, conversations_db, owner_id)
    assert page == {
        "items": [
            {
                "id": str(state.conversation_id),
                "title": "합성 질문 1",
                "summary": summary,
                "createdAt": "2026-10-19T01:00:00Z",
                "updatedAt": "2026-10-19T01:00:02.654321Z",
                "lastRequestStatus": "completed",
            }
        ],
        "nextCursor": None,
    }


async def test_repository_pending_orm_state_preserves_changes_without_flush(
    conversations_db, monkeypatch
) -> None:
    """new·dirty·deleted가 있는 세션에서 조회 → 한 SELECT만 실행하고 미저장 상태를 보존한다."""
    sessions, user_ids, _ = conversations_db
    owner_id, _ = user_ids
    state = await create_request(sessions, owner_id)
    snapshot = await stored_snapshot(sessions, user_ids)
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
            page = await repository.list_conversations(
                session,
                user_id=owner_id,
                limit=20,
                after=repository.ConversationKey(NOW + timedelta(minutes=1), uuid4()),
            )
            assert len(page.items) == 1
            assert page.items[0].conversation.title == "합성 질문 1"
            assert page.items[0].last_request_status == "completed"
            assert execute.await_count == 1
            statement = execute.await_args.args[0]
            sql = str(statement)
            assert (
                "(coaching_conversation.updated_at, coaching_conversation.id) <" in sql
            )
            assert (
                "coaching_conversation.updated_at DESC, coaching_conversation.id DESC"
                in sql
            )
            assert statement.get_execution_options()["autoflush"] is False
            commit.assert_not_called()
            rollback.assert_not_called()
            flush.assert_not_called()
            assert (
                set(session.new),
                set(session.dirty),
                set(session.deleted),
            ) == pending
            assert conversation.title == "합성 미저장 제목"
    assert await stored_snapshot(sessions, user_ids) == snapshot
