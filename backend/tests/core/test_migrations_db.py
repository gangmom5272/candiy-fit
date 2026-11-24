"""Alembic 리비전과 모델(app.models)이 같은 스키마를 만드는지 검증한다.

AUTH_TEST_DATABASE_URL DB에 `alembic upgrade head`를 적용한 뒤 실행한다 (docs/auth-account-schema.md "검증 재현").
모델만 고치고 리비전을 빠뜨리면 여기서 깨진다. `uv run alembic check`와 같은 검사다.
"""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import metadata

pytestmark = pytest.mark.db


async def test_models_match_migrations():
    url = os.environ.get("AUTH_TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "AUTH_TEST_DATABASE_URL에 Alembic을 적용한 별도 테스트 DB를 지정하세요"
        )
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM exercise_code")) == 61

            def diff(sync_conn):
                opts = {"compare_type": True, "compare_server_default": True}
                return compare_metadata(
                    MigrationContext.configure(sync_conn, opts=opts), metadata
                )

            assert await conn.run_sync(diff) == []
    finally:
        await engine.dispose()
