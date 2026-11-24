from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.auth import tokens
from app.core.db import get_session
from app.core.errors import TemporaryStorageError
from app.users import service

ORIGIN = "http://localhost:5173"
PATH = "/api/v1/users/me"
NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "method,status,origin",
    [
        ("GET", 200, ORIGIN),
        ("GET", 401, ORIGIN),
        ("GET", 503, ORIGIN),
        ("GET", 500, ORIGIN),
        ("PATCH", 200, ORIGIN),
        ("PATCH", 401, ORIGIN),
        ("PATCH", 422, ORIGIN),
        ("PATCH", 503, ORIGIN),
        ("PATCH", 500, ORIGIN),
        ("GET", 200, None),
        ("PATCH", 200, None),
        ("GET", 200, "https://denied.example"),
        ("PATCH", 200, "https://denied.example"),
    ],
)
async def test_profile_cors_preserves_authentication_and_response_contract(
    app, client, settings, make_user, monkeypatch, caplog, method, status, origin
):
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    user = make_user()
    db = AsyncMock()

    async def session():
        yield db

    app.dependency_overrides[get_session] = session
    call = AsyncMock(return_value=user)
    if status == 503:
        call.side_effect = TemporaryStorageError("synthetic-private-db")
    elif status == 500:
        call.side_effect = RuntimeError("synthetic-private-bug")
    name = "get_current_profile" if method == "GET" else "update_current_profile"
    monkeypatch.setattr(service, name, call)
    headers = {} if origin is None else {"Origin": origin}
    if status != 401:
        token = tokens.issue_access_token(user.id, settings, now=NOW).token
        headers["Authorization"] = "Bearer " + token
    kwargs = {}
    if method == "PATCH":
        kwargs["json"] = (
            {"heightCm": "synthetic-private-input"}
            if status == 422
            else {"displayName": user.display_name}
        )
    response = await client.request(method, PATH, headers=headers, **kwargs)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4
    assert "synthetic-private" not in response.text + caplog.text
    if origin == ORIGIN:
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "Origin" in response.headers["vary"]
        assert "X-Request-Id" in response.headers["access-control-expose-headers"]
    else:
        assert "access-control-allow-origin" not in response.headers
    if status == 200:
        data = response.json()["data"]
        assert len(data) == 9
        assert data["id"] == data["endUserId"] == str(user.id)
    else:
        assert (
            response.json()["error"]["code"]
            == {
                401: "AUTH_REQUIRED",
                422: "INVALID_REQUEST",
                503: "USER_UNAVAILABLE",
                500: "INTERNAL_ERROR",
            }[status]
        )
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    if status in {401, 422}:
        call.assert_not_awaited()
    else:
        call.assert_awaited_once()
        assert call.await_args.args[:2] == (db, user.id)


@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("origin", [ORIGIN, "https://denied.example"])
async def test_profile_preflight_needs_no_authentication_or_database(
    app, client, method, origin
):
    session = AsyncMock()
    app.dependency_overrides[get_session] = session
    response = await client.options(
        PATH,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == (200 if origin == ORIGIN else 400)
    assert ("access-control-allow-origin" in response.headers) == (origin == ORIGIN)
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4
    session.assert_not_called()
