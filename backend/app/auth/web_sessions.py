"""웹 자격은 읽을 때 회전하지 않는다. 각 호출은 새 DB 세션을 받는다."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.contract import AuthenticationError
from app.auth.models import AuthSession
from app.auth.service import hash_refresh_token
from app.auth.transactions import auth_transaction
from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def authenticate_web_session(db: AsyncSession, token: str) -> AuthSession:
    """짧은 읽기 트랜잭션을 종료한 뒤 반환한다. 자격과 만료를 갱신하지 않는다."""
    try:
        async with auth_transaction(db):
            session = await repository.find_web_session(db, hash_refresh_token(token))
            now = _utc_now()
            if (
                session is None
                or session.revoked_at is not None
                or now < session.created_at
            ):
                raise AuthenticationError("AUTH_REQUIRED")
            if now >= session.expires_at:
                raise AuthenticationError("AUTH_EXPIRED")
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("web_session_unavailable") from None
        raise
    return session


async def logout_web_session(db: AsyncSession, token: str) -> None:
    """폐기 커밋 뒤 반환하며 이미 폐기했거나 없는 자격도 성공한다."""
    try:
        async with auth_transaction(db):
            session = await repository.find_web_session(
                db, hash_refresh_token(token), lock=True
            )
            if session is not None and session.revoked_at is None:
                session.revoked_at = max(_utc_now(), session.created_at)
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("web_session_unavailable") from None
        raise
