"""동일 세션 행 잠금 아래 갱신 자격을 회전·폐기한다. 커밋 후에만 반환한다."""

import secrets
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.contract import IssuedLoginSession
from app.auth.models import AuthSession
from app.auth.service import (
    PlatformType,
    effective_session_expires_at,
    hash_refresh_token,
)
from app.auth.social import LoggedIn
from app.auth.tokens import issue_access_token
from app.auth.transactions import auth_transaction
from app.core.config import Settings
from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.users.service import get_current_profile


class RefreshRejectedError(Exception):
    """갱신 자격이 무효·만료·폐기·사용 완료이거나 전달 경로가 다르다."""


class RefreshUnavailableError(TemporaryStorageError):
    def __init__(self, *, retryable: bool) -> None:
        super().__init__("refresh_storage_unavailable")
        self.retryable = retryable


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _lock_session(
    db_session: AsyncSession, token_hash: str
) -> AuthSession | None:
    session_id = await repository.find_refresh_session_id(db_session, token_hash)
    if session_id is None:
        return None
    return await repository.lock_auth_session(db_session, session_id)


def _is_expired(session: AuthSession, now: datetime) -> bool:
    expires_at = effective_session_expires_at(
        session.platform_type, session.expires_at, session.last_refreshed_at
    )
    return now < session.created_at or now >= expires_at


async def refresh_session(
    db_session: AsyncSession,
    refresh_token: str,
    platform_type: PlatformType,
    settings: Settings,
) -> LoggedIn:
    """새 요청 DB 세션을 받는다. 재사용 폐기도 커밋한 뒤 거절한다."""
    commit_started = False
    reused = False
    token_hash = hash_refresh_token(refresh_token)
    try:
        async with auth_transaction(db_session):
            session = await _lock_session(db_session, token_hash)
            if session is None or session.platform_type != platform_type:
                raise RefreshRejectedError
            # 잠금을 기다리기 전의 토큰 snapshot/identity map을 사용하지 않는다.
            token = await repository.find_refresh_token(db_session, token_hash)
            now = _utc_now()
            if (
                token is None
                or session.revoked_at is not None
                or _is_expired(session, now)
            ):
                raise RefreshRejectedError
            if token.used_at is not None:
                session.revoked_at = now
                reused = True
            else:
                user = await get_current_profile(db_session, session.user_id)
                token.used_at = now
                # 부분 UNIQUE: INSERT보다 기존 미사용 행의 UPDATE를 먼저 확정한다.
                await db_session.flush()
                raw_refresh = secrets.token_urlsafe(32)
                await repository.insert_rotated_refresh(
                    db_session, session.id, hash_refresh_token(raw_refresh), now=now
                )
                session.last_refreshed_at = now
                result = LoggedIn(
                    user,
                    IssuedLoginSession(
                        session_id=session.id,
                        access=issue_access_token(session.user_id, settings, now=now),
                        refresh_token=raw_refresh,
                        refresh_expires_at=effective_session_expires_at(
                            platform_type, session.expires_at, now
                        ),
                    ),
                )
            await db_session.flush()
            # context manager 종료에서 COMMIT. 이후 연결 오류는 rollback으로도 결과를 확정할 수 없다.
            commit_started = True
    except TemporaryStorageError:
        raise RefreshUnavailableError(retryable=not commit_started) from None
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise RefreshUnavailableError(retryable=not commit_started) from None
        raise
    if reused:
        raise RefreshRejectedError
    return result


async def logout_session(
    db_session: AsyncSession, refresh_token: str, platform_type: PlatformType
) -> None:
    """현재 세션만 폐기한다. 토큰 이력과 다른 세션은 보존한다."""
    try:
        async with auth_transaction(db_session):
            session = await _lock_session(db_session, hash_refresh_token(refresh_token))
            if session is None:
                return
            if session.platform_type != platform_type:
                raise RefreshRejectedError
            now = _utc_now()
            if session.revoked_at is not None or _is_expired(session, now):
                return
            session.revoked_at = now
            await db_session.flush()
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("logout_storage_unavailable") from None
        raise
