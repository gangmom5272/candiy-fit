from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.auth import sessions
from app.auth.contract import IssuedAccessToken, IssuedLoginSession
from app.auth.social import LoggedIn
from app.core.db import get_session
from app.core.errors import TemporaryStorageError
from app.users.models import User
from app.users.service import UserNotFoundError

ORIGIN = "http://localhost:5173"


@pytest.fixture
def session_calls(app, monkeypatch):
    async def db_session():
        yield object()

    app.dependency_overrides[get_session] = db_session
    now = datetime(2031, 1, 1, tzinfo=UTC)
    user = User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id="synthetic",
        display_name="합성회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=165,
        weight_kg=55,
        created_at=now,
        updated_at=now,
    )
    result = LoggedIn(
        user,
        IssuedLoginSession(
            uuid4(),
            IssuedAccessToken("synthetic-access", now + timedelta(minutes=15)),
            "synthetic-refresh",
            now + timedelta(days=6),
        ),
    )
    refresh = AsyncMock(return_value=result)
    logout = AsyncMock()
    monkeypatch.setattr(sessions, "refresh_session", refresh)
    monkeypatch.setattr(sessions, "logout_session", logout)
    return refresh, logout


@pytest.mark.parametrize("endpoint", ["refresh", "logout"])
@pytest.mark.parametrize(
    ("headers", "payload", "status"),
    [
        ({}, None, None),
        ({"Origin": ORIGIN}, None, None),
        ({"Origin": "null"}, None, 403),
        ({"Origin": ""}, None, 403),
        ({"Origin": "https://denied.example"}, None, 403),
        ({"Cookie": "cf_refresh=synthetic"}, None, 403),
        (
            {"Cookie": "cf_refresh=synthetic", "Origin": ORIGIN},
            {"refreshToken": "synthetic"},
            422,
        ),
        ({}, {}, 422),
        ({}, {"refreshToken": None}, 422),
        ({}, {"refreshToken": 1}, 422),
        ({}, {"refreshToken": "synthetic", "platformType": "ANDROID"}, 422),
    ],
)
async def test_request_matrix_rejects_before_service(
    client, session_calls, endpoint, headers, payload, status
):
    response = await client.post(
        "/api/v1/auth/" + endpoint,
        headers=headers,
        **({"json": payload} if payload is not None else {}),
    )
    if status is None:
        status = 401 if endpoint == "refresh" else (204 if headers else 422)
    assert response.status_code == status
    for call in session_calls:
        call.assert_not_called()
    if not headers and payload is None:
        assert response.json()["error"]["code"] == (
            "AUTH_REQUIRED" if endpoint == "refresh" else "INVALID_REQUEST"
        )
        if endpoint == "logout":
            assert response.json()["error"]["details"] == [
                {"field": "body", "reason": "missing"}
            ]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    if headers.get("Origin") == ORIGIN:
        assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize("endpoint", ["refresh", "logout"])
@pytest.mark.parametrize("platform", ["WEB", "ANDROID"])
async def test_api_uses_transport_and_preserves_raw_credential(
    client, session_calls, endpoint, platform
):
    token = "synthetic"
    kwargs = (
        {"headers": {"Cookie": "cf_refresh=" + token, "Origin": ORIGIN}}
        if platform == "WEB"
        else {"json": {"refreshToken": token}, "headers": {"Origin": ORIGIN}}
    )
    response = await client.post("/api/v1/auth/" + endpoint, **kwargs)
    call = session_calls[0 if endpoint == "refresh" else 1]
    if platform == "WEB":
        assert response.status_code == (401 if endpoint == "refresh" else 204)
        call.assert_not_called()
        assert "Max-Age=0" in response.headers["set-cookie"]
        return
    assert call.await_args.args[1:3] == (token, platform)
    assert response.status_code == (200 if endpoint == "refresh" else 204)
    assert ("set-cookie" in response.headers) is (platform == "WEB")
    if endpoint == "refresh":
        data = response.json()["data"]
        assert "status" not in data
        assert ("refreshToken" in data) is (platform == "ANDROID")
        assert len(data["user"]) == 9
    else:
        assert response.content == b""
        if platform == "WEB":
            assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize("endpoint", ["refresh", "logout"])
@pytest.mark.parametrize("platform", ["WEB", "ANDROID"])
async def test_rejected_credential_deletes_only_web_cookie(
    client, session_calls, endpoint, platform
):
    call = session_calls[0 if endpoint == "refresh" else 1]
    call.side_effect = sessions.RefreshRejectedError()
    kwargs = (
        {"headers": {"Cookie": "cf_refresh=synthetic", "Origin": ORIGIN}}
        if platform == "WEB"
        else {"json": {"refreshToken": "synthetic"}}
    )
    response = await client.post("/api/v1/auth/" + endpoint, **kwargs)
    if platform == "WEB" and endpoint == "logout":
        assert response.status_code == 204
        call.assert_not_called()
        return
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_REJECTED"
    assert ("set-cookie" in response.headers) is (platform == "WEB")
    if platform == "WEB":
        assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize("retryable", [True, False])
async def test_refresh_outage_has_retryable_and_does_not_delete_cookie(
    client, session_calls, retryable
):
    session_calls[0].side_effect = sessions.RefreshUnavailableError(retryable=retryable)
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refreshToken": "synthetic"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is retryable
    assert "set-cookie" not in response.headers


async def test_logout_outage_has_no_retryable_or_cookie_deletion(client, session_calls):
    session_calls[1].side_effect = TemporaryStorageError()
    response = await client.post(
        "/api/v1/auth/logout",
        json={"refreshToken": "synthetic"},
    )
    assert response.status_code == 503
    assert "retryable" not in response.json()["error"]
    assert "set-cookie" not in response.headers


async def test_missing_member_is_auth_required(client, session_calls):
    session_calls[0].side_effect = UserNotFoundError()
    response = await client.post(
        "/api/v1/auth/refresh", json={"refreshToken": "synthetic"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("endpoint", ["refresh", "logout"])
@pytest.mark.parametrize(
    "raw", ["null", " ", '{"refreshToken":"a","refreshToken":"b"}']
)
async def test_optional_body_keeps_strict_json(client, session_calls, endpoint, raw):
    response = await client.post(
        "/api/v1/auth/" + endpoint,
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    for call in session_calls:
        call.assert_not_called()


@pytest.mark.parametrize("endpoint", ["refresh", "logout"])
async def test_duplicate_origin_and_query_are_rejected(client, session_calls, endpoint):
    response = await client.post(
        "/api/v1/auth/" + endpoint, headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]
    )
    assert response.status_code == 403
    response = await client.post("/api/v1/auth/" + endpoint + "?platformType=WEB")
    assert response.status_code == 422
    for call in session_calls:
        call.assert_not_called()


@pytest.mark.parametrize("value", ["", " \t"])
async def test_logout_blank_token_is_shape_error(client, session_calls, value):
    response = await client.post("/api/v1/auth/logout", json={"refreshToken": value})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "refreshToken", "reason": "invalid_format"}
    ]
    session_calls[1].assert_not_called()


async def test_empty_cookie_is_submitted_credential_and_never_body_fallback(
    client, session_calls
):
    session_calls[0].side_effect = sessions.RefreshRejectedError()
    response = await client.post(
        "/api/v1/auth/refresh", headers={"Cookie": "cf_refresh=", "Origin": ORIGIN}
    )
    assert response.status_code == 401
    session_calls[0].assert_not_called()
    session_calls[0].reset_mock()
    response = await client.post(
        "/api/v1/auth/refresh",
        headers={"Cookie": "cf_refresh=", "Origin": ORIGIN},
        json={"refreshToken": "synthetic"},
    )
    assert response.status_code == 422
    session_calls[0].assert_not_called()


@pytest.mark.parametrize("endpoint", ["refresh", "logout"])
async def test_unexpected_service_error_is_safe_500(client, session_calls, endpoint):
    session_calls[0 if endpoint == "refresh" else 1].side_effect = RuntimeError(
        "synthetic-private SQL"
    )
    response = await client.post(
        "/api/v1/auth/" + endpoint, json={"refreshToken": "synthetic"}
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "synthetic" not in response.text
    assert "retryable" not in response.json()["error"]


@pytest.mark.parametrize(
    ("endpoint", "model"), [("refresh", "RefreshRequest"), ("logout", "LogoutRequest")]
)
def test_openapi_body_can_be_omitted_but_does_not_allow_json_null(app, endpoint, model):
    request_body = app.openapi()["paths"]["/api/v1/auth/" + endpoint]["post"][
        "requestBody"
    ]
    assert request_body.get("required", False) is False
    assert request_body["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/" + model
    }
