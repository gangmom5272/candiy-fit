import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import signup
from app.auth.models import AuthState
from app.auth.schemas import SignupRequest
from app.core.errors import TemporaryStorageError
from app.users.validation import ProfileValidationError

NOW = datetime(2026, 10, 24, tzinfo=UTC)
BODY = {
    "signupToken": "synthetic-signup",
    "displayName": "합성회원",
    "birthYear": 2000,
    "gender": "FEMALE",
    "heightCm": 165.25,
    "weightKg": 55,
}


@pytest.fixture
def signup_service(monkeypatch):
    db = AsyncMock(spec=AsyncSession)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    db.begin.return_value = tx
    state = AuthState(
        purpose="SIGNUP",
        social_type="GOOGLE",
        social_user_id="synthetic-subject",
        platform_type="WEB",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    lookup = AsyncMock(return_value=state)
    monkeypatch.setattr(signup.repository, "find_auth_state", lookup)
    monkeypatch.setattr(signup, "_utc_now", lambda: NOW)
    return db, state, lookup, tx


async def invoke(db, settings, body=None, origin="http://localhost:5173"):
    body = BODY if body is None else body
    return await signup.complete_signup(
        db,
        SignupRequest.model_validate(body),
        json.dumps(body).encode(),
        origin,
        settings,
    )


@pytest.mark.parametrize(
    "invalid", ["missing", "consumed", "expired", "purpose", "platform"]
)
async def test_invalid_credential_precedes_origin_and_profile(
    signup_service, settings, invalid
):
    db, state, lookup, _ = signup_service
    if invalid == "missing":
        lookup.return_value = None
    elif invalid == "consumed":
        state.consumed_at = NOW
    elif invalid == "expired":
        state.expires_at = NOW
    elif invalid == "purpose":
        state.purpose = "ANDROID_LOGIN"
    else:
        state.platform_type = "OTHER"
    with pytest.raises(signup.SignupTokenInvalidError):
        await invoke(db, settings, BODY | {"gender": "OTHER"}, origin=None)
    assert lookup.await_count == 1
    db.flush.assert_not_awaited()


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example"])
async def test_web_origin_precedes_profile_and_preserves_state(
    signup_service, settings, origin
):
    db, state, lookup, _ = signup_service
    with pytest.raises(signup.OriginNotAllowedError):
        await invoke(db, settings, BODY | {"heightCm": -1}, origin=origin)
    assert state.consumed_at is None and lookup.await_count == 1


async def test_profile_error_does_not_lock_or_consume(signup_service, settings):
    db, state, lookup, _ = signup_service
    with pytest.raises(ProfileValidationError):
        await invoke(db, settings, BODY | {"gender": "OTHER"})
    assert lookup.await_count == 1 and state.consumed_at is None


@pytest.mark.parametrize(
    "failure,expected",
    [
        (ConnectionError(), TemporaryStorageError),
        (RuntimeError(), RuntimeError),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_storage_failure_and_cancellation_propagate(
    signup_service, settings, failure, expected
):
    db, _, lookup, tx = signup_service
    lookup.side_effect = failure
    with pytest.raises(expected):
        await invoke(db, settings)
    tx.__aexit__.assert_awaited_once()


async def test_clock_rechecked_after_lock(signup_service, settings, monkeypatch):
    db, state, _, _ = signup_service
    times = iter([NOW, state.expires_at])
    monkeypatch.setattr(signup, "_utc_now", lambda: next(times))
    with pytest.raises(signup.SignupTokenInvalidError):
        await invoke(db, settings)
    assert state.consumed_at is None


async def test_commit_failure_does_not_return_credentials(
    signup_service, settings, monkeypatch
):
    db, _, _, tx = signup_service
    tx.__aexit__.side_effect = ConnectionError()
    insert = AsyncMock(return_value=MagicMock())
    issue = AsyncMock()
    monkeypatch.setattr(signup.repository, "insert_social_user_if_absent", insert)
    monkeypatch.setattr(signup, "create_login_session", issue)
    with pytest.raises(TemporaryStorageError):
        await invoke(db, settings)
    issue.assert_awaited_once()
    tx.__aexit__.assert_awaited_once()
