"""인증·가입 저장. 전달받은 업무 트랜잭션을 종료하지 않는다."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.users.models import User
from app.users.validation import ProfileChanges


async def insert_initial_session(
    db_session: AsyncSession,
    auth_session: AuthSession,
    refresh_hash: str | None,
    *,
    now: datetime,
) -> UUID:
    try:
        db_session.add(auth_session)
        await db_session.flush()
        if refresh_hash is not None:
            db_session.add(
                AuthRefreshToken(
                    session_id=auth_session.id,
                    token_hash=refresh_hash,
                    created_at=now,
                )
            )
        await db_session.flush()
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return auth_session.id


async def find_web_session(
    db_session: AsyncSession, token_hash: str, *, lock: bool = False
) -> AuthSession | None:
    statement = (
        select(AuthSession)
        .where(
            AuthSession.web_token_hash == token_hash, AuthSession.platform_type == "WEB"
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return await db_session.scalar(statement)


async def find_social_user(
    db_session: AsyncSession, social_type: str, social_user_id: str
) -> User | None:
    return await db_session.scalar(
        select(User).where(
            User.social_type == social_type, User.social_user_id == social_user_id
        )
    )


async def insert_auth_state(db_session: AsyncSession, state: AuthState) -> None:
    db_session.add(state)
    await db_session.flush()


async def find_auth_state(
    db_session: AsyncSession, token_hash: str, *, lock: bool = False
) -> AuthState | None:
    statement = select(AuthState).where(AuthState.token_hash == token_hash)
    if lock:
        # 최초 조회 객체가 identity map에 있어도 잠금 획득 뒤 DB 값을 다시 읽는다.
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    return await db_session.scalar(statement)


async def insert_social_user_if_absent(
    db_session: AsyncSession,
    social_type: str,
    social_user_id: str,
    profile: ProfileChanges,
) -> User | None:
    return await db_session.scalar(
        insert(User)
        .values(social_type=social_type, social_user_id=social_user_id, **profile)
        .on_conflict_do_nothing(index_elements=[User.social_type, User.social_user_id])
        .returning(User)
    )


async def find_refresh_session_id(
    db_session: AsyncSession, token_hash: str
) -> UUID | None:
    return await db_session.scalar(
        select(AuthRefreshToken.session_id).where(
            AuthRefreshToken.token_hash == token_hash
        )
    )


async def lock_auth_session(
    db_session: AsyncSession, session_id: UUID
) -> AuthSession | None:
    return await db_session.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def find_refresh_token(
    db_session: AsyncSession, token_hash: str
) -> AuthRefreshToken | None:
    return await db_session.scalar(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.token_hash == token_hash)
        .execution_options(populate_existing=True)
    )


async def insert_rotated_refresh(
    db_session: AsyncSession, session_id: UUID, token_hash: str, *, now: datetime
) -> None:
    db_session.add(
        AuthRefreshToken(session_id=session_id, token_hash=token_hash, created_at=now)
    )
