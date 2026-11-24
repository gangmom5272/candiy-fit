import json
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository, signup, tokens
from app.auth import router as auth_router
from app.auth.dependencies import get_social_provider
from app.auth.models import AuthState
from app.auth.providers import SocialProviderClient
from app.auth.social import hash_signup_token
from app.core.db import get_session
from app.users.models import User

NOW = datetime(2026, 10, 24, tzinfo=UTC)
PATH = "/api/v1/auth/signup"
BODY = {
    "signupToken": "synthetic-signup",
    "displayName": "  합성회원  ",
    "birthYear": 2000,
    "gender": "FEMALE",
    "heightCm": 165.25,
    "weightKg": 55,
}
HEADERS = {"Origin": "http://localhost:5173"}


@pytest.fixture
def signup_api(app, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(signup, "_utc_now", lambda: NOW)
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    monkeypatch.setattr(auth_router, "datetime", Clock)
    db = AsyncMock(spec=AsyncSession)
    state = AuthState(
        purpose="SIGNUP",
        social_type="GOOGLE",
        social_user_id="synthetic-subject",
        platform_type="WEB",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    lookup = AsyncMock(return_value=state)
    monkeypatch.setattr(repository, "find_auth_state", lookup)

    async def insert(session, social_type, social_user_id, profile):
        return User(
            id=uuid4(),
            social_type=social_type,
            social_user_id=social_user_id,
            created_at=NOW,
            updated_at=NOW,
            **profile,
        )

    monkeypatch.setattr(
        repository, "insert_social_user_if_absent", AsyncMock(side_effect=insert)
    )

    async def flush():
        if db.add.called:
            db.add.call_args.args[0].id = uuid4()

    db.flush.side_effect = flush

    async def session():
        yield db

    app.dependency_overrides[get_session] = session
    provider = AsyncMock(spec=SocialProviderClient)
    app.dependency_overrides[get_social_provider] = lambda: provider
    return db, state, lookup, provider


@pytest.mark.parametrize("platform", ["WEB", "ANDROID"])
async def test_signup_success_public_response_and_platform_credentials(
    client, signup_api, settings, platform
):
    _, state, _, provider = signup_api
    state.platform_type = platform
    response = await client.post(
        PATH, json=BODY, headers=HEADERS if platform == "WEB" else {}
    )
    assert response.status_code == 201
    assert response.headers["location"] == "/api/v1/users/me"
    data = response.json()["data"]
    expected = {"status", "accessToken", "accessTokenExpiresAt", "user"}
    assert set(data) == (
        expected | {"refreshToken"}
        if platform == "ANDROID"
        else {"status", "user", "expiresAt"}
    )
    assert data["status"] == "LOGGED_IN" and len(data["user"]) == 9
    assert data["user"]["displayName"] == "합성회원"
    assert isinstance(data["user"]["heightCm"], (int, float))
    if platform == "ANDROID":
        assert tokens.authenticate_token(data["accessToken"], settings).user_id == UUID(
            data["user"]["id"]
        )
    if platform == "WEB":
        cookie = SimpleCookie(response.headers.get_list("set-cookie")[0])[
            "__Host-cf_session"
        ]
        assert cookie["httponly"] and cookie["secure"]
        assert (
            cookie["samesite"] == "lax"
            and cookie["path"] == "/"
            and not cookie["domain"]
        )
        assert int(cookie["max-age"]) == 7 * 86400 and cookie.value not in response.text
    else:
        assert data["refreshToken"] and "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    UUID(response.headers["x-request-id"])
    provider.verify_web.assert_not_awaited()


async def test_existing_member_response_is_200_without_location(
    client, signup_api, monkeypatch
):
    saved = User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id="synthetic-subject",
        display_name="기존회원",
        birth_year=1999,
        gender="MALE",
        height_cm=175,
        weight_kg=70,
        created_at=NOW,
    )
    monkeypatch.setattr(
        repository, "insert_social_user_if_absent", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(repository, "find_social_user", AsyncMock(return_value=saved))
    response = await client.post(PATH, json=BODY, headers=HEADERS)
    assert response.status_code == 200 and "location" not in response.headers
    assert response.json()["data"]["user"]["displayName"] == "기존회원"


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("signupToken", None, "null_not_allowed"),
        ("signupToken", 1, "invalid_type"),
        ("signupToken", " ", "invalid_format"),
        ("gender", 123, "invalid_type"),
        ("heightCm", "165", "invalid_type"),
        ("birthYear", True, "invalid_type"),
        ("platformType", "ANDROID", "extra_forbidden"),
        ("userId", None, "extra_forbidden"),
    ],
)
async def test_request_shape_rejected_before_credential(
    client, signup_api, field, value, reason
):
    db, _, lookup, _ = signup_api
    response = await client.post(PATH, json=BODY | {field: value}, headers=HEADERS)
    assert response.status_code == 422
    assert {"field": field, "reason": reason} in response.json()["error"]["details"]
    lookup.assert_not_awaited()
    db.begin.assert_not_called()


@pytest.mark.parametrize("field", list(BODY))
async def test_missing_fields_report_missing(client, signup_api, field):
    body = BODY.copy()
    del body[field]
    response = await client.post(PATH, json=body, headers=HEADERS)
    assert response.status_code == 422
    assert {"field": field, "reason": "missing"} in response.json()["error"]["details"]


@pytest.mark.parametrize("invalid", ["missing", "expired", "consumed", "purpose"])
async def test_credential_failure_precedes_profile_value_failure(
    client, signup_api, invalid
):
    _, state, lookup, _ = signup_api
    if invalid == "missing":
        lookup.return_value = None
    elif invalid == "expired":
        state.expires_at = NOW
    elif invalid == "consumed":
        state.consumed_at = NOW
    else:
        state.purpose = "ANDROID_LOGIN"
    response = await client.post(PATH, json=BODY | {"gender": "OTHER"}, headers=HEADERS)
    assert (
        response.status_code == 401
        and response.json()["error"]["code"] == "SIGNUP_TOKEN_INVALID"
    )
    assert response.headers["www-authenticate"] == "Bearer"
    assert "set-cookie" not in response.headers
    assert response.headers["access-control-allow-origin"] == HEADERS["Origin"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "null"},
        {"Origin": "https://evil.example"},
        [("Origin", "http://localhost:5173"), ("Origin", "http://localhost:5173")],
    ],
)
async def test_web_origin_failure_precedes_profile_and_preserves_state(
    client, signup_api, headers
):
    _, state, _, _ = signup_api
    response = await client.post(PATH, json=BODY | {"heightCm": -1}, headers=headers)
    assert (
        response.status_code == 403
        and response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    )
    assert state.consumed_at is None and "set-cookie" not in response.headers


async def test_original_json_precision_is_used_after_credential_check(
    client, signup_api
):
    raw = json.dumps(BODY).replace("165.25", "165.250000000000000000001")
    response = await client.post(
        PATH, content=raw, headers=HEADERS | {"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert {"field": "heightCm", "reason": "decimal_places"} in response.json()[
        "error"
    ]["details"]
    assert signup_api[1].consumed_at is None


@pytest.mark.parametrize(
    "failure,expected",
    [
        (ConnectionError("synthetic-private"), (503, "AUTH_UNAVAILABLE")),
        (
            IntegrityError("synthetic-private", {}, ValueError()),
            (500, "INTERNAL_ERROR"),
        ),
    ],
)
async def test_storage_errors_are_safe_and_have_cors(
    client, signup_api, failure, expected, caplog
):
    signup_api[2].side_effect = failure
    response = await client.post(PATH, json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()["error"]["code"]) == expected
    assert response.headers["access-control-allow-origin"] == HEADERS["Origin"]
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers and "location" not in response.headers
    assert "synthetic-private" not in response.text + caplog.text


async def test_commit_failure_does_not_publish_cookie_or_tokens(client, signup_api):
    db, _, _, _ = signup_api
    db.begin.return_value.__aexit__.side_effect = ConnectionError()
    response = await client.post(PATH, json=BODY, headers=HEADERS)
    assert response.status_code == 503 and "data" not in response.json()
    assert "set-cookie" not in response.headers and "location" not in response.headers


def test_signup_openapi_has_required_fields_and_both_success_statuses(app):
    schema = app.openapi()
    operation = schema["paths"][PATH]["post"]
    assert {"200", "201"} <= operation["responses"].keys()
    request = schema["components"]["schemas"]["SignupRequest"]
    assert set(request["required"]) == set(BODY)
    assert request["additionalProperties"] is False
    assert not operation.get("security")


async def test_signup_token_is_not_trimmed_or_replaced_by_other_credentials(
    client, signup_api
):
    _, _, lookup, _ = signup_api
    body = BODY | {"signupToken": " synthetic-signup "}
    response = await client.post(
        PATH, json=body, headers=HEADERS | {"Authorization": "Bearer unrelated-token"}
    )
    assert response.status_code == 201
    assert lookup.await_args_list[0].args[1] == hash_signup_token(body["signupToken"])


async def test_undeclared_query_is_rejected_before_signup(client, signup_api):
    response = await client.post(PATH + "?userId=synthetic", json=BODY, headers=HEADERS)
    assert response.status_code == 422
    assert {"field": "userId", "reason": "extra_forbidden"} in response.json()["error"][
        "details"
    ]
    signup_api[2].assert_not_awaited()
