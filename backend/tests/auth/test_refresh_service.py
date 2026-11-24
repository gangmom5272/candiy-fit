import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sessions, transactions
from app.auth.models import AuthRefreshToken, AuthSession
from app.auth.service import hash_refresh_token
from app.core.errors import TemporaryStorageError
from app.users.service import UserNotFoundError

NOW = datetime(2031, 1, 2, tzinfo=UTC)


@pytest.fixture
def rotation(monkeypatch):
    db = AsyncMock(spec=AsyncSession)
    committed = []

    @asynccontextmanager
    async def begin():
        yield
        await db.commit()
        committed.append(True)

    db.begin = begin
    session = AuthSession(
        id=uuid4(),
        user_id=uuid4(),
        platform_type="WEB",
        created_at=NOW - timedelta(days=1),
        last_refreshed_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=6),
    )
    token = AuthRefreshToken(
        id=uuid4(),
        session_id=session.id,
        token_hash=hash_refresh_token("synthetic"),
        created_at=session.created_at,
    )
    monkeypatch.setattr(sessions, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        sessions.repository,
        "find_refresh_session_id",
        AsyncMock(return_value=session.id),
    )
    monkeypatch.setattr(
        sessions.repository, "lock_auth_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(
        sessions.repository, "find_refresh_token", AsyncMock(return_value=token)
    )
    monkeypatch.setattr(sessions.repository, "insert_rotated_refresh", AsyncMock())
    monkeypatch.setattr(
        sessions, "get_current_profile", AsyncMock(return_value=object())
    )
    return db, session, token, committed


async def test_reuse_commits_revocation_before_rejecting(rotation, settings):
    db, session, token, committed = rotation
    token.used_at = NOW - timedelta(hours=1)
    with pytest.raises(sessions.RefreshRejectedError):
        await sessions.refresh_session(db, "synthetic", "WEB", settings)
    assert session.revoked_at == NOW
    assert committed == [True]
    sessions.repository.insert_rotated_refresh.assert_not_called()


@pytest.mark.parametrize("stage", ["lookup", "profile", "flush", "commit"])
async def test_storage_failure_retryable_depends_on_commit_attempt(
    rotation, settings, stage
):
    db, _, _, _ = rotation
    target = {
        "lookup": sessions.repository.find_refresh_session_id,
        "profile": sessions.get_current_profile,
        "flush": db.flush,
        "commit": db.commit,
    }[stage]
    target.side_effect = (
        TemporaryStorageError() if stage == "profile" else ConnectionError()
    )
    with pytest.raises(sessions.RefreshUnavailableError) as error:
        await sessions.refresh_session(db, "synthetic", "WEB", settings)
    assert error.value.retryable is (stage != "commit")


@pytest.mark.parametrize(
    "error",
    [
        IntegrityError(None, None, Exception()),
        RuntimeError("synthetic-secret"),
        asyncio.CancelledError(),
    ],
)
async def test_unexpected_errors_and_cancellation_are_not_storage_outages(
    rotation, settings, error
):
    db, _, _, _ = rotation
    db.flush.side_effect = error
    with pytest.raises(type(error)) as caught:
        await sessions.refresh_session(db, "synthetic", "WEB", settings)
    assert caught.value is error
    db.commit.assert_not_called()


async def test_missing_member_does_not_consume_refresh(rotation, settings):
    db, _, token, _ = rotation
    sessions.get_current_profile.side_effect = UserNotFoundError()
    with pytest.raises(UserNotFoundError):
        await sessions.refresh_session(db, "synthetic", "WEB", settings)
    assert token.used_at is None
    db.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["lookup", "flush", "commit"])
async def test_logout_storage_failure_is_not_success(rotation, stage):
    db, _, _, _ = rotation
    target = {
        "lookup": sessions.repository.find_refresh_session_id,
        "flush": db.flush,
        "commit": db.commit,
    }[stage]
    target.side_effect = ConnectionError()
    with pytest.raises(TemporaryStorageError):
        await sessions.logout_session(db, "synthetic", "WEB")


async def test_logout_cancellation_propagates(rotation):
    db, _, _, _ = rotation
    db.flush.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await sessions.logout_session(db, "synthetic", "WEB")
    db.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["lookup", "profile", "flush", "commit"])
async def test_refresh_wait_timeout_preserves_commit_uncertainty(
    rotation, settings, monkeypatch, stage
):
    db, _, _, _ = rotation
    monkeypatch.setattr(transactions, "AUTH_DB_TIMEOUT_SECONDS", 0.02)

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    target = {
        "lookup": sessions.repository.find_refresh_session_id,
        "profile": sessions.get_current_profile,
        "flush": db.flush,
        "commit": db.commit,
    }[stage]
    target.side_effect = blocked
    with pytest.raises(sessions.RefreshUnavailableError) as caught:
        await sessions.refresh_session(db, "synthetic", "WEB", settings)
    assert caught.value.retryable is (stage != "commit")


async def test_logout_wait_timeout_is_not_success(rotation, monkeypatch):
    db, _, _, _ = rotation
    monkeypatch.setattr(transactions, "AUTH_DB_TIMEOUT_SECONDS", 0.02)

    async def blocked(*args):
        await asyncio.Event().wait()

    sessions.repository.lock_auth_session.side_effect = blocked
    with pytest.raises(TemporaryStorageError):
        await sessions.logout_session(db, "synthetic", "WEB")
    db.commit.assert_not_called()
