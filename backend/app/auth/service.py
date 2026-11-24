"""최초 로그인 세션 발급. 호출자는 커밋 성공 후에만 결과를 외부에 전달한다."""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.contract import IssuedLoginSession, IssuedWebSession
from app.auth.models import AuthSession
from app.auth.repository import insert_initial_session
from app.auth.tokens import issue_access_token, require_utc
from app.core.config import Settings

PlatformType = Literal["WEB", "ANDROID"]


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def effective_session_expires_at(
    platform_type: PlatformType,
    expires_at: datetime,
    last_refreshed_at: datetime,
) -> datetime:
    require_utc(expires_at)
    require_utc(last_refreshed_at)
    if platform_type == "WEB":
        return expires_at
    if platform_type == "ANDROID":
        return min(expires_at, last_refreshed_at + timedelta(days=7))
    raise ValueError("unsupported platform")


async def create_login_session(
    db_session: AsyncSession,
    user_id: UUID,
    platform_type: PlatformType,
    settings: Settings,
    *,
    now: datetime,
) -> IssuedLoginSession | IssuedWebSession:
    require_utc(now)
    if platform_type not in {"WEB", "ANDROID"}:
        raise ValueError("unsupported platform")
    raw_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=7 if platform_type == "WEB" else 30)
    auth_session = AuthSession(
        user_id=user_id,
        platform_type=platform_type,
        created_at=now,
        last_refreshed_at=now,
        expires_at=expires_at,
        web_token_hash=hash_refresh_token(raw_token)
        if platform_type == "WEB"
        else None,
    )
    session_id = await insert_initial_session(
        db_session,
        auth_session,
        hash_refresh_token(raw_token) if platform_type == "ANDROID" else None,
        now=now,
    )
    if platform_type == "WEB":
        return IssuedWebSession(session_id, raw_token, expires_at)
    return IssuedLoginSession(
        session_id=session_id,
        access=issue_access_token(user_id, settings, now=now),
        refresh_token=raw_token,
        refresh_expires_at=effective_session_expires_at(platform_type, expires_at, now),
    )
