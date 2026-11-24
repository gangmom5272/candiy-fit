"""공개 API·실제 JWT 검증·PostgreSQL 통합. 제공자 HTTP만 합성 응답으로 대체한다."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSessionTransaction

from app.auth import android, repository, sessions, signup, social, tokens
from app.auth import router as auth_router
from app.auth.dependencies import get_social_provider
from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.auth.providers import SocialProviderClient
from app.auth.service import hash_refresh_token
from app.core.db import get_session
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 29, tzinfo=UTC)
ROOT = "/api/v1/auth"
PROFILE = {
    "displayName": "합성가입회원",
    "birthYear": 2000,
    "gender": "FEMALE",
    "heightCm": 165.25,
    "weightKg": 55,
}
JWKS = {
    "GOOGLE": "https://www.googleapis.com/oauth2/v3/certs",
    "KAKAO": "https://kauth.kakao.com/.well-known/jwks.json",
}


@pytest.fixture(scope="module")
def flow_keys():
    return {
        name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for name in JWKS
    }


def id_token(flow, provider, nonce, *, existing=True, changes=None, signing_key=None):
    claims = {
        "iss": "https://accounts.google.com"
        if provider == "GOOGLE"
        else "https://kauth.kakao.com",
        "aud": "synthetic-server-client"
        if provider == "GOOGLE"
        else "synthetic-native-key",
        "sub": flow.existing_subject if existing else flow.new_subject,
        "iat": int(flow.now.timestamp()),
        "exp": int(flow.now.timestamp()) + 300,
        "nonce": nonce,
    }
    if provider == "GOOGLE":
        claims["azp"] = "synthetic-android-client"
    claims.update(changes or {})
    return jwt.encode(
        claims,
        flow.keys[provider] if signing_key is None else signing_key,
        algorithm="RS256",
        headers={"kid": "synthetic-key"},
    )


@pytest.fixture
async def android_flow(app, settings, social_database, flow_keys, monkeypatch):
    factory, members, namespace = social_database
    # Kakao의 실제 검증 정책에 맞는 합성 양의 정수 sub. 테스트별로 격리한다.
    subject = str(int(namespace.removeprefix("synthetic-"), 16))
    flow = SimpleNamespace(
        factory=factory,
        keys=flow_keys,
        existing_subject=subject,
        new_subject=subject + "1",
        now=NOW,
        calls=[],
        jwks_status=200,
    )
    owned_hashes = []
    insert_original = repository.insert_auth_state

    async def insert(db, state):
        owned_hashes.append(state.token_hash)
        await insert_original(db, state)

    async def session():
        async with factory() as db:
            yield db

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return flow.now

    monkeypatch.setattr(repository, "insert_auth_state", insert)
    monkeypatch.setattr(auth_router, "datetime", Clock)
    monkeypatch.setattr(social, "datetime", Clock)
    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)
    for module in (android, signup, sessions, tokens):
        monkeypatch.setattr(module, "_utc_now", lambda: flow.now)
    settings.google_client_id = "synthetic-server-client"
    settings.google_android_client_ids = ["synthetic-android-client"]
    settings.google_client_secret = SecretStr("synthetic-google-secret")
    settings.kakao_native_app_key = SecretStr("synthetic-native-key")
    settings.kakao_rest_api_key = SecretStr("synthetic-rest-key")
    settings.kakao_app_id = 123
    settings.google_redirect_uris = settings.kakao_redirect_uris = [
        "https://web.example/callback"
    ]
    app.dependency_overrides[get_session] = session
    async with factory.begin() as db:
        for member in members:
            saved = await db.get(User, member.id)
            saved.social_user_id = subject

    def handler(request):
        url = str(request.url)
        flow.calls.append((request.method, url))
        if url in JWKS.values():
            assert request.method == "GET" and not request.content
            assert "authorization" not in request.headers
            name = next(name for name, endpoint in JWKS.items() if endpoint == url)
            key = json.loads(
                jwt.algorithms.RSAAlgorithm.to_jwk(flow.keys[name].public_key())
            )
            key.update(kid="synthetic-key", alg="RS256", use="sig")
            return httpx.Response(
                flow.jwks_status,
                json={"keys": [key]},
                headers={"Cache-Control": "max-age=300"},
            )
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(
                200,
                json={
                    "id_token": id_token(
                        flow,
                        "GOOGLE",
                        "synthetic-web-nonce",
                        changes={"azp": settings.google_client_id},
                    )
                },
            )
        if url == "https://kauth.kakao.com/oauth/token":
            return httpx.Response(
                200,
                json={"access_token": "synthetic-kakao-access", "token_type": "bearer"},
            )
        assert url == "https://kapi.kakao.com/v1/user/access_token_info"
        return httpx.Response(
            200, json={"id": int(subject), "app_id": 123, "expires_in": 300}
        )

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            flow.provider = SocialProviderClient(settings, http)
            app.dependency_overrides[get_social_provider] = lambda: flow.provider
            yield flow
    finally:
        async with factory.begin() as db:
            await db.execute(
                delete(AuthState).where(AuthState.token_hash.in_(owned_hashes))
            )
            new_users = select(User.id).where(User.social_user_id == flow.new_subject)
            await db.execute(
                delete(AuthSession).where(AuthSession.user_id.in_(new_users))
            )
            await db.execute(
                delete(User).where(User.social_user_id == flow.new_subject)
            )


async def challenge(client, provider):
    response = await client.post(
        ROOT + "/social/challenge",
        json={"socialType": provider, "platformType": "ANDROID"},
    )
    assert response.status_code == 200 and "set-cookie" not in response.headers
    return response.json()["data"]


def login_body(flow, provider, issued, *, existing=True, changes=None):
    return {
        "socialType": provider,
        "platformType": "ANDROID",
        "challengeToken": issued["challengeToken"],
        "idToken": id_token(
            flow, provider, issued["nonce"], existing=existing, changes=changes
        ),
    }


async def state_for(flow, issued):
    async with flow.factory() as db:
        return await repository.find_auth_state(
            db, hashlib.sha256(issued["challengeToken"].encode()).hexdigest()
        )


async def counts(flow):
    async with flow.factory() as db:
        users = select(User.id).where(
            User.social_user_id.in_([flow.existing_subject, flow.new_subject])
        )
        auth_sessions = select(AuthSession.id).where(AuthSession.user_id.in_(users))
        return tuple(
            [
                await db.scalar(
                    select(func.count()).select_from(model).where(condition)
                )
                for model, condition in [
                    (User, User.id.in_(users)),
                    (AuthSession, AuthSession.id.in_(auth_sessions)),
                    (AuthRefreshToken, AuthRefreshToken.session_id.in_(auth_sessions)),
                ]
            ]
        )


def assert_failure(response, status, code):
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "data" not in response.json() and "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize("existing", [True, False])
async def test_challenge_login_signup_me_refresh_logout(
    client, android_flow, provider, existing, caplog
):
    flow = android_flow
    issued = await challenge(client, provider)
    state = await state_for(flow, issued)
    assert state.nonce_hash == hashlib.sha256(issued["nonce"].encode()).hexdigest()
    assert issued["nonce"] not in repr(state.__dict__)
    assert issued["challengeToken"] not in repr(state.__dict__)
    body = login_body(flow, provider, issued, existing=existing)
    response = await client.post(ROOT + "/social/login", json=body)
    assert response.status_code == 200 and "set-cookie" not in response.headers
    assert (await state_for(flow, issued)).consumed_at == NOW
    data = response.json()["data"]
    if not existing:
        assert data["status"] == "SIGNUP_REQUIRED"
        assert "accessToken" not in data and "refreshToken" not in data
        assert await counts(flow) == (2, 0, 0)
        signup_token = data["signupToken"]
        forbidden = await client.get(
            "/api/v1/users/me", headers={"Authorization": "Bearer " + signup_token}
        )
        assert_failure(forbidden, 401, "AUTH_REQUIRED")
        invalid_profile = await client.post(
            ROOT + "/signup",
            json=PROFILE | {"signupToken": signup_token, "heightCm": -1},
        )
        assert_failure(invalid_profile, 422, "INVALID_REQUEST")
        response = await client.post(
            ROOT + "/signup", json=PROFILE | {"signupToken": signup_token}
        )
        assert response.status_code == 201 and "set-cookie" not in response.headers
        data = response.json()["data"]
        replay = await client.post(
            ROOT + "/signup", json=PROFILE | {"signupToken": signup_token}
        )
        assert_failure(replay, 401, "SIGNUP_TOKEN_INVALID")
    assert data["status"] == "LOGGED_IN" and data["refreshToken"]
    assert data["user"]["socialType"] == provider
    replay = await client.post(ROOT + "/social/login", json=body)
    assert_failure(replay, 400, "SOCIAL_CREDENTIAL_INVALID")
    assert await counts(flow) == ((2 if existing else 3), 1, 1)
    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + data["accessToken"]}
    )
    assert me.status_code == 200 and me.json()["data"] == data["user"]
    flow.now += timedelta(minutes=1)
    refreshed = await client.post(
        ROOT + "/refresh", json={"refreshToken": data["refreshToken"]}
    )
    assert refreshed.status_code == 200 and "set-cookie" not in refreshed.headers
    current = refreshed.json()["data"]
    assert (
        current["refreshToken"] != data["refreshToken"]
        and current["user"] == data["user"]
    )
    async with flow.factory() as db:
        old = await repository.find_refresh_token(
            db, hash_refresh_token(data["refreshToken"])
        )
        assert old.used_at == flow.now
    logout = await client.post(
        ROOT + "/logout", json={"refreshToken": current["refreshToken"]}
    )
    assert (
        logout.status_code == 204
        and logout.content == b""
        and "set-cookie" not in logout.headers
    )
    rejected = await client.post(
        ROOT + "/refresh", json={"refreshToken": current["refreshToken"]}
    )
    assert_failure(rejected, 401, "REFRESH_REJECTED")
    still_valid = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer " + current["accessToken"]},
    )
    assert still_valid.status_code == 200
    flow.now += timedelta(minutes=15)
    expired = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer " + current["accessToken"]},
    )
    assert_failure(expired, 401, "AUTH_EXPIRED")
    assert flow.calls == [("GET", JWKS[provider])]
    assert body["idToken"] not in caplog.text and issued["nonce"] not in caplog.text


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_web_and_android_reuse_same_member(client, android_flow, provider):
    flow = android_flow
    web = await client.post(
        ROOT + "/social/login",
        headers={"Origin": "http://localhost:5173"},
        json={
            "socialType": provider,
            "platformType": "WEB",
            "authorizationCode": "synthetic-code",
            "codeVerifier": "a" * 43,
            "redirectUri": "https://web.example/callback",
        },
    )
    assert web.status_code == 200 and "set-cookie" in web.headers
    assert "refreshToken" not in web.json()["data"]
    issued = await challenge(client, provider)
    response = await client.post(
        ROOT + "/social/login", json=login_body(flow, provider, issued)
    )
    assert response.status_code == 200 and "set-cookie" not in response.headers
    assert response.json()["data"]["user"] == web.json()["data"]["user"]
    assert response.json()["data"]["refreshToken"]
    assert await counts(flow) == (2, 2, 1)


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "failure", ["nonce", "expired_token", "issuer", "audience", "signature"]
)
async def test_invalid_id_token_preserves_challenge_for_valid_attempt(
    client, android_flow, provider, failure
):
    flow = android_flow
    issued = await challenge(client, provider)
    changes = {
        "nonce": {"nonce": "synthetic-wrong-nonce"},
        "expired_token": {"exp": int(NOW.timestamp())},
        "issuer": {"iss": "https://wrong.example"},
        "audience": {"aud": "synthetic-other-app"},
        "signature": {},
    }[failure]
    body = login_body(flow, provider, issued, changes=changes)
    if failure == "signature":
        original_token = body["idToken"]
        body["idToken"] = id_token(
            flow,
            provider,
            issued["nonce"],
            signing_key=flow.keys["KAKAO" if provider == "GOOGLE" else "GOOGLE"],
        )
        # issuer·audience 등 다른 검사로 거절되지 않도록 헤더·payload는 동일하게 둔다.
        assert body["idToken"].split(".")[:2] == original_token.split(".")[:2]
    response = await client.post(ROOT + "/social/login", json=body)
    assert_failure(response, 400, "SOCIAL_CREDENTIAL_INVALID")
    assert (await state_for(flow, issued)).consumed_at is None
    assert await counts(flow) == (2, 0, 0)
    valid = await client.post(
        ROOT + "/social/login", json=login_body(flow, provider, issued)
    )
    assert valid.status_code == 200


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "failure", ["provider", "expired_challenge", "different_challenge"]
)
async def test_challenge_binding_and_expiry(client, android_flow, provider, failure):
    flow = android_flow
    issued = await challenge(client, provider)
    body = login_body(flow, provider, issued)
    if failure == "provider":
        body["socialType"] = "KAKAO" if provider == "GOOGLE" else "GOOGLE"
    elif failure == "expired_challenge":
        flow.now += timedelta(minutes=5)
        body = login_body(flow, provider, issued)
    else:
        other = await challenge(client, provider)
        body["challengeToken"] = other["challengeToken"]
    response = await client.post(ROOT + "/social/login", json=body)
    assert_failure(response, 400, "SOCIAL_CREDENTIAL_INVALID")
    assert (await state_for(flow, issued)).consumed_at is None
    assert await counts(flow) == (2, 0, 0)
    if failure != "different_challenge":
        assert flow.calls == []
    else:
        assert (await state_for(flow, other)).consumed_at is None


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_jwks_outage_is_503_without_consumption(client, android_flow, provider):
    flow = android_flow
    flow.jwks_status = 503
    issued = await challenge(client, provider)
    body = login_body(flow, provider, issued)
    response = await client.post(ROOT + "/social/login", json=body)
    assert_failure(response, 503, "SOCIAL_PROVIDER_UNAVAILABLE")
    assert (await state_for(flow, issued)).consumed_at is None
    assert await counts(flow) == (2, 0, 0)


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_concurrent_api_requests_consume_once(
    client, android_flow, monkeypatch, existing, provider
):
    flow = android_flow
    issued = await challenge(client, provider)
    body = login_body(flow, provider, issued, existing=existing)
    original = flow.provider.verify_android
    barrier = asyncio.Barrier(2)

    async def verify(*args, **kwargs):
        result = await original(*args, **kwargs)
        await barrier.wait()
        return result

    monkeypatch.setattr(flow.provider, "verify_android", verify)
    async with asyncio.timeout(5):
        responses = await asyncio.gather(
            *[client.post(ROOT + "/social/login", json=body) for _ in range(2)]
        )
    assert sorted(r.status_code for r in responses) == [200, 400]
    assert_failure(
        next(r for r in responses if r.status_code == 400),
        400,
        "SOCIAL_CREDENTIAL_INVALID",
    )
    assert (await state_for(flow, issued)).consumed_at == NOW
    assert await counts(flow) == ((2, 1, 1) if existing else (2, 0, 0))
    async with flow.factory() as db:
        signup_count = await db.scalar(
            select(func.count())
            .select_from(AuthState)
            .where(AuthState.social_user_id == flow.new_subject)
        )
        assert signup_count == (0 if existing else 1)


@pytest.mark.parametrize("existing", [True, False])
async def test_api_storage_failure_rolls_back_bundle(
    client, android_flow, monkeypatch, existing
):
    flow = android_flow
    issued = await challenge(client, "GOOGLE")
    body = login_body(flow, "GOOGLE", issued, existing=existing)
    original = android.decide_social_login

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("synthetic-private-storage")

    monkeypatch.setattr(android, "decide_social_login", fail)
    response = await client.post(ROOT + "/social/login", json=body)
    assert_failure(response, 503, "AUTH_UNAVAILABLE")
    assert (await state_for(flow, issued)).consumed_at is None
    assert await counts(flow) == (2, 0, 0)
    async with flow.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuthState)
                .where(AuthState.social_user_id == flow.new_subject)
            )
            == 0
        )


@pytest.mark.parametrize("existing", [True, False])
async def test_api_lost_commit_ack_never_returns_credentials(
    client, android_flow, monkeypatch, existing
):
    flow = android_flow
    issued = await challenge(client, "GOOGLE")
    body = login_body(flow, "GOOGLE", issued, existing=existing)
    original = AsyncSessionTransaction.__aexit__
    commits = 0

    async def lose_ack(transaction, *args):
        nonlocal commits
        commits += 1
        await original(transaction, *args)
        if commits == 2:
            raise ConnectionError("synthetic-private-commit-ack")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSessionTransaction, "__aexit__", lose_ack)
        response = await client.post(ROOT + "/social/login", json=body)
    assert_failure(response, 503, "AUTH_UNAVAILABLE")
    assert commits == 2 and "retryable" not in response.json()["error"]
    assert (await state_for(flow, issued)).consumed_at == NOW
    replay = await client.post(ROOT + "/social/login", json=body)
    assert_failure(replay, 400, "SOCIAL_CREDENTIAL_INVALID")
    assert await counts(flow) == ((2, 1, 1) if existing else (2, 0, 0))
    async with flow.factory() as db:
        saved_signup_count = await db.scalar(
            select(func.count())
            .select_from(AuthState)
            .where(AuthState.social_user_id == flow.new_subject)
        )
        assert saved_signup_count == (0 if existing else 1)
