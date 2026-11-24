import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.health.models import SyncState
from app.users.models import User


@pytest.fixture
def make_user():
    def make(**changes):
        user_id = uuid4()
        fields = dict(
            id=user_id,
            social_type="GOOGLE",
            social_user_id=str(user_id),
            display_name="합성회원",
            birth_year=2000,
            gender="FEMALE",
            height_cm=Decimal("165.25"),
            weight_kg=Decimal("55"),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return User(**(fields | changes))

    return make


@pytest.fixture
async def user_database(make_user):
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
    members = [make_user(), make_user()]
    try:
        async with factory.begin() as db:
            db.add_all(members)
        yield factory, members
    finally:
        async with factory.begin() as db:
            await db.execute(
                delete(SyncState).where(
                    SyncState.end_user_id.in_([u.id for u in members])
                )
            )
            await db.execute(delete(User).where(User.id.in_([u.id for u in members])))
        await engine.dispose()
