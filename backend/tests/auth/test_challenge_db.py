import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSessionTransaction

from app.auth import repository
from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.core.db import get_session
from app.users.models import User

pytestmark = pytest.mark.db
PATH = "/api/v1/auth/social/challenge"
BODY = {"socialType": "GOOGLE", "platformType": "ANDROID"}


@pytest.fixture
async def challenge_db(app, social_database, monkeypatch):
    factory, _, _ = social_database
    owned_hashes = []
    original = repository.insert_auth_state

    async def insert(db, state):
        owned_hashes.append(state.token_hash)
        await original(db, state)

    async def session():
        async with factory() as db:
            yield db

    monkeypatch.setattr(repository, "insert_auth_state", insert)
    app.dependency_overrides[get_session] = session
    try:
        yield factory, owned_hashes
    finally:
        async with factory.begin() as db:
            await db.execute(
                delete(AuthState).where(AuthState.token_hash.in_(owned_hashes))
            )


async def member_counts(factory):
    async with factory() as db:
        return tuple(
            [
                await db.scalar(select(func.count()).select_from(model))
                for model in (User, AuthSession, AuthRefreshToken)
            ]
        )


@pytest.mark.parametrize("social_type", ["GOOGLE", "KAKAO"])
async def test_challenge_commits_only_hashed_state(client, challenge_db, social_type):
    factory, owned_hashes = challenge_db
    before = await member_counts(factory)
    response = await client.post(PATH, json=BODY | {"socialType": social_type})
    assert response.status_code == 200
    data = response.json()["data"]
    token_hash = hashlib.sha256(data["challengeToken"].encode()).hexdigest()
    async with factory() as observer:
        state = await repository.find_auth_state(observer, token_hash)
        assert state is not None and owned_hashes == [token_hash]
        assert state.purpose == "ANDROID_LOGIN"
        assert state.social_type == social_type and state.platform_type == "ANDROID"
        assert state.nonce_hash == hashlib.sha256(data["nonce"].encode()).hexdigest()
        assert state.expires_at - state.created_at == timedelta(minutes=5)
        assert state.consumed_at is None and state.social_user_id is None
        assert data["challengeToken"] not in repr(state.__dict__)
        assert data["nonce"] not in repr(state.__dict__)
    assert await member_counts(factory) == before


@pytest.mark.parametrize(
    "failure,status",
    [
        (ConnectionError("synthetic-private"), 503),
        (RuntimeError("synthetic-private"), 500),
    ],
)
async def test_failure_after_flush_rolls_back_state(
    client, challenge_db, monkeypatch, failure, status
):
    factory, owned_hashes = challenge_db
    original = repository.insert_auth_state

    async def fail_after_flush(db, state):
        await original(db, state)
        raise failure

    monkeypatch.setattr(repository, "insert_auth_state", fail_after_flush)
    response = await client.post(PATH, json=BODY)
    assert response.status_code == status and "data" not in response.json()
    assert len(owned_hashes) == 1
    async with factory() as observer:
        assert await repository.find_auth_state(observer, owned_hashes[0]) is None


async def test_lost_commit_ack_does_not_return_credentials_or_retry(
    client, challenge_db, monkeypatch
):
    factory, owned_hashes = challenge_db
    original_exit = AsyncSessionTransaction.__aexit__

    async def lose_ack(transaction, *args):
        await original_exit(transaction, *args)
        raise ConnectionError("synthetic-commit-ack-lost")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSessionTransaction, "__aexit__", lose_ack)
        response = await client.post(PATH, json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "data" not in response.json()
    assert "retryable" not in response.json()["error"]
    assert len(owned_hashes) == 1
    async with factory() as observer:
        state = await repository.find_auth_state(observer, owned_hashes[0])
        assert state is not None and state.consumed_at is None
