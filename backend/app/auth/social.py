"""검증된 소셜 주체의 회원 판정. 공통 판정은 호출자의 트랜잭션에 참여한다."""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.contract import (
    IssuedLoginSession,
    IssuedWebSession,
    VerifiedSocialSubject,
)
from app.auth.models import AuthState
from app.auth.providers import SocialProviderClient
from app.auth.schemas import WebSocialLoginRequest
from app.auth.service import PlatformType, create_login_session
from app.auth.tokens import require_utc
from app.auth.transactions import auth_transaction
from app.core.config import Settings
from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.users.models import User


@dataclass(frozen=True, slots=True)
class LoggedIn:
    user: User
    session: IssuedLoginSession | IssuedWebSession


@dataclass(frozen=True, slots=True)
class SignupRequired:
    signup_token: str = field(repr=False)
    expires_at: datetime
    social_type: Literal["GOOGLE", "KAKAO"]


def hash_signup_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def decide_social_login(
    db_session: AsyncSession,
    subject: VerifiedSocialSubject,
    platform_type: PlatformType,
    settings: Settings,
    *,
    now: datetime,
) -> LoggedIn | SignupRequired:
    """외부 검증 완료 후 트랜잭션 안에서 호출한다. 커밋·롤백은 호출자 책임이다."""
    require_utc(now)
    if platform_type not in {"WEB", "ANDROID"}:
        raise ValueError("unsupported platform")
    user = await repository.find_social_user(
        db_session, subject.social_type, subject.social_user_id
    )
    if user is not None:
        session = await create_login_session(
            db_session, user.id, platform_type, settings, now=now
        )
        return LoggedIn(user, session)
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=10)
    await repository.insert_auth_state(
        db_session,
        AuthState(
            purpose="SIGNUP",
            token_hash=hash_signup_token(token),
            social_type=subject.social_type,
            platform_type=platform_type,
            social_user_id=subject.social_user_id,
            created_at=now,
            expires_at=expires_at,
        ),
    )
    return SignupRequired(token, expires_at, subject.social_type)


async def complete_social_login(
    db_session: AsyncSession,
    subject: VerifiedSocialSubject,
    platform_type: PlatformType,
    settings: Settings,
    *,
    now: datetime,
) -> LoggedIn | SignupRequired:
    """WEB 최초 로그인 저장을 소유한다. 커밋 성공 뒤에만 자격을 반환한다."""
    try:
        async with auth_transaction(db_session):
            result = await decide_social_login(
                db_session, subject, platform_type, settings, now=now
            )
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return result


async def login_web(
    db_session: AsyncSession,
    provider: SocialProviderClient,
    payload: WebSocialLoginRequest,
    settings: Settings,
) -> LoggedIn | SignupRequired:
    subject = await provider.verify_web(payload)
    return await complete_social_login(
        db_session, subject, "WEB", settings, now=datetime.now(UTC)
    )
