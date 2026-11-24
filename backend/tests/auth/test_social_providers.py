import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.auth import providers
from app.auth.providers import (
    ProviderConfigurationError,
    SocialProviderClient,
    SocialProviderError,
)
from app.auth.schemas import WebSocialLoginRequest

NOW = datetime(2026, 10, 23, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_key(key, kid="synthetic-key"):
    return json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
    }


def id_token(key, changes=None, missing=None, kid="synthetic-key"):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "synthetic-client",
        "sub": "synthetic-sub",
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 300,
    }
    claims.update(changes or {})
    if missing:
        del claims[missing]
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def provider_settings(settings):
    settings.google_client_id = "synthetic-client"
    settings.google_client_secret = SecretStr("synthetic-google-secret")
    settings.kakao_rest_api_key = SecretStr("synthetic-kakao-key")
    settings.kakao_client_secret = SecretStr("synthetic-kakao-secret")
    settings.kakao_app_id = 123
    return settings


def payload(provider="GOOGLE"):
    return WebSocialLoginRequest.model_validate(
        {
            "socialType": provider,
            "platformType": "WEB",
            "authorizationCode": "synthetic-code",
            "codeVerifier": "a" * 43,
            "redirectUri": "https://web.example/callback",
        }
    )


async def test_google_exchange_and_key_cache(provider_settings, signing_key):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form["code_verifier"] == ["a" * 43]
            assert form["redirect_uri"] == ["https://web.example/callback"]
            assert form["grant_type"] == ["authorization_code"]
            assert request.url.query == b""
            return httpx.Response(200, json={"id_token": id_token(signing_key)})
        return httpx.Response(
            200,
            json={"keys": [public_key(signing_key)]},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(provider_settings, http)
        result = await client.verify_web(payload())
        assert (result.social_type, result.social_user_id) == (
            "GOOGLE",
            "synthetic-sub",
        )
        await client.verify_web(payload())
    assert [r.method for r in calls] == ["POST", "GET", "POST"]


@pytest.mark.parametrize(
    "changes,missing",
    [
        ({"iss": "https://evil.example"}, None),
        ({"aud": "other-client"}, None),
        ({"exp": int(NOW.timestamp())}, None),
        ({"sub": ""}, None),
        ({"sub": 12}, None),
        ({"azp": "other-client"}, None),
        ({"aud": ["synthetic-client", "other"]}, None),
        ({}, "iss"),
        ({}, "aud"),
        ({}, "exp"),
        ({}, "sub"),
        ({}, "iat"),
    ],
)
async def test_google_rejects_invalid_claims(
    provider_settings, signing_key, changes, missing
):
    def handler(request):
        return httpx.Response(200, json={"keys": [public_key(signing_key)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_google_id_token(
                id_token(signing_key, changes, missing)
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize("kind", ["signature", "algorithm", "malformed", "kid"])
async def test_google_rejects_untrusted_token(provider_settings, signing_key, kind):
    token = id_token(signing_key)
    if kind == "signature":
        token = id_token(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    elif kind == "algorithm":
        token = jwt.encode(
            {"sub": "synthetic"},
            "synthetic-hmac-key-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "synthetic-key"},
        )
    elif kind == "malformed":
        token = "synthetic.bad.token"
    else:
        token = id_token(signing_key, kid="unknown")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_google_id_token(
                token
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


async def test_kakao_trusted_identity_and_form(provider_settings):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert parse_qs(request.content.decode())["code_verifier"] == ["a" * 43]
            return httpx.Response(
                200,
                json={"access_token": "synthetic-kakao-token", "token_type": "bearer"},
            )
        assert request.headers["Authorization"] == "Bearer synthetic-kakao-token"
        assert request.url.query == b""
        return httpx.Response(
            200, json={"id": 1234567890123456789, "app_id": 123, "expires_in": 300}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await SocialProviderClient(provider_settings, http).verify_web(
            payload("KAKAO")
        )
    assert result.social_user_id == "1234567890123456789"
    assert result.social_type == "KAKAO"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "change",
    [{"app_id": 456}, {"expires_in": 0}, {"id": True}, {"id": "123"}, {"id": None}],
)
async def test_kakao_rejects_wrong_identity(provider_settings, change):
    def handler(request):
        body = (
            {"access_token": "synthetic-token", "token_type": "bearer"}
            if request.method == "POST"
            else {"id": 123, "app_id": 123, "expires_in": 300} | change
        )
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SocialProviderError):
            await SocialProviderClient(provider_settings, http).verify_web(
                payload("KAKAO")
            )


@pytest.mark.parametrize(
    "provider,status,body,code",
    [
        ("GOOGLE", 400, {"error": "invalid_grant"}, "SOCIAL_CREDENTIAL_INVALID"),
        (
            "KAKAO",
            400,
            {"error": "invalid_client", "error_code": "KOE114"},
            "SOCIAL_CREDENTIAL_INVALID",
        ),
        (
            "KAKAO",
            400,
            {"error": "invalid_grant", "error_code": "KOE320"},
            "SOCIAL_CREDENTIAL_INVALID",
        ),
        ("KAKAO", 400, {"code": -1}, "SOCIAL_PROVIDER_UNAVAILABLE"),
        ("KAKAO", 400, {"error_code": "KOE237"}, "SOCIAL_PROVIDER_UNAVAILABLE"),
        ("GOOGLE", 429, {}, "SOCIAL_PROVIDER_UNAVAILABLE"),
        ("GOOGLE", 503, {}, "SOCIAL_PROVIDER_UNAVAILABLE"),
    ],
)
async def test_provider_error_mapping(provider_settings, provider, status, body, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json=body | {"error_description": "synthetic-secret"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_web(
                payload(provider)
            )
    assert error.value.code == code
    assert "synthetic-secret" not in str(error.value)
    assert len(calls) == 1


async def test_kakao_http400_internal_error_is_unavailable(provider_settings):
    def handler(request):
        return (
            httpx.Response(
                200, json={"access_token": "synthetic-token", "token_type": "bearer"}
            )
            if request.method == "POST"
            else httpx.Response(400, json={"code": -1})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_web(
                payload("KAKAO")
            )
    assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"


async def test_timeout_not_retried(provider_settings):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_web(payload())
    assert len(calls) == 1
    assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"


async def test_client_misconfiguration_not_credential_error(provider_settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": "invalid_client"})
        )
    ) as http:
        with pytest.raises(ProviderConfigurationError):
            await SocialProviderClient(provider_settings, http).verify_web(payload())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(302, headers={"location": "https://evil.example"}),
    ],
)
async def test_malformed_provider_response(provider_settings, response):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: response)
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_web(payload())
    assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "body",
    [
        {"error": []},
        {"error": {}},
        {"error_code": []},
        {"error_code": {}},
        {"error": None},
        {"code": True},
    ],
)
async def test_malformed_error_fields_are_provider_failure(
    provider_settings, provider, body
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, json=body))
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_web(
                payload(provider)
            )
    assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(400, json={"error": []}),
        httpx.Response(400, json={"error_code": {}}),
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(401, json={"error": "invalid_client"}),
        httpx.Response(200, json={"keys": []}),
        httpx.Response(200, json={"keys": [{"kid": "synthetic-key"}]}),
    ],
)
async def test_jwks_failure_is_not_invalid_credential(
    provider_settings, signing_key, response
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: response)
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(provider_settings, http).verify_google_id_token(
                id_token(signing_key)
            )
    assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"


async def test_key_rotation_refreshes_once_and_cache_expires(
    provider_settings, signing_key, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(providers, "monotonic", lambda: clock[0])
    calls = []

    def handler(request):
        calls.append(request)
        kid = "synthetic-key" if len(calls) == 1 else "rotated"
        return httpx.Response(
            200,
            json={"keys": [public_key(signing_key, kid)]},
            headers={"Cache-Control": "max-age=60"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(provider_settings, http)
        await client.verify_google_id_token(id_token(signing_key))
        clock[0] += 31
        await client.verify_google_id_token(id_token(signing_key, kid="rotated"))
        with pytest.raises(SocialProviderError):
            await client.verify_google_id_token(id_token(signing_key, kid="unknown"))
        assert len(calls) == 2
        clock[0] += 61
        await client.verify_google_id_token(id_token(signing_key, kid="rotated"))
        assert len(calls) == 3


async def test_multiple_audience_requires_matching_presenter(
    provider_settings, signing_key
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        result = await SocialProviderClient(
            provider_settings, http
        ).verify_google_id_token(
            id_token(
                signing_key,
                {"aud": ["synthetic-client", "other"], "azp": "synthetic-client"},
            )
        )
    assert result.social_user_id == "synthetic-sub"
