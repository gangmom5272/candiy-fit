"""로그인 세션·refresh 이력·일회성 인증 상태. 처리 계약은 docs/auth-account-schema.md."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.users.models import User


class AuthSession(Base):
    __tablename__ = "auth_session"
    __table_args__ = (
        CheckConstraint("platform_type IN ('WEB', 'ANDROID')", name="platform_type"),
        CheckConstraint(
            "web_token_hash IS NULL OR (platform_type = 'WEB' AND web_token_hash ~ '^[0-9a-f]{64}$')",
            name="web_token_hash",
        ),
        UniqueConstraint("web_token_hash"),
        CheckConstraint(
            "expires_at > created_at AND last_refreshed_at >= created_at AND last_refreshed_at < expires_at "
            "AND (revoked_at IS NULL OR revoked_at >= created_at)",
            name="timestamps",
        ),
        Index("ix_auth_session_user_id", "user_id"),
        Index("ix_auth_session_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey(User.id, ondelete="RESTRICT"))
    platform_type: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    web_token_hash: Mapped[str | None] = mapped_column(Text)


class AuthRefreshToken(Base):
    __tablename__ = "auth_refresh_token"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
        CheckConstraint("used_at IS NULL OR used_at >= created_at", name="timestamps"),
        Index("ix_auth_refresh_token_session_id", "session_id"),
        Index(
            "uq_auth_refresh_token_current",
            "session_id",
            unique=True,
            postgresql_where=text("used_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey(AuthSession.id, ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthState(Base):
    __tablename__ = "auth_state"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
        CheckConstraint("social_type IN ('GOOGLE', 'KAKAO')", name="social_type"),
        CheckConstraint("platform_type IN ('WEB', 'ANDROID')", name="platform_type"),
        CheckConstraint(
            "(purpose = 'ANDROID_LOGIN' AND platform_type = 'ANDROID' AND social_user_id IS NULL "
            "AND nonce_hash IS NOT NULL AND nonce_hash ~ '^[0-9a-f]{64}$') "
            "OR (purpose = 'SIGNUP' AND social_user_id IS NOT NULL "
            "AND length(social_user_id) > 0 AND nonce_hash IS NULL)",
            name="payload",
        ),
        CheckConstraint(
            "expires_at > created_at "
            "AND (consumed_at IS NULL OR (consumed_at >= created_at AND consumed_at < expires_at))",
            name="timestamps",
        ),
        Index("ix_auth_state_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    purpose: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    social_type: Mapped[str] = mapped_column(Text)
    platform_type: Mapped[str] = mapped_column(Text)
    social_user_id: Mapped[str | None] = mapped_column(Text)
    nonce_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
