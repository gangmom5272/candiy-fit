import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.models import AuthSession, AuthState
from app.users.models import User


@pytest.fixture
async def social_database():
    raw = os.environ.get("AUTH_TEST_DATABASE_URL")
    if not raw:
        pytest.fail("AUTH_TEST_DATABASE_URL with a dedicated migrated DB is required")
    url = make_url(raw)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "candiy_auth_test"
    ):
        pytest.fail("Only a dedicated candiy_auth_test PostgreSQL DB is allowed")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    namespace = "synthetic-" + uuid4().hex
    now = datetime(2026, 1, 1, tzinfo=UTC)
    members = [
        User(
            id=uuid4(),
            social_type=provider,
            social_user_id=namespace,
            display_name="합성회원",
            birth_year=2000,
            gender="FEMALE",
            height_cm=Decimal("165.25"),
            weight_kg=Decimal("55"),
            created_at=now,
            updated_at=now,
        )
        for provider in ("GOOGLE", "KAKAO")
    ]
    try:
        async with factory.begin() as db:
            db.add_all(members)
        yield factory, members, namespace
    finally:
        owned_members = User.id.in_(
            [member.id for member in members]
        ) | User.social_user_id.startswith(namespace)
        try:
            async with factory.begin() as db:
                await db.execute(
                    delete(AuthState).where(
                        AuthState.social_user_id.startswith(namespace)
                    )
                )
                await db.execute(
                    delete(AuthSession).where(
                        AuthSession.user_id.in_(select(User.id).where(owned_members))
                    )
                )
                await db.execute(delete(User).where(owned_members))
            async with factory() as observer:
                # 소셜 식별자가 바뀌어도 fixture가 만든 회원은 남으면 안 된다.
                assert (
                    await observer.scalar(
                        select(User.id)
                        .where(User.id.in_([member.id for member in members]))
                        .limit(1)
                    )
                    is None
                ), "fixture members remain after cleanup"
        finally:
            await engine.dispose()
