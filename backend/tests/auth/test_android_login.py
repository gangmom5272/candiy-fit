from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.auth import android, repository
from app.auth.models import AuthState
from app.auth.providers import SocialProviderClient, SocialProviderError

NOW = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.mark.parametrize(
    "changes",
    [
        None,
        {"purpose": "SIGNUP"},
        {"platform_type": "WEB"},
        {"social_type": "KAKAO"},
        {"nonce_hash": None},
        {"nonce_hash": ""},
        {"consumed_at": NOW},
        {"expires_at": NOW},
        {"created_at": NOW + timedelta(seconds=1)},
    ],
)
async def test_invalid_challenge_never_calls_provider(settings, monkeypatch, changes):
    state = AuthState(
        purpose="ANDROID_LOGIN",
        platform_type="ANDROID",
        social_type="GOOGLE",
        nonce_hash="a" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    if changes is None:
        state = None
    else:
        for name, value in changes.items():
            setattr(state, name, value)

    @asynccontextmanager
    async def begin():
        yield

    db = AsyncMock()
    db.begin = begin
    provider = AsyncMock(spec=SocialProviderClient)
    monkeypatch.setattr(android, "_utc_now", lambda: NOW)
    find = AsyncMock(return_value=state)
    monkeypatch.setattr(repository, "find_auth_state", find)
    with pytest.raises(SocialProviderError) as error:
        await android.login_android(
            db,
            provider,
            settings,
            social_type="GOOGLE",
            id_token="synthetic-id-token",
            challenge_token="synthetic-challenge",
        )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"
    provider.verify_android.assert_not_awaited()
    assert find.await_count == 1


async def test_non_utf8_challenge_is_invalid_before_storage(settings):
    db = AsyncMock()
    provider = AsyncMock(spec=SocialProviderClient)
    with pytest.raises(SocialProviderError) as error:
        await android.login_android(
            db,
            provider,
            settings,
            social_type="GOOGLE",
            id_token="synthetic-id-token",
            challenge_token="\ud800",
        )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"
    db.begin.assert_not_called()
    provider.verify_android.assert_not_awaited()
