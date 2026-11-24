"""실제 업무 트랜잭션과 독립 observer로 조기 커밋·부분 저장을 검출한다."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import service, tokens
from app.auth.contract import AuthenticationError
from app.auth.models import AuthRefreshToken, AuthSession
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.fixture
async def database():
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
    user_ids = []
    try:
        yield factory, user_ids
    finally:
        # 커밋 사례가 만든 합성 UUID에 한정한다. 다른 데이터는 정리하지 않는다.
        async with factory.begin() as cleanup:
            await cleanup.execute(
                delete(AuthSession).where(AuthSession.user_id.in_(user_ids))
            )
            await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


def member(user_ids):
    user_id = uuid4()
    user_ids.append(user_id)
    return User(
        id=user_id,
        social_type="GOOGLE",
        social_user_id=str(user_id),
        display_name="합성회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=165,
        weight_kg=55,
        created_at=NOW,
        updated_at=NOW,
    )


async def observed_counts(factory, user_id):
    async with factory() as observer:
        users = await observer.scalar(
            select(func.count()).select_from(User).where(User.id == user_id)
        )
        sessions = await observer.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == user_id)
        )
        refreshes = await observer.scalar(
            select(func.count())
            .select_from(AuthRefreshToken)
            .join(AuthSession)
            .where(AuthSession.user_id == user_id)
        )
        return users, sessions, refreshes


@pytest.mark.parametrize(("platform", "days"), [("WEB", 7), ("ANDROID", 30)])
async def test_commit_visibility_and_hash_only_storage(
    database, settings, platform, days
):
    factory, user_ids = database
    user = member(user_ids)
    async with factory() as db:
        async with db.begin():
            db.add(user)
            await db.flush()
            issued = await service.create_login_session(
                db, user.id, platform, settings, now=NOW
            )
            assert await observed_counts(factory, user.id) == (0, 0, 0)
        assert await observed_counts(factory, user.id) == (
            1,
            1,
            int(platform == "ANDROID"),
        )
    async with factory() as observer:
        saved = await observer.get(AuthSession, issued.session_id)
        if platform == "WEB":
            assert saved.web_token_hash == service.hash_refresh_token(issued.token)
            assert saved.expires_at == issued.expires_at == NOW + timedelta(days=7)
            assert issued.token not in [
                getattr(saved, c.key) for c in saved.__table__.columns
            ]
            return
        refresh = (
            await observer.scalars(
                select(AuthRefreshToken).where(AuthRefreshToken.session_id == saved.id)
            )
        ).one()
        assert saved.user_id == user.id and saved.platform_type == platform
        assert saved.created_at == saved.last_refreshed_at == refresh.created_at == NOW
        assert saved.expires_at == NOW + timedelta(days=days)
        assert issued.refresh_expires_at == NOW + timedelta(days=7)
        assert refresh.token_hash == service.hash_refresh_token(issued.refresh_token)
        assert len(refresh.token_hash) == 64 and refresh.used_at is None
        for record in (saved, refresh):
            values = [
                getattr(record, column.key) for column in record.__table__.columns
            ]
            assert issued.refresh_token not in values
            assert issued.access.token not in values


async def test_caller_rollback_removes_entire_signup_bundle(database, settings):
    factory, user_ids = database
    user = member(user_ids)
    with pytest.raises(RuntimeError, match="caller failure"):
        async with factory.begin() as db:
            db.add(user)
            await db.flush()
            await service.create_login_session(db, user.id, "WEB", settings, now=NOW)
            assert await observed_counts(factory, user.id) == (0, 0, 0)
            raise RuntimeError("caller failure")
    assert await observed_counts(factory, user.id) == (0, 0, 0)


async def test_actual_refresh_constraint_failure_leaves_no_session(
    database, settings, monkeypatch
):
    factory, user_ids = database
    user = member(user_ids)
    monkeypatch.setattr(service, "hash_refresh_token", lambda _: "invalid-hash")
    with pytest.raises(IntegrityError):
        async with factory.begin() as db:
            db.add(user)
            await db.flush()
            await service.create_login_session(db, user.id, "WEB", settings, now=NOW)
    assert await observed_counts(factory, user.id) == (0, 0, 0)


async def test_separate_logins_and_revocation_do_not_revoke_access(
    database, settings, monkeypatch
):
    factory, user_ids = database
    user = member(user_ids)
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(user)
        await db.flush()
        first = await service.create_login_session(
            db, user.id, "ANDROID", settings, now=NOW
        )
        second = await service.create_login_session(
            db, user.id, "ANDROID", settings, now=NOW
        )
        assert first.session_id != second.session_id
        assert first.refresh_token != second.refresh_token
        session = await db.get(AuthSession, first.session_id)
        session.revoked_at = NOW
    assert await observed_counts(factory, user.id) == (1, 2, 2)
    async with factory() as observer:
        for session_id in (first.session_id, second.session_id):
            assert (
                await observer.scalar(
                    select(func.count())
                    .select_from(AuthRefreshToken)
                    .where(
                        AuthRefreshToken.session_id == session_id,
                        AuthRefreshToken.used_at.is_(None),
                    )
                )
                == 1
            )
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(seconds=899))
    assert tokens.authenticate_token(first.access.token, settings).user_id == user.id
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(seconds=900))
    with pytest.raises(AuthenticationError, match="AUTH_EXPIRED"):
        tokens.authenticate_token(first.access.token, settings)
