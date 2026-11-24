import asyncio
import hashlib
import json
from datetime import UTC, datetime

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

NOW = datetime(2026, 10, 29, tzinfo=UTC)
NONCE = "synthetic-server-nonce"
NONCE_HASH = hashlib.sha256(NONCE.encode()).hexdigest()
JWKS = {
    "GOOGLE": "https://www.googleapis.com/oauth2/v3/certs",
    "KAKAO": "https://kauth.kakao.com/.well-known/jwks.json",
}


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


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_key(key, kid="synthetic-key"):
    return json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
    }


def token(key, provider, changes=None, missing=None, kid="synthetic-key"):
    claims = {
        "iss": "https://accounts.google.com"
        if provider == "GOOGLE"
        else "https://kauth.kakao.com",
        "aud": "synthetic-server-client"
        if provider == "GOOGLE"
        else "synthetic-native-key",
        "sub": "synthetic-google-sub"
        if provider == "GOOGLE"
        else "1234567890123456789",
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 300,
        "nonce": NONCE,
    }
    claims.update(changes or {})
    if missing:
        del claims[missing]
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def android_settings(settings):
    settings.google_client_id = "synthetic-server-client"
    settings.google_android_client_ids = ["synthetic-android-client"]
    settings.kakao_native_app_key = SecretStr("synthetic-native-key")
    return settings


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_android_identity_uses_only_jwks_and_does_not_expose_credentials(
    android_settings, signing_key, provider, caplog
):
    calls = []
    credential = token(signing_key, provider)

    def handler(request):
        calls.append(request)
        assert str(request.url) == JWKS[provider]
        assert request.method == "GET" and not request.content
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"keys": [public_key(signing_key)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(android_settings, http)
        result = await client.verify_android(
            provider, credential, nonce_hash=NONCE_HASH
        )
        assert result.social_type == provider
        assert result.social_user_id == (
            "synthetic-google-sub" if provider == "GOOGLE" else "1234567890123456789"
        )
        await client.verify_android(provider, credential, nonce_hash=NONCE_HASH)
    assert len(calls) == 1
    assert credential not in repr(result) + caplog.text
    assert NONCE not in repr(result) + caplog.text


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize("missing", ["iss", "aud", "sub", "exp", "iat", "nonce"])
async def test_android_requires_claims(
    android_settings, signing_key, provider, missing
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                provider,
                token(signing_key, provider, missing=missing),
                nonce_hash=NONCE_HASH,
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.example"},
        {"aud": "other-app"},
        {"aud": [123]},
        {"sub": ""},
        {"sub": 123},
        {"sub": "x" * 256},
        {"sub": "한글"},
        {"exp": int(NOW.timestamp())},
        {"exp": str(int(NOW.timestamp()) + 300)},
        {"exp": True},
        {"iat": int(NOW.timestamp()) + 1},
        {"iat": "1"},
        {"iat": True},
        {"nonce": "different-nonce"},
        {"nonce": ""},
        {"nonce": None},
        {"nonce": []},
        {"nonce": 123},
        {"nonce": "\ud800"},
    ],
)
async def test_android_rejects_invalid_claims(
    android_settings, signing_key, provider, changes
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                provider, token(signing_key, provider, changes), nonce_hash=NONCE_HASH
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "kind", ["signature", "algorithm", "malformed", "kid", "other_provider"]
)
async def test_android_rejects_untrusted_tokens(
    android_settings, signing_key, other_key, provider, kind
):
    credential = token(other_key if kind == "signature" else signing_key, provider)
    if kind == "algorithm":
        credential = jwt.encode(
            {"nonce": NONCE},
            "synthetic-hmac-key-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "synthetic-key"},
        )
    elif kind == "malformed":
        credential = "synthetic.bad.token"
    elif kind == "kid":
        credential = token(signing_key, provider, kid="unknown")
    elif kind == "other_provider":
        credential = token(signing_key, "KAKAO" if provider == "GOOGLE" else "GOOGLE")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                provider, credential, nonce_hash=NONCE_HASH
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize(
    "changes,accepted",
    [
        ({"azp": "synthetic-android-client"}, True),
        ({"azp": "synthetic-server-client"}, True),
        ({"azp": "unknown"}, False),
        ({"azp": None}, False),
        ({"azp": []}, False),
        ({"aud": ["synthetic-server-client", "other"]}, False),
        (
            {
                "aud": ["synthetic-server-client", "other"],
                "azp": "synthetic-android-client",
            },
            True,
        ),
        ({"aud": "synthetic-android-client", "azp": "synthetic-android-client"}, False),
    ],
)
async def test_google_android_audience_and_presenter_policy(
    android_settings, signing_key, changes, accepted
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        client = SocialProviderClient(android_settings, http)
        credential = token(signing_key, "GOOGLE", changes)
        if accepted:
            assert (
                await client.verify_android("GOOGLE", credential, nonce_hash=NONCE_HASH)
            ).social_type == "GOOGLE"
        else:
            with pytest.raises(SocialProviderError) as error:
                await client.verify_android("GOOGLE", credential, nonce_hash=NONCE_HASH)
            assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize("subject", ["0", "-1", "00123", "1.0", "abc"])
async def test_kakao_subject_matches_web_member_number(
    android_settings, signing_key, subject
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                "KAKAO",
                token(signing_key, "KAKAO", {"sub": subject}),
                nonce_hash=NONCE_HASH,
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(401, json={"error": "invalid_client"}),
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(302, headers={"Location": "https://evil.example"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"keys": []}),
        httpx.Response(200, json={"keys": [{"kid": "synthetic-key"}]}),
    ],
)
async def test_android_jwks_failure_is_provider_failure(
    android_settings, signing_key, provider, response
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: response)
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                provider, token(signing_key, provider), nonce_hash=NONCE_HASH
            )
    assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_missing_android_settings_fail_before_network(
    settings, signing_key, provider
):
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as http:
        with pytest.raises(ProviderConfigurationError):
            await SocialProviderClient(settings, http).verify_android(
                provider, token(signing_key, provider), nonce_hash=NONCE_HASH
            )
    assert not calls


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("synthetic-private"),
        TimeoutError("synthetic-private"),
        asyncio.CancelledError(),
    ],
)
async def test_android_timeout_not_retried_and_cancellation_propagates(
    android_settings, signing_key, failure
):
    calls = []

    def handler(request):
        calls.append(request)
        raise failure

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        expected = (
            asyncio.CancelledError
            if isinstance(failure, asyncio.CancelledError)
            else SocialProviderError
        )
        with pytest.raises(expected) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                "GOOGLE", token(signing_key, "GOOGLE"), nonce_hash=NONCE_HASH
            )
    assert len(calls) == 1
    if expected is SocialProviderError:
        assert error.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"
        assert "synthetic-private" not in str(error.value)


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_android_key_rotation_throttle_and_expiry(
    android_settings, signing_key, provider, monkeypatch
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
        client = SocialProviderClient(android_settings, http)
        await client.verify_android(
            provider, token(signing_key, provider), nonce_hash=NONCE_HASH
        )
        clock[0] += 31
        await client.verify_android(
            provider, token(signing_key, provider, kid="rotated"), nonce_hash=NONCE_HASH
        )
        with pytest.raises(SocialProviderError) as error:
            await client.verify_android(
                provider,
                token(signing_key, provider, kid="unknown"),
                nonce_hash=NONCE_HASH,
            )
        assert error.value.code == "SOCIAL_CREDENTIAL_INVALID" and len(calls) == 2
        clock[0] += 61
        await client.verify_android(
            provider, token(signing_key, provider, kid="rotated"), nonce_hash=NONCE_HASH
        )
        assert len(calls) == 3


async def test_provider_caches_are_separate_and_google_web_shares_keys(
    android_settings, signing_key, other_key
):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        key = signing_key if str(request.url) == JWKS["GOOGLE"] else other_key
        return httpx.Response(200, json={"keys": [public_key(key)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(android_settings, http)
        google_token = token(signing_key, "GOOGLE")
        await client.verify_android("GOOGLE", google_token, nonce_hash=NONCE_HASH)
        await client.verify_android(
            "KAKAO", token(other_key, "KAKAO"), nonce_hash=NONCE_HASH
        )
        await client.verify_google_id_token(google_token)
        await client.verify_android(
            "KAKAO", token(other_key, "KAKAO"), nonce_hash=NONCE_HASH
        )
    assert calls == [JWKS["GOOGLE"], JWKS["KAKAO"]]


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_concurrent_key_requests_fetch_once(
    android_settings, signing_key, provider
):
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        calls.append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, json={"keys": [public_key(signing_key)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(android_settings, http)
        credential = token(signing_key, provider)
        first = asyncio.create_task(
            client.verify_android(provider, credential, nonce_hash=NONCE_HASH)
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        second = asyncio.create_task(
            client.verify_android(provider, credential, nonce_hash=NONCE_HASH)
        )
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    assert len(calls) == 1 and all(result.social_type == provider for result in results)


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize("failure", ["http", "malformed", "network"])
async def test_failed_key_refresh_stays_unavailable_and_recovers(
    android_settings, signing_key, monkeypatch, provider, failure
):
    clock = [100.0]
    monkeypatch.setattr(providers, "monotonic", lambda: clock[0])
    calls = []
    failing = False

    def handler(request):
        calls.append(request)
        if failing:
            if failure == "network":
                raise httpx.ConnectError("synthetic failure", request=request)
            return httpx.Response(503 if failure == "http" else 200, json={"keys": []})
        return httpx.Response(
            200,
            json={
                "keys": [
                    public_key(
                        signing_key, "synthetic-key" if len(calls) == 1 else "rotated"
                    )
                ]
            },
            headers={"Cache-Control": "max-age=60"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(android_settings, http)
        old = token(signing_key, provider)
        rotated = token(signing_key, provider, kid="rotated")
        await client.verify_android(provider, old, nonce_hash=NONCE_HASH)
        clock[0] = 131.0
        failing = True
        for _ in range(3):
            with pytest.raises(SocialProviderError) as caught:
                await client.verify_android(provider, rotated, nonce_hash=NONCE_HASH)
            assert caught.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"
        assert len(calls) == 2
        # 새 키 조회가 실패해도 아직 유효한 기존 키는 사용할 수 있다.
        await client.verify_android(provider, old, nonce_hash=NONCE_HASH)
        clock[0] = 160.0
        with pytest.raises(SocialProviderError) as caught:
            await client.verify_android(provider, old, nonce_hash=NONCE_HASH)
        assert caught.value.code == "SOCIAL_PROVIDER_UNAVAILABLE"
        assert len(calls) == 2
        failing = False
        clock[0] = 161.0
        await client.verify_android(provider, rotated, nonce_hash=NONCE_HASH)
        with pytest.raises(SocialProviderError) as caught:
            await client.verify_android(provider, old, nonce_hash=NONCE_HASH)
        assert caught.value.code == "SOCIAL_CREDENTIAL_INVALID"
        assert len(calls) == 3


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
async def test_concurrent_cold_cache_failure_fetches_once(
    android_settings, signing_key, monkeypatch, provider
):
    monkeypatch.setattr(providers, "monotonic", lambda: 100.0)
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(503, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(android_settings, http)
        credential = token(signing_key, provider)
        results = await asyncio.gather(
            *(
                client.verify_android(provider, credential, nonce_hash=NONCE_HASH)
                for _ in range(3)
            ),
            return_exceptions=True,
        )
    assert all(
        isinstance(result, SocialProviderError)
        and result.code == "SOCIAL_PROVIDER_UNAVAILABLE"
        for result in results
    )
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize(
    "cache_control,seconds,fetches",
    [
        ("no-store", 0, 2),
        ("no-cache", 0, 2),
        ("max-age=0", 0, 2),
        ("max-age=99999", 3601, 2),
        ("max-age=60", 59, 1),
        ("", 301, 2),
    ],
)
async def test_jwks_cache_control(
    android_settings,
    signing_key,
    provider,
    cache_control,
    seconds,
    fetches,
    monkeypatch,
):
    clock = [100.0]
    monkeypatch.setattr(providers, "monotonic", lambda: clock[0])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"keys": [public_key(signing_key)]},
            headers={"Cache-Control": cache_control},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SocialProviderClient(android_settings, http)
        for _ in range(2):
            await client.verify_android(
                provider, token(signing_key, provider), nonce_hash=NONCE_HASH
            )
            clock[0] += seconds
    assert len(calls) == fetches


async def test_google_android_presenter_does_not_relax_web_policy(
    android_settings, signing_key
):
    credential = token(signing_key, "GOOGLE", {"azp": "synthetic-android-client"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        client = SocialProviderClient(android_settings, http)
        assert (
            await client.verify_android("GOOGLE", credential, nonce_hash=NONCE_HASH)
        ).social_type == "GOOGLE"
        with pytest.raises(SocialProviderError) as error:
            await client.verify_google_id_token(credential)
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": ["synthetic-native-key", "other"]},
        {"aud": "synthetic-rest-key"},
        {"aud": "123"},
    ],
)
async def test_kakao_requires_exact_native_audience(
    android_settings, signing_key, changes
):
    android_settings.kakao_rest_api_key = SecretStr("synthetic-rest-key")
    android_settings.kakao_app_id = 123
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"keys": [public_key(signing_key)]})
        )
    ) as http:
        with pytest.raises(SocialProviderError) as error:
            await SocialProviderClient(android_settings, http).verify_android(
                "KAKAO", token(signing_key, "KAKAO", changes), nonce_hash=NONCE_HASH
            )
    assert error.value.code == "SOCIAL_CREDENTIAL_INVALID"


async def test_unknown_provider_never_uses_kakao(android_settings, signing_key):
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as http:
        with pytest.raises(ValueError, match="unsupported social provider"):
            await SocialProviderClient(android_settings, http).verify_android(
                "OTHER", token(signing_key, "GOOGLE"), nonce_hash=NONCE_HASH
            )
    assert not calls
