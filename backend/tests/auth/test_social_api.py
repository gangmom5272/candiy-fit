from datetime import UTC, datetime
from decimal import Decimal
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import router as auth_router
from app.auth import social, tokens
from app.auth.contract import VerifiedSocialSubject
from app.auth.dependencies import get_social_provider
from app.auth.providers import SocialProviderClient, SocialProviderError
from app.core.db import get_session
from app.users.models import User

NOW = datetime(2026, 10, 23, tzinfo=UTC)
PATH = "/api/v1/auth/social/login"


@pytest.fixture
def social_api(app, settings, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(social, "datetime", Clock)
    monkeypatch.setattr(auth_router, "datetime", Clock)
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    settings.google_redirect_uris = ["https://web.example/callback"]
    settings.kakao_redirect_uris = ["https://web.example/callback"]
    provider = AsyncMock(spec=SocialProviderClient)
    provider.verify_web.return_value = VerifiedSocialSubject(
        "GOOGLE", "synthetic-subject"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    async def flush():
        db.add.call_args.args[0].id = uuid4()

    db.flush.side_effect = flush

    async def session():
        yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_social_provider] = lambda: provider
    body = {
        "socialType": "GOOGLE",
        "platformType": "WEB",
        "authorizationCode": "synthetic-code",
        "codeVerifier": "a" * 43,
        "redirectUri": "https://web.example/callback",
    }
    return db, provider, body, {"Origin": "http://localhost:5173"}


async def test_signup_only_response_and_no_cookie(client, social_api):
    db, provider, body, headers = social_api
    response = await client.post(PATH, json=body, headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"status", "signupToken", "signupTokenExpiresAt", "socialType"}
    assert data["status"] == "SIGNUP_REQUIRED"
    assert data["signupTokenExpiresAt"] == "2026-10-23T00:10:00Z"
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    db.add.assert_called_once()
    provider.verify_web.assert_awaited_once()


async def test_login_response_cookie_and_protected_identity(
    client, social_api, settings
):
    db, _, body, headers = social_api
    user = User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id="synthetic-subject",
        display_name="기존회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )
    db.scalar.return_value = user
    db.get.return_value = user
    response = await client.post(PATH, json=body, headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"status", "expiresAt", "user"}
    assert data["status"] == "LOGGED_IN"
    assert data["expiresAt"] == "2026-10-30T00:00:00Z"
    assert len(data["user"]) == 9
    cookie = SimpleCookie(response.headers.get_list("set-cookie")[0])[
        "__Host-cf_session"
    ]
    assert cookie["httponly"] and cookie["secure"]
    assert cookie["samesite"] == "lax" and cookie["path"] == "/"
    assert cookie["domain"] == "" and int(cookie["max-age"]) == 7 * 86400
    assert cookie.value not in response.text
    assert "accessToken" not in data and "refreshToken" not in data
    saved = db.add.call_args_list[0].args[0]
    from app.auth.service import hash_refresh_token

    assert saved.web_token_hash == hash_refresh_token(cookie.value)


@pytest.mark.parametrize("origin", [None, "null", "https://not-allowed.example"])
async def test_origin_before_provider_and_database(client, social_api, origin):
    db, provider, body, _ = social_api
    response = await client.post(
        PATH, json=body, headers={} if origin is None else {"Origin": origin}
    )
    assert response.status_code == 403
    provider.verify_web.assert_not_awaited()
    db.begin.assert_not_called()


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (
            SocialProviderError("SOCIAL_CREDENTIAL_INVALID"),
            400,
            "SOCIAL_CREDENTIAL_INVALID",
        ),
        (
            SocialProviderError("SOCIAL_PROVIDER_UNAVAILABLE"),
            503,
            "SOCIAL_PROVIDER_UNAVAILABLE",
        ),
        (RuntimeError("synthetic-private-provider"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_provider_errors_no_db_or_cookie(
    client, social_api, failure, status, code, caplog
):
    db, provider, body, headers = social_api
    provider.verify_web.side_effect = failure
    response = await client.post(PATH, json=body, headers=headers)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert "set-cookie" not in response.headers
    assert "private" not in response.text + caplog.text
    db.begin.assert_not_called()


@pytest.mark.parametrize(
    "error_code,status,code",
    [
        ("KOE114", 400, "SOCIAL_CREDENTIAL_INVALID"),
        ("KOE010", 500, "INTERNAL_ERROR"),
    ],
)
async def test_kakao_code_binding_and_client_configuration_errors(
    app, client, settings, social_api, error_code, status, code, caplog
):
    db, _, body, headers = social_api
    settings.kakao_rest_api_key = SecretStr("synthetic-key")
    settings.kakao_client_secret = SecretStr("synthetic-secret")
    settings.kakao_app_id = 123
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            400,
            json={
                "error": "invalid_client",
                "error_code": error_code,
                "error_description": "synthetic-private-provider",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SocialProviderClient(settings, http)
        app.dependency_overrides[get_social_provider] = lambda: provider
        response = await client.post(
            PATH, json=body | {"socialType": "KAKAO"}, headers=headers
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert "set-cookie" not in response.headers and "data" not in response.json()
    assert "synthetic-private" not in response.text + caplog.text
    assert len(calls) == 1
    db.begin.assert_not_called()
    db.scalar.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "stage", ["google_exchange", "kakao_exchange", "google_jwks", "kakao_info"]
)
@pytest.mark.parametrize("error_body", [{"error": []}, {"error_code": {}}])
async def test_malformed_upstream_errors_are_safe_503_before_database(
    app, client, settings, social_api, stage, error_body, caplog
):
    db, _, body, headers = social_api
    settings.google_client_id = "synthetic-client"
    settings.google_client_secret = SecretStr("synthetic-secret")
    settings.kakao_rest_api_key = SecretStr("synthetic-key")
    settings.kakao_app_id = 123
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST" and stage == "google_jwks":
            # RS256/kid 헤더만 읽힌 뒤 공개키 조회에서 실패하는 합성 토큰.
            return httpx.Response(
                200,
                json={
                    "id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6InN5bnRoZXRpYyJ9.e30.AA"
                },
            )
        if request.method == "POST" and stage == "kakao_info":
            return httpx.Response(
                200, json={"access_token": "synthetic-token", "token_type": "bearer"}
            )
        return httpx.Response(
            400, json=error_body | {"error_description": "synthetic-private-provider"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SocialProviderClient(settings, http)
        app.dependency_overrides[get_social_provider] = lambda: provider
        response = await client.post(
            PATH,
            json=body
            | {"socialType": "GOOGLE" if stage.startswith("google") else "KAKAO"},
            headers=headers,
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SOCIAL_PROVIDER_UNAVAILABLE"
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers and "data" not in response.json()
    assert "synthetic-private" not in response.text + caplog.text
    assert len(calls) == (1 if stage.endswith("exchange") else 2)
    db.begin.assert_not_called()
    db.scalar.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.parametrize("stage", ["lookup", "commit"])
async def test_storage_failure_no_success_credentials(client, social_api, stage):
    db, _, body, headers = social_api
    if stage == "lookup":
        db.scalar.side_effect = ConnectionError()
    else:
        db.begin.return_value.__aexit__.side_effect = ConnectionError()
    response = await client.post(PATH, json=body, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "set-cookie" not in response.headers and "data" not in response.json()


@pytest.mark.parametrize(
    "content,reason",
    [
        ("null", "null_not_allowed"),
        ('{"socialType":"GOOGLE","socialType":"KAKAO"}', "duplicate"),
        ('{"codeVerifier":NaN}', "invalid_json"),
    ],
)
async def test_existing_json_contract_preserved(client, social_api, content, reason):
    db, provider, _, headers = social_api
    response = await client.post(
        PATH, content=content, headers=headers | {"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["reason"] == reason
    provider.verify_web.assert_not_awaited()
    db.begin.assert_not_called()


async def test_provider_runs_before_transaction(client, social_api):
    db, provider, body, headers = social_api

    async def verify(payload):
        db.begin.assert_not_called()
        db.scalar.assert_not_awaited()
        return VerifiedSocialSubject("GOOGLE", "synthetic-subject")

    provider.verify_web.side_effect = verify
    assert (await client.post(PATH, json=body, headers=headers)).status_code == 200
    db.begin.assert_called_once()


async def test_lifespan_closes_provider_http_client(app):
    async with app.router.lifespan_context(app):
        http = app.state.social_provider.http
        assert not http.is_closed
        assert http.follow_redirects is False
    assert http.is_closed


@pytest.mark.parametrize(
    "change,field,reason",
    [
        ({"socialType": "OTHER"}, "socialType", "invalid_choice"),
        ({"platformType": "OTHER"}, "platformType", "invalid_choice"),
        ({"authorizationCode": None}, "authorizationCode", "null_not_allowed"),
        ({"authorizationCode": 123}, "authorizationCode", "invalid_type"),
        ({"codeVerifier": "a" * 42}, "codeVerifier", "invalid_format"),
        (
            {"redirectUri": "https://web.example/callback/"},
            "redirectUri",
            "invalid_choice",
        ),
        ({"idToken": "synthetic-private-input"}, "idToken", "extra_forbidden"),
        (
            {"challengeToken": "synthetic-private-input"},
            "challengeToken",
            "extra_forbidden",
        ),
        ({"nonce": "synthetic-private-input"}, "nonce", "extra_forbidden"),
    ],
)
async def test_invalid_payload_has_safe_details_before_provider(
    client, social_api, change, field, reason, caplog
):
    db, provider, body, headers = social_api
    response = await client.post(PATH, json=body | change, headers=headers)
    assert response.status_code == 422
    assert {"field": field, "reason": reason} in response.json()["error"]["details"]
    assert "synthetic-private-input" not in response.text + caplog.text
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    provider.verify_web.assert_not_awaited()
    db.begin.assert_not_called()


async def test_duplicate_origin_rejected(client, social_api):
    db, provider, body, headers = social_api
    response = await client.post(
        PATH,
        json=body,
        headers=[("Origin", headers["Origin"]), ("Origin", headers["Origin"])],
    )
    assert response.status_code == 403
    provider.verify_web.assert_not_awaited()
    db.begin.assert_not_called()


async def test_database_bug_is_safe_500_not_temporary_outage(
    client, social_api, caplog
):
    from sqlalchemy.exc import ProgrammingError

    db, _, body, headers = social_api
    db.scalar.side_effect = ProgrammingError(
        "synthetic-private-sql", {}, Exception("synthetic-private-row")
    )
    response = await client.post(PATH, json=body, headers=headers)
    assert (
        response.status_code == 500
        and response.json()["error"]["code"] == "INTERNAL_ERROR"
    )
    assert "synthetic-private" not in response.text + caplog.text
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert "set-cookie" not in response.headers
