import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import android, repository, tokens
from app.auth.contract import VerifiedSocialSubject
from app.auth.dependencies import get_social_provider
from app.auth.models import AuthState
from app.auth.providers import (
    ProviderConfigurationError,
    SocialProviderClient,
    SocialProviderError,
)
from app.core.db import get_session
from app.users.models import User

NOW = datetime(2026, 10, 29, tzinfo=UTC)
PATH = "/api/v1/auth/social/login"
BODY = {
    "socialType": "GOOGLE",
    "platformType": "ANDROID",
    "idToken": "synthetic-private-id-token",
    "challengeToken": "synthetic-private-challenge",
}


@pytest.fixture
def android_api(app, monkeypatch):
    monkeypatch.setattr(android, "_utc_now", lambda: NOW)
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    state = AuthState(
        purpose="ANDROID_LOGIN",
        platform_type="ANDROID",
        social_type="GOOGLE",
        nonce_hash=hashlib.sha256(b"synthetic-nonce").hexdigest(),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    find = AsyncMock(return_value=state)
    monkeypatch.setattr(repository, "find_auth_state", find)

    async def flush():
        if db.add.called:
            db.add.call_args.args[0].id = uuid4()

    db.flush.side_effect = flush

    async def session():
        yield db

    provider = AsyncMock(spec=SocialProviderClient)
    provider.verify_android.side_effect = lambda social_type, token, **kwargs: (
        VerifiedSocialSubject(social_type, "synthetic-subject")
    )
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_social_provider] = lambda: provider
    return db, state, find, provider


@pytest.mark.parametrize("social_type", ["GOOGLE", "KAKAO"])
@pytest.mark.parametrize("existing", [True, False])
async def test_android_login_public_result(
    client, android_api, settings, social_type, existing
):
    db, state, find, provider = android_api
    state.social_type = social_type
    if existing:
        db.scalar.return_value = User(
            id=uuid4(),
            social_type=social_type,
            social_user_id="synthetic-subject",
            display_name="합성회원",
            birth_year=2000,
            gender="FEMALE",
            height_cm=Decimal("165.25"),
            weight_kg=Decimal("55"),
            created_at=NOW,
            updated_at=NOW,
        )
    response = await client.post(PATH, json=BODY | {"socialType": social_type})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    data = response.json()["data"]
    if existing:
        assert set(data) == {
            "status",
            "accessToken",
            "accessTokenExpiresAt",
            "refreshToken",
            "user",
        }
        assert data["status"] == "LOGGED_IN" and len(data["user"]) == 9
        assert data["user"]["socialType"] == social_type
        assert data["accessTokenExpiresAt"] == "2026-10-29T00:15:00Z"
        assert tokens.authenticate_token(data["accessToken"], settings).user_id == UUID(
            data["user"]["id"]
        )
        assert data["refreshToken"]
    else:
        assert set(data) == {
            "status",
            "signupToken",
            "signupTokenExpiresAt",
            "socialType",
        }
        assert data["status"] == "SIGNUP_REQUIRED" and data["socialType"] == social_type
        assert data["signupTokenExpiresAt"] == "2026-10-29T00:10:00Z"
    provider.verify_web.assert_not_awaited()
    provider.verify_android.assert_awaited_once_with(
        social_type, BODY["idToken"], nonce_hash=state.nonce_hash
    )
    assert find.await_count == 2 and state.consumed_at == NOW
    assert (
        BODY["idToken"] not in response.text
        and BODY["challengeToken"] not in response.text
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://untrusted.example"},
        {"Cookie": "cf_refresh=synthetic-old-web-token"},
        [("Origin", "https://a.example"), ("Origin", "https://b.example")],
    ],
)
async def test_android_does_not_apply_web_origin_or_cookie_policy(
    client, android_api, headers
):
    response = await client.post(PATH, json=BODY, headers=headers)
    assert response.status_code == 200 and "set-cookie" not in response.headers


@pytest.mark.parametrize("field", list(BODY))
@pytest.mark.parametrize(
    "value,reason",
    [
        (None, "null_not_allowed"),
        (123, "invalid_type"),
        ([], "invalid_type"),
        (True, "invalid_type"),
    ],
)
async def test_android_required_field_types(
    client, android_api, field, value, reason, caplog
):
    db, _, _, provider = android_api
    response = await client.post(PATH, json=BODY | {field: value})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    assert BODY["idToken"] not in response.text + caplog.text
    assert BODY["challengeToken"] not in response.text + caplog.text
    db.begin.assert_not_called()
    provider.verify_android.assert_not_awaited()
    provider.verify_web.assert_not_awaited()


@pytest.mark.parametrize("field", list(BODY))
async def test_android_required_field_missing(client, android_api, field):
    body = BODY.copy()
    del body[field]
    response = await client.post(PATH, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": field, "reason": "missing"}
    ]
    android_api[0].begin.assert_not_called()


@pytest.mark.parametrize("field", ["socialType", "platformType"])
async def test_unknown_discriminator_or_provider_has_public_field(
    client, android_api, field
):
    response = await client.post(PATH, json=BODY | {field: "synthetic-private-unknown"})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": field, "reason": "invalid_choice"}
    ]
    assert "synthetic-private-unknown" not in response.text
    android_api[0].begin.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "authorizationCode",
        "codeVerifier",
        "redirectUri",
        "nonce",
        "refreshToken",
        "id_token",
        "challenge_token",
        "ANDROID",
        "WEB",
    ],
)
async def test_android_rejects_other_platform_and_extra_fields(
    client, android_api, field
):
    response = await client.post(PATH, json=BODY | {field: "synthetic-private-extra"})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": field, "reason": "extra_forbidden"}
    ]
    assert "synthetic-private-extra" not in response.text
    android_api[0].begin.assert_not_called()


@pytest.mark.parametrize(
    "raw,field,reason",
    [
        (b"", "body", "missing"),
        (b"null", "body", "null_not_allowed"),
        (b"[]", "body", "invalid_type"),
        (b'"text"', "body", "invalid_type"),
        (b"123", "body", "invalid_type"),
        (b"{", "body", "invalid_json"),
        (
            b'{"platformType":"ANDROID","platformType":"WEB"}',
            "platformType",
            "duplicate",
        ),
        (b'{"idToken":NaN}', "body", "invalid_json"),
    ],
)
async def test_android_json_contract(client, android_api, raw, field, reason):
    response = await client.post(
        PATH, content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    android_api[0].begin.assert_not_called()


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
        (ProviderConfigurationError("synthetic-private-config"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_android_provider_failure_has_no_credentials(
    client, android_api, failure, status, code, caplog
):
    _, state, _, provider = android_api
    provider.verify_android.side_effect = failure
    response = await client.post(PATH, json=BODY)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "data" not in response.json() and "set-cookie" not in response.headers
    assert "synthetic-private" not in response.text + caplog.text
    assert state.consumed_at is None


@pytest.mark.parametrize("stage", ["lookup", "commit"])
async def test_android_storage_failure_has_no_credentials(client, android_api, stage):
    db, _, find, _ = android_api
    if stage == "lookup":
        find.side_effect = ConnectionError("synthetic-private-storage")
    else:
        db.begin.return_value.__aexit__.side_effect = [
            None,
            ConnectionError("synthetic-private-commit"),
        ]
    response = await client.post(PATH, json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "data" not in response.json() and "set-cookie" not in response.headers
    assert "retryable" not in response.json()["error"]


async def test_android_sql_bug_is_500(client, android_api, caplog):
    android_api[2].side_effect = ProgrammingError(
        "synthetic-private-sql", {}, Exception("synthetic-private")
    )
    response = await client.post(PATH, json=BODY)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "data" not in response.json() and "set-cookie" not in response.headers
    assert "synthetic-private" not in response.text + caplog.text


async def test_unknown_challenge_maps_to_400_before_provider(client, android_api):
    android_api[2].return_value = None
    response = await client.post(PATH, json=BODY)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOCIAL_CREDENTIAL_INVALID"
    android_api[3].verify_android.assert_not_awaited()


@pytest.mark.parametrize("field", ["idToken", "challengeToken"])
@pytest.mark.parametrize("value", ["", " "])
async def test_blank_credentials_are_invalid_credentials_not_missing_fields(
    client, android_api, field, value
):
    if field == "idToken":
        android_api[3].verify_android.side_effect = SocialProviderError(
            "SOCIAL_CREDENTIAL_INVALID"
        )
    else:
        android_api[2].return_value = None
    response = await client.post(PATH, json=BODY | {field: value})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOCIAL_CREDENTIAL_INVALID"


async def test_login_openapi_describes_both_platforms_and_results(app):
    schema = app.openapi()
    login = schema["paths"][PATH]["post"]
    body = login["requestBody"]["content"]["application/json"]["schema"]
    assert body["discriminator"]["propertyName"] == "platformType"
    assert set(body["discriminator"]["mapping"]) == {"WEB", "ANDROID"}
    models = schema["components"]["schemas"]
    for name, fields in [
        ("AndroidSocialLoginRequest", set(BODY)),
        (
            "WebSocialLoginRequest",
            {
                "socialType",
                "platformType",
                "authorizationCode",
                "codeVerifier",
                "redirectUri",
            },
        ),
    ]:
        assert set(models[name]["required"]) == fields
        assert set(models[name]["properties"]) == fields
        assert models[name]["additionalProperties"] is False
    result_ref = login["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].split("/")[-1]
    alternatives = models[result_ref]["properties"]["data"]["anyOf"]
    assert {item["$ref"].split("/")[-1] for item in alternatives} == {
        "AndroidLoggedInResponse",
        "WebLoggedInResponse",
        "SignupRequiredResponse",
    }
    assert "refreshToken" not in models["WebLoggedInResponse"]["properties"]
    assert "refreshToken" in models["AndroidLoggedInResponse"]["required"]


async def test_request_repr_does_not_expose_credentials():
    from pydantic import TypeAdapter

    from app.auth.schemas import SocialLoginRequest

    parsed = TypeAdapter(SocialLoginRequest).validate_python(BODY)
    assert BODY["idToken"] not in repr(parsed)
    assert BODY["challengeToken"] not in repr(parsed)


async def test_android_rejects_wrong_content_type_and_query(client, android_api):
    response = await client.post(
        PATH, content=json.dumps(BODY), headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "body", "reason": "invalid_format"}
    ]
    response = await client.post(PATH + "?nonce=synthetic-extra", json=BODY)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "nonce", "reason": "extra_forbidden"}
    ]
    android_api[0].begin.assert_not_called()


@pytest.mark.parametrize("platform", ["WEB", "ANDROID"])
async def test_opposite_platform_fields_do_not_select_other_union_branch(
    client, android_api, platform
):
    web_fields = {
        "authorizationCode": "synthetic-private-code",
        "codeVerifier": "a" * 43,
        "redirectUri": "https://web.example/callback",
    }
    android_fields = {
        "idToken": BODY["idToken"],
        "challengeToken": BODY["challengeToken"],
    }
    sent, missing = (
        (android_fields, web_fields)
        if platform == "WEB"
        else (web_fields, android_fields)
    )
    response = await client.post(
        PATH, json={"socialType": "GOOGLE", "platformType": platform} | sent
    )
    assert response.status_code == 422
    expected = {(field, "missing") for field in missing} | {
        (field, "extra_forbidden") for field in sent
    }
    assert {
        (item["field"], item["reason"]) for item in response.json()["error"]["details"]
    } == expected
    android_api[0].begin.assert_not_called()
    android_api[3].verify_web.assert_not_awaited()
    android_api[3].verify_android.assert_not_awaited()
