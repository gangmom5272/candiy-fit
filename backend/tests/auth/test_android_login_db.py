import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSessionTransaction

from app.auth import android, repository, social
from app.auth.contract import VerifiedSocialSubject
from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.auth.providers import (
    ProviderConfigurationError,
    SocialProviderClient,
    SocialProviderError,
)
from app.auth.service import hash_refresh_token
from app.core.errors import TemporaryStorageError
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.fixture
async def android_db(social_database, monkeypatch):
    factory, _, _ = social_database
    hashes = []
    original = repository.insert_auth_state

    async def insert(db, state):
        hashes.append(state.token_hash)
        await original(db, state)

    monkeypatch.setattr(repository, "insert_auth_state", insert)
    monkeypatch.setattr(android, "_utc_now", lambda: NOW)
    try:
        yield social_database
    finally:
        async with factory.begin() as db:
            await db.execute(delete(AuthState).where(AuthState.token_hash.in_(hashes)))


async def issue(factory, provider="GOOGLE"):
    async with factory() as db:
        return await android.issue_login_challenge(db, provider, now=NOW)


async def state_for(db, challenge):
    return await repository.find_auth_state(
        db, hashlib.sha256(challenge.challenge_token.encode()).hexdigest()
    )


async def call(factory, settings, challenge, subject, provider=None):
    async with factory() as db:
        if provider is None:
            provider = AsyncMock(spec=SocialProviderClient)

            async def verify(social_type, token, *, nonce_hash):
                assert not db.in_transaction()
                assert social_type == subject.social_type
                assert token == "synthetic-id-token"
                assert (
                    nonce_hash == hashlib.sha256(challenge.nonce.encode()).hexdigest()
                )
                return subject

            provider.verify_android.side_effect = verify
        return await android.login_android(
            db,
            provider,
            settings,
            social_type=subject.social_type,
            id_token="synthetic-id-token",
            challenge_token=challenge.challenge_token,
        )


async def counts(factory, namespace):
    async with factory() as db:
        users = select(User.id).where(User.social_user_id.startswith(namespace))
        sessions = select(AuthSession.id).where(AuthSession.user_id.in_(users))
        return tuple(
            [
                await db.scalar(select(func.count()).select_from(model).where(where))
                for model, where in (
                    (User, User.id.in_(users)),
                    (AuthSession, AuthSession.id.in_(sessions)),
                    (AuthRefreshToken, AuthRefreshToken.session_id.in_(sessions)),
                    (AuthState, AuthState.social_user_id.startswith(namespace)),
                )
            ]
        )


@pytest.mark.parametrize("provider_name", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize("existing", [True, False])
async def test_commits_consumption_with_login_result(
    android_db, settings, provider_name, existing
):
    factory, members, namespace = android_db
    challenge = await issue(factory, provider_name)
    subject = VerifiedSocialSubject(
        provider_name, namespace if existing else namespace + "-new"
    )
    result = await call(factory, settings, challenge, subject)
    async with factory() as observer:
        state = await state_for(observer, challenge)
        assert state.consumed_at == NOW
        assert state.expires_at == NOW + timedelta(minutes=5)
        if existing:
            assert isinstance(result, social.LoggedIn)
            member = next(u for u in members if u.social_type == provider_name)
            assert result.user.id == member.id
            assert result.user.display_name == member.display_name
            assert result.user.updated_at == member.updated_at
            session = await observer.get(AuthSession, result.session.session_id)
            refresh = await observer.scalar(
                select(AuthRefreshToken).where(
                    AuthRefreshToken.session_id == session.id
                )
            )
            assert session.platform_type == "ANDROID"
            assert session.created_at == session.last_refreshed_at == NOW
            assert session.expires_at == NOW + timedelta(days=30)
            assert result.session.refresh_expires_at == NOW + timedelta(days=7)
            assert refresh.token_hash == hash_refresh_token(
                result.session.refresh_token
            )
        else:
            assert isinstance(result, social.SignupRequired)
            signup = await repository.find_auth_state(
                observer, social.hash_signup_token(result.signup_token)
            )
            assert signup.purpose == "SIGNUP" and signup.platform_type == "ANDROID"
            assert (
                signup.social_type == provider_name
                and signup.social_user_id == subject.social_user_id
            )
            assert signup.nonce_hash is None and signup.consumed_at is None
            assert signup.expires_at == result.expires_at == NOW + timedelta(minutes=10)
    expected = (2, 1, 1, 0) if existing else (2, 0, 0, 1)
    assert await counts(factory, namespace) == expected
    provider = AsyncMock(spec=SocialProviderClient)
    with pytest.raises(SocialProviderError) as error:
        await call(factory, settings, challenge, subject, provider)
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"
    provider.verify_android.assert_not_awaited()
    assert await counts(factory, namespace) == expected


@pytest.mark.parametrize(
    "failure",
    [
        SocialProviderError("SOCIAL_CREDENTIAL_INVALID"),
        SocialProviderError("SOCIAL_PROVIDER_UNAVAILABLE"),
        ProviderConfigurationError("synthetic_configuration_missing"),
        asyncio.CancelledError(),
    ],
)
async def test_provider_failure_preserves_challenge(android_db, settings, failure):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    subject = VerifiedSocialSubject("GOOGLE", namespace)
    provider = AsyncMock(spec=SocialProviderClient)
    provider.verify_android.side_effect = failure
    with pytest.raises(type(failure)):
        await call(factory, settings, challenge, subject, provider)
    assert provider.verify_android.await_count == 1
    async with factory() as observer:
        assert (await state_for(observer, challenge)).consumed_at is None
    assert await counts(factory, namespace) == (2, 0, 0, 0)
    assert isinstance(
        await call(factory, settings, challenge, subject), social.LoggedIn
    )


@pytest.mark.parametrize("existing", [True, False])
async def test_same_challenge_concurrently_has_one_result(
    android_db, settings, existing
):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    subject = VerifiedSocialSubject(
        "GOOGLE", namespace if existing else namespace + "-new"
    )
    barrier = asyncio.Barrier(2)
    provider = AsyncMock(spec=SocialProviderClient)

    async def verify(*args, **kwargs):
        await barrier.wait()
        return subject

    provider.verify_android.side_effect = verify
    async with asyncio.timeout(5):
        results = await asyncio.gather(
            call(factory, settings, challenge, subject, provider),
            call(factory, settings, challenge, subject, provider),
            return_exceptions=True,
        )
    errors = [r for r in results if isinstance(r, SocialProviderError)]
    assert len(errors) == 1 and errors[0].code == "SOCIAL_CREDENTIAL_INVALID"
    assert (
        sum(
            isinstance(r, social.LoggedIn if existing else social.SignupRequired)
            for r in results
        )
        == 1
    )
    assert await counts(factory, namespace) == (
        (2, 1, 1, 0) if existing else (2, 0, 0, 1)
    )


@pytest.mark.parametrize(
    "change", ["expired", "consumed", "nonce", "provider", "deleted"]
)
async def test_state_is_rechecked_after_provider(
    android_db, settings, monkeypatch, change
):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    subject = VerifiedSocialSubject("GOOGLE", namespace)
    provider = AsyncMock(spec=SocialProviderClient)

    async def verify(*args, **kwargs):
        if change == "expired":
            monkeypatch.setattr(android, "_utc_now", lambda: NOW + timedelta(minutes=5))
        else:
            async with factory.begin() as db:
                state = await state_for(db, challenge)
                if change == "deleted":
                    await db.delete(state)
                elif change == "consumed":
                    state.consumed_at = NOW
                elif change == "nonce":
                    state.nonce_hash = "b" * 64
                else:
                    state.social_type = "KAKAO"
        return subject

    provider.verify_android.side_effect = verify
    with pytest.raises(SocialProviderError) as error:
        await call(factory, settings, challenge, subject, provider)
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"
    assert await counts(factory, namespace) == (2, 0, 0, 0)


async def test_expiry_is_checked_after_waiting_for_row_lock(
    android_db, settings, monkeypatch
):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    subject = VerifiedSocialSubject("GOOGLE", namespace)
    original = repository.find_auth_state
    waiting = asyncio.Event()

    async def find(*args, **kwargs):
        if kwargs.get("lock"):
            waiting.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(repository, "find_auth_state", find)
    async with asyncio.timeout(5):
        async with factory.begin() as locker:
            state = await state_for(locker, challenge)
            await original(locker, state.token_hash, lock=True)
            task = asyncio.create_task(call(factory, settings, challenge, subject))
            await waiting.wait()
            monkeypatch.setattr(android, "_utc_now", lambda: NOW + timedelta(minutes=5))
        with pytest.raises(SocialProviderError) as error:
            await task
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"
    async with factory() as observer:
        assert (await state_for(observer, challenge)).consumed_at is None
    assert await counts(factory, namespace) == (2, 0, 0, 0)


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize(
    "failure,expected",
    [
        (ConnectionError("synthetic-private"), TemporaryStorageError),
        (RuntimeError("synthetic-private"), RuntimeError),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_failure_after_result_write_rolls_back_consumption(
    android_db, settings, monkeypatch, existing, failure, expected
):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    subject = VerifiedSocialSubject(
        "GOOGLE", namespace if existing else namespace + "-new"
    )
    original = android.decide_social_login

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise failure

    monkeypatch.setattr(android, "decide_social_login", fail)
    with pytest.raises(expected):
        await call(factory, settings, challenge, subject)
    async with factory() as observer:
        assert (await state_for(observer, challenge)).consumed_at is None
    assert await counts(factory, namespace) == (2, 0, 0, 0)


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize("committed", [True, False])
async def test_commit_failure_returns_no_result_and_never_retries(
    android_db, settings, monkeypatch, existing, committed
):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    subject = VerifiedSocialSubject(
        "GOOGLE", namespace if existing else namespace + "-new"
    )
    original = AsyncSessionTransaction.__aexit__
    exits = 0

    async def fail(transaction, *args):
        nonlocal exits
        exits += 1
        if exits == 2:
            if committed:
                await original(transaction, *args)
            else:
                await transaction.rollback()
            raise ConnectionError("synthetic-commit-failure")
        return await original(transaction, *args)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSessionTransaction, "__aexit__", fail)
        with pytest.raises(TemporaryStorageError):
            await call(factory, settings, challenge, subject)
    assert exits == 2
    async with factory() as observer:
        assert (
            (await state_for(observer, challenge)).consumed_at is not None
        ) == committed
    expected = (2, 1, 1, 0) if existing else (2, 0, 0, 1)
    assert await counts(factory, namespace) == (expected if committed else (2, 0, 0, 0))


async def test_initial_read_failure_does_not_call_provider(
    android_db, settings, monkeypatch
):
    factory, _, namespace = android_db
    challenge = await issue(factory)
    provider = AsyncMock(spec=SocialProviderClient)
    monkeypatch.setattr(
        repository, "find_auth_state", AsyncMock(side_effect=ConnectionError())
    )
    with pytest.raises(TemporaryStorageError):
        await call(
            factory,
            settings,
            challenge,
            VerifiedSocialSubject("GOOGLE", namespace),
            provider,
        )
    provider.verify_android.assert_not_awaited()
