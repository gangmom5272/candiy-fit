import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.auth import repository, signup, social, tokens
from app.auth import router as auth_router
from app.auth.contract import VerifiedSocialSubject
from app.auth.dependencies import get_social_provider
from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.auth.providers import SocialProviderClient
from app.auth.schemas import SignupRequest
from app.auth.service import hash_refresh_token
from app.auth.social import hash_signup_token
from app.core.db import get_session
from app.core.errors import TemporaryStorageError
from app.users.models import User
from app.users.validation import ProfileValidationError

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 24, tzinfo=UTC)
PROFILE = {
    "displayName": "  합성회원  ",
    "birthYear": 2000,
    "gender": "FEMALE",
    "heightCm": 165.25,
    "weightKg": 55,
}
ORIGIN = "http://localhost:5173"


@pytest.fixture
def signup_db(social_database, monkeypatch):
    monkeypatch.setattr(signup, "_utc_now", lambda: NOW)
    return social_database


async def seed(
    factory, namespace, *, platform="WEB", provider="GOOGLE", existing=False
):
    token = "synthetic-" + uuid4().hex
    async with factory.begin() as db:
        db.add(
            AuthState(
                purpose="SIGNUP",
                token_hash=hash_signup_token(token),
                social_type=provider,
                social_user_id=namespace if existing else namespace + "-new",
                platform_type=platform,
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=10),
            )
        )
    return token


async def call(factory, settings, token, *, profile=None, origin=ORIGIN):
    payload = (PROFILE if profile is None else profile) | {"signupToken": token}
    async with factory() as db:
        return await signup.complete_signup(
            db,
            SignupRequest.model_validate(payload),
            json.dumps(payload).encode(),
            origin,
            settings,
        )


async def state_for(db, token):
    return await db.scalar(
        select(AuthState).where(AuthState.token_hash == hash_signup_token(token))
    )


async def counts(factory, namespace):
    async with factory() as db:
        user_ids = select(User.id).where(User.social_user_id == namespace + "-new")
        sessions = select(AuthSession.id).where(AuthSession.user_id.in_(user_ids))
        return tuple(
            [
                await db.scalar(
                    select(func.count()).select_from(model).where(condition)
                )
                for model, condition in [
                    (User, User.id.in_(user_ids)),
                    (AuthSession, AuthSession.id.in_(sessions)),
                    (AuthRefreshToken, AuthRefreshToken.session_id.in_(sessions)),
                ]
            ]
        )


@pytest.mark.parametrize("platform", ["WEB", "ANDROID"])
@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_signup_commits_bundle_and_rejects_replay(
    signup_db, settings, platform, provider
):
    factory, _, namespace = signup_db
    token = await seed(factory, namespace, platform=platform, provider=provider)
    result = await call(
        factory, settings, token, origin=ORIGIN if platform == "WEB" else None
    )
    assert result.created and result.platform_type == platform
    assert (
        result.user.display_name == "합성회원" and result.user.social_type == provider
    )
    assert await counts(factory, namespace) == (1, 1, int(platform == "ANDROID"))
    async with factory() as db:
        state = await state_for(db, token)
        session = await db.get(AuthSession, result.session.session_id)
        refresh = await db.scalar(
            select(AuthRefreshToken).where(AuthRefreshToken.session_id == session.id)
        )
        assert state.consumed_at == NOW and state.expires_at == NOW + timedelta(
            minutes=10
        )
        assert session.expires_at == NOW + timedelta(
            days=7 if platform == "WEB" else 30
        )
        if platform == "ANDROID":
            assert refresh.token_hash == hash_refresh_token(
                result.session.refresh_token
            )
        else:
            assert refresh is None
            assert session.web_token_hash == hash_refresh_token(result.session.token)
    with pytest.raises(signup.SignupTokenInvalidError):
        await call(factory, settings, token)
    assert await counts(factory, namespace) == (1, 1, int(platform == "ANDROID"))


async def test_other_valid_token_logs_in_without_overwriting_profile(
    signup_db, settings
):
    factory, members, namespace = signup_db
    token = await seed(factory, namespace, existing=True)
    result = await call(
        factory, settings, token, profile=PROFILE | {"displayName": "덮어쓰지않음"}
    )
    assert not result.created and result.user.id == members[0].id
    assert result.user.display_name == members[0].display_name
    assert result.user.updated_at == members[0].updated_at
    async with factory() as db:
        assert (await state_for(db, token)).consumed_at == NOW


@pytest.mark.parametrize("bad_origin", [False, True])
async def test_validation_failure_preserves_credential_for_retry(
    signup_db, settings, bad_origin
):
    factory, _, namespace = signup_db
    token = await seed(factory, namespace)
    with pytest.raises(
        signup.OriginNotAllowedError if bad_origin else ProfileValidationError
    ):
        await call(
            factory,
            settings,
            token,
            profile=PROFILE | {"heightCm": -1},
            origin=None if bad_origin else ORIGIN,
        )
    async with factory() as db:
        state = await state_for(db, token)
        assert state.consumed_at is None and state.expires_at == NOW + timedelta(
            minutes=10
        )
    assert await counts(factory, namespace) == (0, 0, 0)
    assert (await call(factory, settings, token)).created


@pytest.mark.parametrize(
    "failure,expected",
    [
        (ConnectionError(), TemporaryStorageError),
        (RuntimeError(), RuntimeError),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_failure_after_session_write_rolls_back_everything(
    signup_db, settings, monkeypatch, failure, expected
):
    factory, _, namespace = signup_db
    token = await seed(factory, namespace)
    original = signup.create_login_session

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise failure

    monkeypatch.setattr(signup, "create_login_session", fail)
    with pytest.raises(expected):
        await call(factory, settings, token)
    assert await counts(factory, namespace) == (0, 0, 0)
    async with factory() as db:
        assert (await state_for(db, token)).consumed_at is None


@pytest.mark.parametrize("same_token", [True, False])
async def test_concurrent_signup_has_one_member_and_single_consumption(
    signup_db_api, client, monkeypatch, same_token
):
    factory, _, namespace, _ = signup_db_api
    first = await seed(factory, namespace)
    second = first if same_token else await seed(factory, namespace)
    original = repository.find_auth_state
    barrier = asyncio.Barrier(2)

    async def find(*args, **kwargs):
        state = await original(*args, **kwargs)
        if not kwargs.get("lock"):
            await barrier.wait()
        return state

    monkeypatch.setattr(repository, "find_auth_state", find)
    async with asyncio.timeout(5):
        responses = await asyncio.gather(
            client.post(
                "/api/v1/auth/signup",
                json=PROFILE | {"signupToken": first, "displayName": "첫번째"},
                headers={"Origin": ORIGIN},
            ),
            client.post(
                "/api/v1/auth/signup",
                json=PROFILE | {"signupToken": second, "displayName": "두번째"},
                headers={"Origin": ORIGIN},
            ),
        )
    assert sorted(r.status_code for r in responses) == (
        [201, 401] if same_token else [200, 201]
    )
    successes = [r for r in responses if r.status_code in {200, 201}]
    created = next(r for r in successes if r.status_code == 201)
    assert created.headers["location"] == "/api/v1/users/me"
    assert all(
        r.json()["data"]["user"] == created.json()["data"]["user"] for r in successes
    )
    for response in responses:
        if response.status_code == 200:
            assert "location" not in response.headers
        elif response.status_code == 401:
            assert response.json()["error"]["code"] == "SIGNUP_TOKEN_INVALID"
            assert "set-cookie" not in response.headers
    assert await counts(factory, namespace) == (1, len(successes), 0)
    async with factory() as db:
        assert (await state_for(db, first)).consumed_at == NOW
        assert (await state_for(db, second)).consumed_at == NOW


async def wait_for_lock(factory):
    async with asyncio.timeout(5), factory() as observer:
        while not await observer.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' AND cardinality(pg_blocking_pids(pid)) > 0)"
            )
        ):
            await (
                observer.rollback()
            )  # 다음 조회에서 PostgreSQL 통계 스냅샷도 갱신한다.
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("change", ["consume", "expire"])
async def test_waiting_state_is_refreshed_after_lock(
    signup_db, settings, monkeypatch, change
):
    factory, _, namespace = signup_db
    token = await seed(factory, namespace)
    async with factory() as holder:
        async with holder.begin():
            state = await repository.find_auth_state(
                holder, hash_signup_token(token), lock=True
            )
            task = asyncio.create_task(call(factory, settings, token))
            try:
                await wait_for_lock(factory)
                if change == "consume":
                    state.consumed_at = NOW
                else:
                    monkeypatch.setattr(
                        signup, "_utc_now", lambda: NOW + timedelta(minutes=10)
                    )
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        with pytest.raises(signup.SignupTokenInvalidError):
            async with asyncio.timeout(5):
                await task
    assert await counts(factory, namespace) == (0, 0, 0)


async def test_competing_member_insert_can_succeed_after_rollback(
    signup_db, settings, monkeypatch
):
    factory, _, namespace = signup_db
    first, second = await seed(factory, namespace), await seed(factory, namespace)
    inserted = asyncio.Event()
    release = asyncio.Event()
    original = signup.create_login_session
    calls = 0

    async def fail_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            inserted.set()
            await release.wait()
            raise ConnectionError()
        return await original(*args, **kwargs)

    monkeypatch.setattr(signup, "create_login_session", fail_first)
    tasks = []
    try:
        async with asyncio.timeout(5):
            tasks.append(asyncio.create_task(call(factory, settings, first)))
            await inserted.wait()
            tasks.append(asyncio.create_task(call(factory, settings, second)))
            await wait_for_lock(factory)
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], TemporaryStorageError)
        assert results[1].created
        assert await counts(factory, namespace) == (1, 1, 0)
        async with factory() as db:
            assert (await state_for(db, first)).consumed_at is None
            assert (await state_for(db, second)).consumed_at == NOW
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def signup_db_api(app, signup_db, settings, monkeypatch):
    factory, members, namespace = signup_db

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(social, "datetime", Clock)
    monkeypatch.setattr(auth_router, "datetime", Clock)
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    settings.google_redirect_uris = ["https://web.example/callback"]
    settings.kakao_redirect_uris = ["https://web.example/callback"]

    async def session():
        async with factory() as db:
            yield db

    provider = AsyncMock(spec=SocialProviderClient)
    provider.verify_web.side_effect = lambda body: VerifiedSocialSubject(
        body.social_type, namespace + "-new"
    )
    app.dependency_overrides[get_session] = session
    app.state.sessionmaker = factory
    from app.auth import web_sessions

    monkeypatch.setattr(web_sessions, "_utc_now", lambda: NOW)
    app.dependency_overrides[get_social_provider] = lambda: provider
    return factory, members, namespace, provider


@pytest.mark.parametrize("provider_name", ["GOOGLE", "KAKAO"])
async def test_social_login_signup_authenticated_get_and_relogin(
    client, signup_db_api, provider_name
):
    factory, _, namespace, provider = signup_db_api
    login_body = {
        "socialType": provider_name,
        "platformType": "WEB",
        "authorizationCode": "synthetic-code",
        "codeVerifier": "a" * 43,
        "redirectUri": "https://web.example/callback",
    }
    login = await client.post(
        "/api/v1/auth/social/login", json=login_body, headers={"Origin": ORIGIN}
    )
    assert (
        login.status_code == 200 and login.json()["data"]["status"] == "SIGNUP_REQUIRED"
    )
    token = login.json()["data"]["signupToken"]
    response = await client.post(
        "/api/v1/auth/signup",
        json=PROFILE | {"signupToken": token},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert provider.verify_web.await_count == 1
    fetched = await client.get(
        "/api/v1/users/me",
        headers={
            "Cookie": "__Host-cf_session=" + response.cookies.get("__Host-cf_session")
        },
    )
    assert fetched.status_code == 200 and fetched.json()["data"] == data["user"]
    relogin = await client.post(
        "/api/v1/auth/social/login", json=login_body, headers={"Origin": ORIGIN}
    )
    assert (
        relogin.status_code == 200 and relogin.json()["data"]["status"] == "LOGGED_IN"
    )
    assert relogin.json()["data"]["user"] == data["user"]
    assert provider.verify_web.await_count == 2
    replay = await client.post(
        "/api/v1/auth/signup",
        json=PROFILE | {"signupToken": token},
        headers={"Origin": ORIGIN},
    )
    assert replay.status_code == 401
    assert await counts(factory, namespace) == (1, 2, 0)


async def test_android_signup_uses_stored_platform_and_body_refresh(
    client, signup_db_api
):
    factory, _, namespace, provider = signup_db_api
    token = await seed(factory, namespace, platform="ANDROID")
    response = await client.post(
        "/api/v1/auth/signup", json=PROFILE | {"signupToken": token}
    )
    assert response.status_code == 201
    assert "set-cookie" not in response.headers
    data = response.json()["data"]
    async with factory() as db:
        state = await state_for(db, token)
        user = await repository.find_social_user(
            db, state.social_type, state.social_user_id
        )
        session = await db.scalar(
            select(AuthSession).where(AuthSession.user_id == user.id)
        )
        refresh = await db.scalar(
            select(AuthRefreshToken).where(AuthRefreshToken.session_id == session.id)
        )
        assert (
            session.platform_type == "ANDROID"
            and session.expires_at == NOW + timedelta(days=30)
        )
        assert refresh.token_hash == hash_refresh_token(data["refreshToken"])
        assert state.consumed_at == NOW
    provider.verify_web.assert_not_awaited()


async def test_profile_error_then_precise_raw_json_signup(client, signup_db_api):
    factory, _, namespace, _ = signup_db_api
    token = await seed(factory, namespace)
    body = PROFILE | {"signupToken": token}
    headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
    rejected = await client.post(
        "/api/v1/auth/signup",
        content=json.dumps(body).replace("165.25", "165.250000000000000000001"),
        headers=headers,
    )
    assert rejected.status_code == 422
    async with factory() as db:
        assert (await state_for(db, token)).consumed_at is None
    response = await client.post(
        "/api/v1/auth/signup",
        content=json.dumps(body).replace("165.25", "165.25" + "0" * 20000),
        headers=headers,
    )
    assert response.status_code == 201
    async with factory() as db:
        user = await repository.find_social_user(db, "GOOGLE", namespace + "-new")
        assert str(user.height_cm) == "165.25"
