"""전용 PostgreSQL의 저장 상태를 새 HTTP 요청으로 폴링하며 조회 계약을 검사한다."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
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
from app.coaching.schemas import request_state_data
from app.core.config import Settings
from app.core.db import get_session
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 19, 1, tzinfo=UTC)
FINISHED_AT = NOW + timedelta(seconds=2)


def fixed_now() -> datetime:
    """합성 토큰 검증에 사용할 고정 UTC 시각을 반환한다."""
    return NOW


def database_url() -> URL:
    """환경변수로 명시한 asyncpg 전용 테스트 DB만 허용하며 .env는 읽지 않는다."""
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
    """실제 건강데이터 없이 UUIDv4와 합성 프로필로 회원을 만든다."""
    return User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id=str(uuid4()),
        display_name="합성상태조회회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def committed_members() -> AsyncIterator[tuple[AsyncEngine, tuple[UUID, UUID]]]:
    """합성 회원을 commit하고 finally에서 메시지·요청·대화·회원 순으로 정리한다."""
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
def status_db(app, committed_members, monkeypatch):
    """HTTP 요청마다 새 DB 세션을 제공하고 조회 중 commit·rollback·flush를 검사한다."""
    engine, user_ids = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request_sessions = []
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)

    async def session() -> AsyncIterator[AsyncSession]:
        """요청 전용 실제 세션을 열고 서비스가 트랜잭션을 변경하지 않는지 확인한다."""
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
    """합성 회원의 액세스 토큰으로 HTTP 인증 헤더를 만든다."""
    return {
        "Authorization": "Bearer "
        + tokens.issue_access_token(user_id, settings, now=NOW).token
    }


async def accept_request(
    sessions: async_sessionmaker[AsyncSession], user_id: UUID
) -> UUID:
    """실제 저장소로 합성 질문을 접수하고 commit한 요청 ID를 반환한다."""
    async with sessions() as session:
        result = await repository.accept(
            session,
            user_id=user_id,
            client_request_id=uuid4(),
            conversation_id=None,
            content="합성 상태 조회 질문",
            now=NOW,
        )
        assert isinstance(result, repository.Accepted)
        await session.commit()
        return result.request.id


async def read_status(
    client, settings, sessions, user_id: UUID, request_id: UUID
) -> dict:
    """새 HTTP 조회 결과를 DB 재조회와 비교하고 저장 상태가 그대로인지 검사한다."""
    async with sessions() as session:
        before = await repository.get_request_state(
            session, user_id=user_id, request_id=request_id
        )
    response = await client.get(
        f"/api/v1/coaching/requests/{request_id}", headers=headers(user_id, settings)
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4
    async with sessions() as session:
        after = await repository.get_request_state(
            session, user_id=user_id, request_id=request_id
        )
    assert after is not None
    assert before == after
    assert response.json() == {
        "data": request_state_data(after).model_dump(mode="json")
    }
    data = response.json()["data"]
    assert len(data) == 11
    return data


async def test_request_status_polling_from_acceptance_to_completion(
    client, settings, status_db
) -> None:
    """접수·생성·완료를 commit하며 폴링하면 → 네트워크 재요청마다 최신 저장 상태를 받는다."""
    sessions, (owner_id, _), request_sessions = status_db
    request_id = await accept_request(sessions, owner_id)
    accepted = await read_status(client, settings, sessions, owner_id, request_id)
    assert accepted["status"] == "accepted"
    assert accepted["assistantMessage"]["content"] == ""
    assert accepted["finishedAt"] is None
    assert accepted["errorCode"] is None
    assert accepted["errorContext"] is None
    assert accepted["evidence"] == []

    async with sessions() as session:
        assert await repository.mark_generating(session, request_id=request_id)
        await session.commit()
    generating = await read_status(client, settings, sessions, owner_id, request_id)
    assert generating == accepted | {"status": "generating"}

    async with sessions() as session:
        assert await repository.complete(
            session, request_id=request_id, answer="합성 완료 답변", now=FINISHED_AT
        )
        await session.commit()
    completed = await read_status(client, settings, sessions, owner_id, request_id)
    assert completed == accepted | {
        "status": "completed",
        "assistantMessage": accepted["assistantMessage"]
        | {"content": "합성 완료 답변"},
        "finishedAt": "2026-10-19T01:00:02Z",
    }
    assert len(request_sessions) == 3
    assert len({id(session) for session in request_sessions}) == 3


@pytest.mark.parametrize(
    "outcome", ["health_failure", "interrupted_accepted", "interrupted_generating"]
)
async def test_request_status_after_failure_or_interruption(
    client, settings, status_db, outcome
) -> None:
    """건강 실패·접수 또는 생성 중단 뒤 조회 → 저장된 부분 답변·실패 코드·문맥을 200으로 받는다."""
    sessions, (owner_id, _), request_sessions = status_db
    request_id = await accept_request(sessions, owner_id)
    if outcome != "interrupted_accepted":
        async with sessions() as session:
            assert await repository.mark_generating(session, request_id=request_id)
            await session.commit()
    progress = await read_status(client, settings, sessions, owner_id, request_id)
    assert progress["status"] == (
        "accepted" if outcome == "interrupted_accepted" else "generating"
    )
    assert progress["assistantMessage"]["content"] == ""
    assert progress["finishedAt"] is None

    async with sessions() as session:
        if outcome == "health_failure":
            assert await repository.fail(
                session,
                request_id=request_id,
                error_code="HEALTH_DATA_UNAVAILABLE",
                error_context={"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
                partial_answer="합성 건강 실패 부분 답변",
                now=FINISHED_AT,
            )
        else:
            assert await repository.interrupt_in_progress(session, now=FINISHED_AT) >= 1
        await session.commit()
    failed = await read_status(client, settings, sessions, owner_id, request_id)
    assert failed["status"] == "failed"
    assert failed["finishedAt"] == "2026-10-19T01:00:02Z"
    assert failed["evidence"] == []
    if outcome == "health_failure":
        assert failed["assistantMessage"]["content"] == "합성 건강 실패 부분 답변"
        assert failed["errorCode"] == "HEALTH_DATA_UNAVAILABLE"
        assert failed["errorContext"] == {
            "reason": "DATA_NOT_READY",
            "metrics": ["sleeps"],
        }
    else:
        assert failed["assistantMessage"]["content"] == ""
        assert failed["errorCode"] == "GENERATION_INTERRUPTED"
        assert failed["errorContext"] is None
    assert len(request_sessions) == 2
    assert request_sessions[0] is not request_sessions[1]


async def test_request_status_other_member_matches_missing(
    client, settings, status_db
) -> None:
    """DB에 있는 다른 회원의 요청 조회 → 없는 요청과 같은 404이며 소유자 상태를 보존한다."""
    sessions, (owner_id, other_id), _ = status_db
    request_id = await accept_request(sessions, owner_id)
    before = await read_status(client, settings, sessions, owner_id, request_id)
    other = await client.get(
        f"/api/v1/coaching/requests/{request_id}", headers=headers(other_id, settings)
    )
    missing = await client.get(
        f"/api/v1/coaching/requests/{uuid4()}", headers=headers(other_id, settings)
    )
    assert other.status_code == missing.status_code == 404
    assert (
        other.json()
        == missing.json()
        == {
            "error": {
                "code": "REQUEST_NOT_FOUND",
                "message": "요청을 찾을 수 없습니다.",
            }
        }
    )
    assert await read_status(client, settings, sessions, owner_id, request_id) == before


async def test_request_status_missing_in_database(client, settings, status_db) -> None:
    """DB에 없는 UUIDv4 요청 조회 → 404 REQUEST_NOT_FOUND를 받는다."""
    sessions, (owner_id, _), _ = status_db
    request_id = uuid4()
    async with sessions() as session:
        assert (
            await repository.get_request_state(
                session, user_id=owner_id, request_id=request_id
            )
            is None
        )
    response = await client.get(
        f"/api/v1/coaching/requests/{request_id}", headers=headers(owner_id, settings)
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "REQUEST_NOT_FOUND",
            "message": "요청을 찾을 수 없습니다.",
        }
    }
