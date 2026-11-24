import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import create_sessionmaker
from app.health.models import (
    ActivityDaily,
    BodyDaily,
    ExerciseSession,
    HeartDaily,
    LifestyleScore,
    RawHealthRecord,
    SleepSession,
    StepSnapshot,
    SyncState,
)
from app.users.models import User
from tests.db_support import connect_test_database


@pytest.fixture
async def health_db():
    raw_url = os.environ.get("HEALTH_TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail("HEALTH_TEST_DATABASE_URL에 Alembic을 적용한 전용 DB를 지정하세요")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "candiy_health_test"
    ):
        pytest.fail(
            "postgresql+asyncpg 및 candiy_health_test 접두사의 전용 DB가 필요합니다"
        )
    engine = create_async_engine(url)
    factory = create_sessionmaker(engine)
    owners = (uuid4(), uuid4())
    try:
        await connect_test_database(engine)
        try:
            yield factory, owners
        finally:
            async with factory.begin() as session:
                for model in (
                    LifestyleScore,
                    RawHealthRecord,
                    StepSnapshot,
                    ActivityDaily,
                    BodyDaily,
                    ExerciseSession,
                    HeartDaily,
                    SleepSession,
                    SyncState,
                ):
                    await session.execute(
                        delete(model).where(model.end_user_id.in_(owners))
                    )
    finally:
        await engine.dispose()


@pytest.fixture
async def status_db(health_db):
    """상태 API는 회원 설정도 읽으므로 실제 회원 행이 필요하다."""
    factory, owners = health_db
    async with factory.begin() as session:
        session.add_all(
            [
                User(
                    id=owner,
                    social_type="GOOGLE",
                    social_user_id=str(owner),
                    display_name="합성회원",
                    birth_year=2000,
                    gender="FEMALE",
                    height_cm=165,
                    weight_kg=55,
                )
                for owner in owners
            ]
        )
    try:
        yield health_db
    finally:
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id.in_(owners)))
