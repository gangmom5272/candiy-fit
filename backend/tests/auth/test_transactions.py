import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import router, transactions
from app.auth.web import SESSION_COOKIE
from app.core.db import get_session


@pytest.fixture
def db():
    session = AsyncMock(spec=AsyncSession)

    @asynccontextmanager
    async def begin():
        try:
            yield
        except BaseException:
            await session.rollback()
            raise
        else:
            await session.commit()

    session.begin = begin
    return session


@pytest.mark.parametrize("sqlstate", ["57014", "55P03", "23505"])
async def test_only_database_wait_errors_become_timeouts(db, sqlstate):
    original = Exception("synthetic SQL with private data")
    original.sqlstate = sqlstate
    error = DBAPIError("synthetic SQL", {}, original)
    with pytest.raises(TimeoutError if sqlstate != "23505" else DBAPIError) as caught:
        async with transactions.auth_transaction(db):
            raise error
    if sqlstate == "23505":
        assert caught.value is error
    else:
        assert "private data" not in str(caught.value)
    db.rollback.assert_awaited_once()
    db.commit.assert_not_called()


async def test_deadline_covers_connection_acquisition(db, monkeypatch):
    monkeypatch.setattr(transactions, "AUTH_DB_TIMEOUT_SECONDS", 0.02)

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    db.execute.side_effect = blocked
    with pytest.raises(TimeoutError):
        async with transactions.auth_transaction(db):
            pytest.fail("entered before connection acquisition")
    db.rollback.assert_awaited_once()


async def test_caller_cancellation_is_preserved(db):
    with pytest.raises(asyncio.CancelledError):
        async with transactions.auth_transaction(db):
            raise asyncio.CancelledError
    db.rollback.assert_awaited_once()
    db.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["acquisition", "profile", "commit"])
async def test_web_session_profile_timeout_is_auth_unavailable(
    db, app, client, monkeypatch, stage
):
    async def db_session():
        yield db

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    app.dependency_overrides[get_session] = db_session
    monkeypatch.setattr(transactions, "AUTH_DB_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(
        router.web_sessions,
        "authenticate_web_session",
        AsyncMock(return_value=SimpleNamespace(user_id="synthetic-user")),
    )
    profile = AsyncMock()
    monkeypatch.setattr(router, "get_current_profile", profile)
    {"acquisition": db.execute, "profile": profile, "commit": db.commit}[
        stage
    ].side_effect = blocked
    client.cookies.set(SESSION_COOKIE, "synthetic-web-session")
    response = await client.get("/api/v1/auth/session")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
