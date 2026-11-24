"""가입 완료 회원. 스키마 생성은 Alembic이 담당한다."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("social_type", "social_user_id"),
        CheckConstraint("social_type IN ('GOOGLE', 'KAKAO')", name="social_type"),
        CheckConstraint("length(social_user_id) > 0", name="social_user_id"),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 12", name="display_name"
        ),
        CheckConstraint("birth_year >= 1900", name="birth_year"),
        CheckConstraint("gender IN ('MALE', 'FEMALE')", name="gender"),
        CheckConstraint(
            "height_cm > 0 AND height_cm <= 300 AND height_cm = round(height_cm, 2)",
            name="height_cm",
        ),
        CheckConstraint(
            "weight_kg > 0 AND weight_kg <= 500 AND weight_kg = round(weight_kg, 2)",
            name="weight_kg",
        ),
        CheckConstraint("updated_at >= created_at", name="timestamps"),
        CheckConstraint(
            "app_connection_status IN ('UNKNOWN', 'NOT_CONNECTED', 'CONNECTED')",
            name="app_connection_status",
        ),
        CheckConstraint(
            "(app_connection_status = 'CONNECTED') = (app_connected_at IS NOT NULL)",
            name="app_connection_time",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    social_type: Mapped[str] = mapped_column(Text)
    social_user_id: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    birth_year: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(Text)
    height_cm: Mapped[Decimal] = mapped_column(Numeric)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric)
    app_connection_status: Mapped[str] = mapped_column(
        Text, server_default=text("'NOT_CONNECTED'::text")
    )
    app_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
