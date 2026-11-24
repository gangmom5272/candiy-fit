import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from sqlalchemy import func, select, update

from app.auth import repository, social
from app.auth.contract import VerifiedSocialSubject
from app.auth.dependencies import get_social_provider
from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.auth.providers import SocialProviderClient
from app.auth.service import hash_refresh_token
from app.core.db import get_session
from app.core.errors import TemporaryStorageError
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 23, tzinfo=UTC)


@pytest.fixture
def social_db_api(app, settings, social_database):
    factory, members, namespace = social_database
    settings.google_redirect_uris = ["https://web.example/callback"]
    settings.kakao_redirect_uris = ["https://web.example/callback"]

    async def session():
        async with factory() as db:
            yield db

    provider = AsyncMock(spec=SocialProviderClient)
    provider.verify_web.side_effect = lambda body: VerifiedSocialSubject(
        body.social_type, namespace
    )
    app.dependency_overrides[get_session] = session
    app.state.sessionmaker = factory
    app.dependency_overrides[get_social_provider] = lambda: provider
    return factory, members, namespace, provider


@pytest.mark.parametrize("provider_name", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize("existing", [True, False])
async def test_api_committed_result(client, social_db_api, provider_name, existing):
    factory, members, namespace, provider = social_db_api
    subject = namespace if existing else namespace + "-new"
    provider.verify_web.side_effect = lambda body: VerifiedSocialSubject(
        body.social_type, subject
    )
    response = await client.post(
        "/api/v1/auth/social/login",
        json={
            "socialType": provider_name,
            "platformType": "WEB",
            "authorizationCode": "synthetic-code",
            "codeVerifier": "a" * 43,
            "redirectUri": "https://web.example/callback",
        },
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "refreshToken" not in data
    async with factory() as observer:
        if existing:
            user = next(u for u in members if u.social_type == provider_name)
            assert data["status"] == "LOGGED_IN" and data["user"]["id"] == str(user.id)
            assert "set-cookie" in response.headers
            assert (
                await observer.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(AuthSession.user_id == user.id)
                )
                == 1
            )
        else:
            assert (
                data["status"] == "SIGNUP_REQUIRED"
                and "set-cookie" not in response.headers
            )
            saved = await observer.scalar(
                select(AuthState).where(
                    AuthState.token_hash
                    == social.hash_signup_token(data["signupToken"])
                )
            )
            assert (
                saved.social_type == provider_name and saved.social_user_id == subject
            )
            assert (
                await repository.find_social_user(observer, provider_name, subject)
                is None
            )


@pytest.mark.parametrize("index", [0, 1])
async def test_existing_identity_reuses_session_and_profile(
    social_database, settings, index
):
    factory, members, namespace = social_database
    user = members[index]
    async with factory() as db:
        result = await social.complete_social_login(
            db,
            VerifiedSocialSubject(user.social_type, namespace),
            "WEB",
            settings,
            now=NOW,
        )
    async with factory() as observer:
        session = await observer.get(AuthSession, result.session.session_id)
        refresh = await observer.scalar(
            select(AuthRefreshToken).where(AuthRefreshToken.session_id == session.id)
        )
        saved = await observer.get(User, user.id)
        assert session.user_id == user.id and session.expires_at == NOW + timedelta(
            days=7
        )
        assert refresh is None
        assert session.web_token_hash == hash_refresh_token(result.session.token)
        assert (
            saved.display_name == user.display_name
            and saved.updated_at == user.updated_at
        )
        assert result.user.id == user.id
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(AuthState)
                .where(AuthState.social_user_id == namespace)
            )
            == 0
        )


async def test_new_subject_only_creates_distinct_signup_states(
    social_database, settings
):
    factory, members, namespace = social_database
    subject = VerifiedSocialSubject("GOOGLE", namespace + "-new")
    results = []
    for _ in range(2):
        async with factory() as db:
            results.append(
                await social.complete_social_login(
                    db, subject, "WEB", settings, now=NOW
                )
            )
    assert results[0].signup_token != results[1].signup_token
    async with factory() as db:
        states = (
            await db.scalars(
                select(AuthState).where(
                    AuthState.social_user_id == subject.social_user_id
                )
            )
        ).all()
        assert len(states) == 2
        assert {s.token_hash for s in states} == {
            social.hash_signup_token(r.signup_token) for r in results
        }
        assert all(
            s.expires_at == NOW + timedelta(minutes=10)
            and s.consumed_at is None
            and s.nonce_hash is None
            for s in states
        )
        assert (
            await repository.find_social_user(db, "GOOGLE", subject.social_user_id)
            is None
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.user_id.in_([u.id for u in members]))
            )
            == 0
        )


@pytest.mark.parametrize("existing", [True, False])
async def test_failure_after_write_rolls_back(
    social_database, settings, monkeypatch, existing
):
    factory, members, namespace = social_database
    subject = VerifiedSocialSubject(
        "GOOGLE", namespace if existing else namespace + "-new"
    )
    original = social.decide_social_login

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("synthetic-storage-error")

    monkeypatch.setattr(social, "decide_social_login", fail)
    async with factory() as db:
        with pytest.raises(TemporaryStorageError):
            await social.complete_social_login(db, subject, "WEB", settings, now=NOW)
    async with factory() as observer:
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.user_id == members[0].id)
            )
            == 0
        )

        assert (
            await observer.scalar(
                select(func.count())
                .select_from(AuthState)
                .where(AuthState.social_user_id == subject.social_user_id)
            )
            == 0
        )


@pytest.mark.parametrize("provider_name", ["GOOGLE", "KAKAO"])
async def test_provider_http_login_patch_and_get_roundtrip(
    client, app, settings, social_db_api, provider_name
):
    factory, members, _, _ = social_db_api
    user = next(u for u in members if u.social_type == provider_name)
    subject = str(user.id.int) if provider_name == "KAKAO" else user.social_user_id
    async with factory.begin() as db:
        await db.execute(
            update(User).where(User.id == user.id).values(social_user_id=subject)
        )
    settings.google_client_id = "synthetic-client"
    settings.google_client_secret = SecretStr("synthetic-google-secret")
    settings.kakao_rest_api_key = SecretStr("synthetic-kakao-key")
    settings.kakao_app_id = 123
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {
        "kid": "synthetic",
        "alg": "RS256",
        "use": "sig",
    }
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": "synthetic-client",
            "sub": subject,
            "iat": now,
            "exp": now + 300,
        },
        key,
        algorithm="RS256",
        headers={"kid": "synthetic"},
    )
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert parse_qs(request.content.decode())["code_verifier"] == ["a" * 43]
            body = (
                {"id_token": token}
                if provider_name == "GOOGLE"
                else {
                    "access_token": "synthetic-provider-access",
                    "token_type": "bearer",
                }
            )
            return httpx.Response(200, json=body)
        if provider_name == "GOOGLE":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(
            200, json={"id": int(subject), "app_id": 123, "expires_in": 300}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SocialProviderClient(settings, http)
        app.dependency_overrides[get_social_provider] = lambda: provider
        response = await client.post(
            "/api/v1/auth/social/login",
            json={
                "socialType": provider_name,
                "platformType": "WEB",
                "authorizationCode": "synthetic-code",
                "codeVerifier": "a" * 43,
                "redirectUri": "https://web.example/callback",
            },
            headers={"Origin": "http://localhost:5173"},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user"]["id"] == str(user.id) and "refreshToken" not in data
    assert len(calls) == 2
    headers = {
        "Cookie": "__Host-cf_session=" + response.cookies.get("__Host-cf_session"),
        "Origin": "http://localhost:5173",
    }
    fetched = await client.get("/api/v1/users/me", headers=headers)
    assert fetched.json()["data"] == data["user"]
    patch = await client.patch(
        "/api/v1/users/me",
        content='{"heightCm":166.25'
        + "0" * 20000
        + ',"weightKg":56.75'
        + "0" * 20000
        + "}",
        headers=headers | {"Content-Type": "application/json"},
    )
    assert patch.status_code == 200
    fetched = await client.get("/api/v1/users/me", headers=headers)
    assert fetched.json() == patch.json()
    async with factory() as observer:
        saved = await observer.get(User, user.id)
        assert str(saved.height_cm) == "166.25" and str(saved.weight_kg) == "56.75"
        assert (
            saved.social_user_id == subject and saved.display_name == user.display_name
        )


async def test_signup_token_cannot_authenticate_protected_api(client, social_db_api):
    factory, _, namespace, provider = social_db_api
    provider.verify_web.side_effect = lambda body: VerifiedSocialSubject(
        "GOOGLE", namespace + "-new"
    )
    response = await client.post(
        "/api/v1/auth/social/login",
        json={
            "socialType": "GOOGLE",
            "platformType": "WEB",
            "authorizationCode": "synthetic-code",
            "codeVerifier": "a" * 43,
            "redirectUri": "https://web.example/callback",
        },
        headers={"Origin": "http://localhost:5173"},
    )
    token = response.json()["data"]["signupToken"]
    for method, payload in [("GET", None), ("PATCH", {"displayName": "변경시도"})]:
        rejected = await client.request(
            method,
            "/api/v1/users/me",
            json=payload,
            headers={"Authorization": "Bearer " + token},
        )
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "AUTH_REQUIRED"
    async with factory() as db:
        state = await db.scalar(
            select(AuthState).where(
                AuthState.token_hash == social.hash_signup_token(token)
            )
        )
        assert state.consumed_at is None
