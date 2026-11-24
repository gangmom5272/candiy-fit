import asyncio
import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from asyncpg.exceptions import (
    CannotConnectNowError,
    ConnectionDoesNotExistError,
    PostgresSyntaxError,
    TooManyConnectionsError,
)
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.models import AuthRefreshToken, AuthSession
from app.core.errors import TemporaryStorageError

NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.fixture
def db():
    session = AsyncMock(spec=AsyncSession)

    async def flush():
        obj = session.add.call_args.args[0]
        obj.id = uuid4()

    session.flush.side_effect = flush
    return session


@pytest.mark.parametrize(("platform", "days"), [("WEB", 7), ("ANDROID", 30)])
async def test_initial_session(db, settings, platform, days):
    user_id = uuid4()
    issued = await service.create_login_session(
        db, user_id, platform, settings, now=NOW
    )
    if platform == "WEB":
        from app.auth.contract import IssuedWebSession

        assert isinstance(issued, IssuedWebSession)
        db.add.assert_called_once()
        saved = db.add.call_args.args[0]
        assert saved.web_token_hash == service.hash_refresh_token(issued.token)
        assert saved.expires_at == issued.expires_at == NOW + timedelta(days=7)
        assert saved.platform_type == "WEB"
        assert issued.token not in repr(issued)
        db.commit.assert_not_called()
        return
    auth_session, refresh = [call.args[0] for call in db.add.call_args_list]
    assert isinstance(auth_session, AuthSession)
    assert isinstance(refresh, AuthRefreshToken)
    assert auth_session.user_id == user_id
    assert auth_session.platform_type == platform
    assert (
        auth_session.created_at
        == auth_session.last_refreshed_at
        == refresh.created_at
        == NOW
    )
    assert auth_session.expires_at == NOW + timedelta(days=days)
    assert issued.refresh_expires_at == NOW + timedelta(days=7)
    assert issued.session_id == auth_session.id == refresh.session_id
    assert len(base64.urlsafe_b64decode(issued.refresh_token + "=")) == 32
    assert refresh.token_hash == service.hash_refresh_token(issued.refresh_token)
    assert refresh.token_hash != issued.refresh_token
    assert refresh.used_at is None
    assert issued.refresh_token not in repr(issued)
    assert issued.access.token not in repr(issued)
    db.begin.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("platform", "last_day", "effective_day"),
    [
        ("WEB", 6, 7),
        ("ANDROID", 0, 7),
        ("ANDROID", 22, 29),
        ("ANDROID", 29, 30),
    ],
)
def test_effective_expiry(platform, last_day, effective_day):
    fixed = NOW + timedelta(days=7 if platform == "WEB" else 30)
    effective = service.effective_session_expires_at(
        platform, fixed, NOW + timedelta(days=last_day)
    )
    assert effective == NOW + timedelta(days=effective_day)


@pytest.mark.parametrize(("offset", "expected_offset"), [(-1, -1), (0, 0), (1, 0)])
def test_android_idle_limit_meets_fixed_limit(offset, expected_offset):
    fixed = NOW + timedelta(days=30)
    last = NOW + timedelta(days=23, seconds=offset)
    assert service.effective_session_expires_at(
        "ANDROID", fixed, last
    ) == fixed + timedelta(seconds=expected_offset)


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(),
        TimeoutError(),
        PoolTimeoutError(),
        CannotConnectNowError(),
        TooManyConnectionsError(),
        DBAPIError(None, None, ConnectionDoesNotExistError()),
        DBAPIError(None, None, Exception(), connection_invalidated=True),
    ],
)
async def test_transient_storage_errors(db, settings, error):
    db.flush.side_effect = error
    with pytest.raises(TemporaryStorageError):
        await service.create_login_session(db, uuid4(), "WEB", settings, now=NOW)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        IntegrityError(None, None, Exception()),
        OperationalError(None, None, Exception()),
        DBAPIError(None, None, PostgresSyntaxError()),
        PostgresSyntaxError(),
        RuntimeError("synthetic-secret"),
        asyncio.CancelledError(),
    ],
)
async def test_unexpected_or_cancelled_errors_propagate(db, settings, error):
    db.flush.side_effect = error
    with pytest.raises(type(error)) as caught:
        await service.create_login_session(db, uuid4(), "WEB", settings, now=NOW)
    assert caught.value is error
    db.rollback.assert_not_called()


async def test_failed_second_insert_does_not_commit_or_retry(db, settings):
    calls = 0

    async def flush():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IntegrityError(None, None, Exception())
        db.add.call_args.args[0].id = uuid4()

    db.flush.side_effect = flush
    with pytest.raises(IntegrityError):
        await service.create_login_session(db, uuid4(), "WEB", settings, now=NOW)
    assert calls == 2
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


async def test_invalid_platform_before_writes(db, settings):
    with pytest.raises(ValueError, match="platform"):
        await service.create_login_session(db, uuid4(), "OTHER", settings, now=NOW)
    db.add.assert_not_called()
