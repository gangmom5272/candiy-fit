"""Android challenge 발급·소비. 로그인 결과는 소비와 함께 커밋한 뒤 반환한다."""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.models import AuthState
from app.auth.providers import SocialProviderClient, invalid_credential
from app.auth.social import LoggedIn, SignupRequired, decide_social_login
from app.auth.tokens import require_utc
from app.auth.transactions import auth_transaction
from app.core.config import Settings
from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error


@dataclass(frozen=True, slots=True)
class IssuedLoginChallenge:
    challenge_token: str = field(repr=False)
    nonce: str = field(repr=False)
    expires_at: datetime


async def issue_login_challenge(
    db_session: AsyncSession,
    social_type: Literal["GOOGLE", "KAKAO"],
    *,
    now: datetime,
) -> IssuedLoginChallenge:
    require_utc(now)
    challenge_token = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=5)
    try:
        async with auth_transaction(db_session):
            await repository.insert_auth_state(
                db_session,
                AuthState(
                    purpose="ANDROID_LOGIN",
                    token_hash=hashlib.sha256(
                        challenge_token.encode("utf-8")
                    ).hexdigest(),
                    nonce_hash=hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
                    social_type=social_type,
                    platform_type="ANDROID",
                    created_at=now,
                    expires_at=expires_at,
                ),
            )
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return IssuedLoginChallenge(challenge_token, nonce, expires_at)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_challenge(
    state: AuthState | None, social_type: str, now: datetime
) -> AuthState:
    if (
        state is None
        or state.purpose != "ANDROID_LOGIN"
        or state.platform_type != "ANDROID"
        or state.social_type != social_type
        or not state.nonce_hash
        or state.consumed_at is not None
        or not state.created_at <= now < state.expires_at
    ):
        raise invalid_credential()
    return state


async def login_android(
    db_session: AsyncSession,
    provider: SocialProviderClient,
    settings: Settings,
    *,
    social_type: Literal["GOOGLE", "KAKAO"],
    id_token: str,
    challenge_token: str,
) -> LoggedIn | SignupRequired:
    """외부 검증 동안 트랜잭션을 닫고, 잠금 후 재검사·소비·회원 판정을 묶는다."""
    try:
        token_hash = hashlib.sha256(challenge_token.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise invalid_credential() from None
    try:
        async with auth_transaction(db_session):
            state = _validate_challenge(
                await repository.find_auth_state(db_session, token_hash),
                social_type,
                _utc_now(),
            )
            # 트랜잭션 밖에서는 ORM 객체 대신 검증에 필요한 값만 사용한다.
            nonce_hash = state.nonce_hash

        subject = await provider.verify_android(
            social_type, id_token, nonce_hash=nonce_hash
        )

        async with auth_transaction(db_session):
            state = await repository.find_auth_state(db_session, token_hash, lock=True)
            now = _utc_now()
            state = _validate_challenge(state, social_type, now)
            if state.nonce_hash != nonce_hash:
                raise invalid_credential()
            state.consumed_at = now
            await db_session.flush()
            result = await decide_social_login(
                db_session, subject, "ANDROID", settings, now=now
            )
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return result
