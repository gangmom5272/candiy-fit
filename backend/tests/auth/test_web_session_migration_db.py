"""325 추가/되돌림은 기존 Android 세션과 refresh 이력을 보존해야 한다."""

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.auth.models import AuthRefreshToken, AuthSession
from app.auth.service import create_login_session, hash_refresh_token

pytestmark = pytest.mark.db


async def test_android_rows_survive_web_column_upgrade(social_database, settings):
    factory, members, _ = social_database
    async with factory.begin() as db:
        issued = await create_login_session(
            db, members[0].id, "ANDROID", settings, now=datetime.now(UTC)
        )
    # social_database가 전용 candiy_auth_test DB 이름을 검사한 뒤에만 실행한다.
    url = os.environ["AUTH_TEST_DATABASE_URL"]
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    config.cmd_opts = SimpleNamespace(x=["url=" + url])
    try:
        await asyncio.to_thread(command.downgrade, config, "1e3fb416134d")
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            session = await db.get(AuthSession, issued.session_id)
            assert session.platform_type == "ANDROID"
            assert session.web_token_hash is None and session.revoked_at is None
            refresh = await db.scalar(
                select(AuthRefreshToken).where(
                    AuthRefreshToken.session_id == issued.session_id
                )
            )
            assert refresh.token_hash == hash_refresh_token(issued.refresh_token)
            assert refresh.used_at is None
    finally:
        await engine.dispose()
