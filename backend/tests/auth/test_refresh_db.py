import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.auth import repository, sessions, tokens
from app.auth import router as auth_router
from app.auth.models import AuthRefreshToken, AuthSession
from app.auth.service import create_login_session, hash_refresh_token
from app.core.db import get_session

pytestmark = pytest.mark.db
NOW = datetime(2031, 1, 1, tzinfo=UTC)
ORIGIN = "http://localhost:5173"


@pytest.fixture
async def refresh_db(social_database, monkeypatch):
    monkeypatch.setattr(sessions, "_utc_now", lambda: NOW + timedelta(days=1))
    return social_database


@pytest.fixture
async def db_api(refresh_db, app, monkeypatch):
    factory, _, _ = refresh_db

    async def db_session():
        async with factory() as db:
            yield db

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return sessions._utc_now()

    monkeypatch.setattr(auth_router, "datetime", Clock)
    monkeypatch.setattr(tokens, "_utc_now", lambda: sessions._utc_now())
    from app.auth import web_sessions

    monkeypatch.setattr(web_sessions, "_utc_now", lambda: sessions._utc_now())
    app.dependency_overrides[get_session] = db_session
    app.state.sessionmaker = factory
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        yield client


async def android_request(client, endpoint, token):
    return await client.post(
        "/api/v1/auth/" + endpoint,
        json={"refreshToken": token},
    )


async def seed(factory, member, settings, platform="ANDROID", *, created=NOW):
    async with factory.begin() as db:
        return await create_login_session(
            db, member.id, platform, settings, now=created
        )


async def rotate(factory, token, settings, platform="ANDROID"):
    async with factory() as db:
        return await sessions.refresh_session(db, token, platform, settings)


async def snapshot(factory, session_id):
    async with factory() as db:
        session = await db.get(AuthSession, session_id)
        tokens = list(
            await db.scalars(
                select(AuthRefreshToken).where(
                    AuthRefreshToken.session_id == session_id
                )
            )
        )
        return session, tokens


@pytest.mark.parametrize("platform", ["ANDROID"])
async def test_rotation_keeps_session_and_fixed_expiry(refresh_db, settings, platform):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings, platform)
    result = await rotate(factory, initial.refresh_token, settings, platform)
    assert result.user.id == members[0].id
    assert result.session.session_id == initial.session_id
    assert result.session.access.token != initial.access.token
    assert result.session.refresh_token != initial.refresh_token
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.created_at == NOW
    assert session.expires_at == NOW + timedelta(days=7 if platform == "WEB" else 30)
    assert session.last_refreshed_at == NOW + timedelta(days=1)
    assert len(tokens) == 2 and sum(t.used_at is None for t in tokens) == 1
    assert {t.token_hash for t in tokens} == {
        hash_refresh_token(initial.refresh_token),
        hash_refresh_token(result.session.refresh_token),
    }
    assert result.session.refresh_expires_at == NOW + timedelta(
        days=7 if platform == "WEB" else 8
    )
    with pytest.raises(sessions.RefreshRejectedError):
        await rotate(factory, initial.refresh_token, settings, platform)
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.revoked_at == NOW + timedelta(days=1)
    assert len(tokens) == 2
    with pytest.raises(sessions.RefreshRejectedError):
        await rotate(factory, result.session.refresh_token, settings, platform)


@pytest.mark.parametrize(
    ("platform", "last_day", "expiry_day"),
    [("ANDROID", 0, 7), ("ANDROID", 26, 30)],
)
@pytest.mark.parametrize("offset", [-1, 0])
async def test_expiry_boundary_after_lock(
    refresh_db, settings, monkeypatch, platform, last_day, expiry_day, offset
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings, platform)
    async with factory.begin() as db:
        session = await db.get(AuthSession, initial.session_id)
        session.last_refreshed_at = NOW + timedelta(days=last_day)
    now = NOW + timedelta(days=expiry_day, seconds=offset)
    monkeypatch.setattr(sessions, "_utc_now", lambda: now)
    if offset == 0:
        with pytest.raises(sessions.RefreshRejectedError):
            await rotate(factory, initial.refresh_token, settings, platform)
        session, tokens = await snapshot(factory, initial.session_id)
        assert tokens[0].used_at is None
        assert session.last_refreshed_at == NOW + timedelta(days=last_day)
    else:
        result = await rotate(factory, initial.refresh_token, settings, platform)
        assert result.session.refresh_expires_at <= NOW + timedelta(
            days=7 if platform == "WEB" else 30
        )


@pytest.mark.parametrize("mode", ["unknown", "platform", "revoked"])
async def test_rejected_refresh_does_not_mutate_session(refresh_db, settings, mode):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    if mode == "revoked":
        async with factory.begin() as db:
            session = await db.get(AuthSession, initial.session_id)
            session.revoked_at = NOW
    with pytest.raises(sessions.RefreshRejectedError):
        await rotate(
            factory,
            "unknown" if mode == "unknown" else initial.refresh_token,
            settings,
            "WEB" if mode == "platform" else "ANDROID",
        )
    session, tokens = await snapshot(factory, initial.session_id)
    assert len(tokens) == 1 and tokens[0].used_at is None
    assert session.last_refreshed_at == NOW
    assert session.revoked_at == (NOW if mode == "revoked" else None)


async def test_failed_new_insert_rolls_back_consumption(
    refresh_db, settings, monkeypatch
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)

    async def fail(*args, **kwargs):
        raise ConnectionError()

    monkeypatch.setattr(repository, "insert_rotated_refresh", fail)
    with pytest.raises(sessions.RefreshUnavailableError) as error:
        await rotate(factory, initial.refresh_token, settings)
    assert error.value.retryable is True
    session, tokens = await snapshot(factory, initial.session_id)
    assert tokens[0].used_at is None and len(tokens) == 1
    assert session.last_refreshed_at == NOW and session.revoked_at is None


async def test_concurrent_refresh_commits_reuse_revocation(
    refresh_db, db_api, settings, monkeypatch
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    original = repository.find_refresh_session_id
    barrier = asyncio.Barrier(2)

    async def find(*args):
        value = await original(*args)
        await barrier.wait()
        return value

    with monkeypatch.context() as patch:
        patch.setattr(repository, "find_refresh_session_id", find)
        async with asyncio.timeout(5):
            results = await asyncio.gather(
                android_request(db_api, "refresh", initial.refresh_token),
                android_request(db_api, "refresh", initial.refresh_token),
            )
    assert sorted(r.status_code for r in results) == [200, 401]
    winner = next(r for r in results if r.status_code == 200)
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.revoked_at is not None and len(tokens) == 2
    new_token = winner.json()["data"]["refreshToken"]
    rejected = await android_request(db_api, "refresh", new_token)
    assert rejected.status_code == 401


async def wait_for_lock(factory):
    async with asyncio.timeout(5), factory() as observer:
        while not await observer.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' AND cardinality(pg_blocking_pids(pid)) > 0)"
            )
        ):
            await observer.rollback()
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("change", ["expire", "revoke"])
async def test_waiting_refresh_rechecks_latest_session_and_time(
    refresh_db, settings, monkeypatch, change
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    task = None
    try:
        async with factory.begin() as holder:
            session = await repository.lock_auth_session(holder, initial.session_id)
            task = asyncio.create_task(rotate(factory, initial.refresh_token, settings))
            await wait_for_lock(factory)
            if change == "expire":
                monkeypatch.setattr(
                    sessions, "_utc_now", lambda: NOW + timedelta(days=7)
                )
            else:
                session.revoked_at = NOW
        with pytest.raises(sessions.RefreshRejectedError):
            async with asyncio.timeout(5):
                await task
        _, tokens = await snapshot(factory, initial.session_id)
        assert len(tokens) == 1 and tokens[0].used_at is None
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def logout(factory, token, platform="ANDROID"):
    async with factory() as db:
        await sessions.logout_session(db, token, platform)


@pytest.mark.parametrize("used", [False, True])
async def test_logout_preserves_history_and_other_sessions(refresh_db, settings, used):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    other = await seed(factory, members[0], settings)
    if used:
        await rotate(factory, initial.refresh_token, settings)
    await logout(factory, initial.refresh_token)
    await logout(factory, initial.refresh_token)
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.revoked_at == NOW + timedelta(days=1)
    assert len(tokens) == (2 if used else 1)
    other_session, other_tokens = await snapshot(factory, other.session_id)
    assert other_session.revoked_at is None
    assert len(other_tokens) == 1 and other_tokens[0].used_at is None


@pytest.mark.parametrize("mode", ["unknown", "expired", "revoked", "platform"])
async def test_logout_terminal_states_and_platform_mismatch(
    refresh_db, settings, monkeypatch, mode
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    if mode == "expired":
        monkeypatch.setattr(sessions, "_utc_now", lambda: NOW + timedelta(days=7))
    if mode == "revoked":
        async with factory.begin() as db:
            session = await db.get(AuthSession, initial.session_id)
            session.revoked_at = NOW
    if mode == "platform":
        with pytest.raises(sessions.RefreshRejectedError):
            await logout(factory, initial.refresh_token, "WEB")
    else:
        await logout(factory, "unknown" if mode == "unknown" else initial.refresh_token)
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.revoked_at == (NOW if mode == "revoked" else None)
    assert len(tokens) == 1 and tokens[0].used_at is None


@pytest.mark.parametrize("first", ["refresh", "logout"])
async def test_refresh_logout_serialize_in_both_orders(
    refresh_db, db_api, settings, monkeypatch, first
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    locked = asyncio.Event()
    release = asyncio.Event()
    original = repository.lock_auth_session

    async def lock(*args):
        value = await original(*args)
        if not locked.is_set():
            locked.set()
            await release.wait()
        return value

    monkeypatch.setattr(repository, "lock_auth_session", lock)
    operations = {
        "refresh": lambda: android_request(db_api, "refresh", initial.refresh_token),
        "logout": lambda: android_request(db_api, "logout", initial.refresh_token),
    }
    tasks = []
    try:
        async with asyncio.timeout(5):
            tasks.append(asyncio.create_task(operations[first]()))
            await locked.wait()
            tasks.append(
                asyncio.create_task(
                    operations["logout" if first == "refresh" else "refresh"]()
                )
            )
            await wait_for_lock(factory)
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert results[0].status_code == (204 if first == "logout" else 200)
        if first == "logout":
            assert results[1].status_code == 401
        else:
            assert results[1].status_code == 204
            new_token = results[0].json()["data"]["refreshToken"]
            assert (
                await android_request(db_api, "refresh", new_token)
            ).status_code == 401
        session, tokens = await snapshot(factory, initial.session_id)
        assert session.revoked_at is not None
        assert len(tokens) == (2 if first == "refresh" else 1)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_logout_failure_rolls_back_revocation(refresh_db, settings, monkeypatch):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    async with factory() as db:
        original = db.flush

        async def fail_after_flush(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionError()

        monkeypatch.setattr(db, "flush", fail_after_flush)
        with pytest.raises(sessions.TemporaryStorageError):
            await sessions.logout_session(db, initial.refresh_token, "ANDROID")
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.revoked_at is None and len(tokens) == 1


@pytest.mark.parametrize("new_member", [False, True])
async def test_web_login_or_signup_session_logout_flow(
    refresh_db, db_api, app, settings, monkeypatch, new_member
):
    from unittest.mock import AsyncMock

    from app.auth import signup, social
    from app.auth.contract import VerifiedSocialSubject
    from app.auth.dependencies import get_social_provider
    from app.auth.providers import SocialProviderClient

    _, members, namespace = refresh_db

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return sessions._utc_now()

    monkeypatch.setattr(social, "datetime", Clock)
    monkeypatch.setattr(signup, "_utc_now", lambda: sessions._utc_now())
    settings.google_redirect_uris = ["https://web.example/callback"]
    provider = AsyncMock(spec=SocialProviderClient)
    provider.verify_web.return_value = VerifiedSocialSubject(
        "GOOGLE", namespace + "-new" if new_member else members[0].social_user_id
    )
    app.dependency_overrides[get_social_provider] = lambda: provider
    response = await db_api.post(
        "/api/v1/auth/social/login",
        headers={"Origin": ORIGIN},
        json={
            "socialType": "GOOGLE",
            "platformType": "WEB",
            "authorizationCode": "synthetic-code",
            "codeVerifier": "a" * 43,
            "redirectUri": "https://web.example/callback",
        },
    )
    assert response.status_code == 200
    if new_member:
        assert response.json()["data"]["status"] == "SIGNUP_REQUIRED"
        response = await db_api.post(
            "/api/v1/auth/signup",
            headers={"Origin": ORIGIN},
            json={
                "signupToken": response.json()["data"]["signupToken"],
                "displayName": "합성회원",
                "birthYear": 2000,
                "gender": "FEMALE",
                "heightCm": 165,
                "weightKg": 55,
            },
        )
        assert response.status_code == 201
    data = response.json()["data"]
    original_cookie = db_api.cookies.get("__Host-cf_session")
    for _ in range(20):
        response = await db_api.get("/api/v1/auth/session")
        assert response.status_code == 200
        assert response.json()["data"]["user"] == data["user"]
        assert "set-cookie" not in response.headers
        assert db_api.cookies.get("__Host-cf_session") == original_cookie
    response = await db_api.get("/api/v1/users/me")
    assert response.status_code == 200 and response.json()["data"] == data["user"]
    response = await db_api.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 204 and response.content == b""
    assert db_api.cookies.get("__Host-cf_session") is None
    for path in ("/api/v1/auth/session", "/api/v1/users/me"):
        response = await db_api.get(
            path, headers={"Cookie": "__Host-cf_session=" + original_cookie}
        )
        assert response.status_code == 401


async def test_android_synthetic_session_api_flow(refresh_db, db_api, settings):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings, "ANDROID")
    response = await db_api.post(
        "/api/v1/auth/refresh", json={"refreshToken": initial.refresh_token}
    )
    assert response.status_code == 200 and "set-cookie" not in response.headers
    data = response.json()["data"]
    assert "status" not in data and data["refreshToken"] != initial.refresh_token
    fetched = await db_api.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + data["accessToken"]}
    )
    assert fetched.status_code == 200 and fetched.json()["data"] == data["user"]
    response = await db_api.post(
        "/api/v1/auth/logout", json={"refreshToken": data["refreshToken"]}
    )
    assert response.status_code == 204 and "set-cookie" not in response.headers
    rejected = await db_api.post(
        "/api/v1/auth/refresh", json={"refreshToken": data["refreshToken"]}
    )
    assert (
        rejected.status_code == 401
        and rejected.json()["error"]["code"] == "REFRESH_REJECTED"
    )


@pytest.mark.parametrize(
    "stage", ["before_commit", "commit_ack_lost", "reuse_commit_ack_lost"]
)
async def test_api_commit_outcome_and_persisted_state(
    refresh_db, db_api, settings, monkeypatch, stage
):
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction

    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    if stage == "reuse_commit_ack_lost":
        await rotate(factory, initial.refresh_token, settings)
    original_exit = AsyncSessionTransaction.__aexit__

    async def lost_ack(transaction, exc_type, exc_value, traceback):
        await original_exit(transaction, exc_type, exc_value, traceback)
        if exc_type is None:
            raise ConnectionError("synthetic-commit-ack-lost")

    async def fail_insert(*args, **kwargs):
        raise ConnectionError("synthetic-before-commit")

    with monkeypatch.context() as patch:
        if stage == "before_commit":
            patch.setattr(repository, "insert_rotated_refresh", fail_insert)
        else:
            patch.setattr(AsyncSessionTransaction, "__aexit__", lost_ack)
        response = await android_request(db_api, "refresh", initial.refresh_token)
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is (stage == "before_commit")
    assert "set-cookie" not in response.headers and "synthetic-" not in response.text
    session, stored = await snapshot(factory, initial.session_id)
    if stage == "before_commit":
        assert len(stored) == 1 and stored[0].used_at is None
        assert session.last_refreshed_at == NOW and session.revoked_at is None
    else:
        assert len(stored) == 2
        assert session.last_refreshed_at == NOW + timedelta(days=1)
        assert (session.revoked_at is not None) is (stage == "reuse_commit_ack_lost")


async def test_unrelated_session_does_not_wait_for_locked_session(
    refresh_db, db_api, settings
):
    factory, members, _ = refresh_db
    initial = await seed(factory, members[0], settings)
    other = await seed(factory, members[0], settings)
    async with factory.begin() as holder:
        await repository.lock_auth_session(holder, initial.session_id)
        async with asyncio.timeout(3):
            response = await android_request(db_api, "refresh", other.refresh_token)
        assert response.status_code == 200
    session, tokens = await snapshot(factory, initial.session_id)
    assert session.revoked_at is None and tokens[0].used_at is None
