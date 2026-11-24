from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends

from app.auth import tokens
from app.auth.contract import AuthenticatedUser
from app.auth.dependencies import get_current_user

NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.fixture
def protected(app, settings, monkeypatch):
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    user_id = uuid4()

    @app.api_route("/api/v1/protected", methods=["GET", "POST"])
    async def current(user: Annotated[AuthenticatedUser, Depends(get_current_user)]):
        return {"data": {"userId": str(user.user_id)}}

    return user_id, tokens.issue_access_token(user_id, settings, now=NOW).token


async def test_identity_only_from_verified_access(client, protected):
    user_id, token = protected
    response = await client.post(
        f"/api/v1/protected?userId={uuid4()}",
        json={"userId": str(uuid4())},
        headers={"Authorization": f"Bearer {token}", "X-User-Id": str(uuid4())},
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"userId": str(user_id)}}


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer", "Bearer ", "Bearer synthetic-opaque"],
)
async def test_missing_or_invalid_access(client, protected, authorization):
    headers = {"Authorization": authorization} if authorization is not None else {}
    headers["Cookie"] = "cf_refresh=synthetic-refresh; signupToken=synthetic-signup"
    response = await client.get("/api/v1/protected", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


async def test_expired_access(client, protected, monkeypatch):
    _, token = protected
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    response = await client.get(
        "/api/v1/protected", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_EXPIRED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_duplicate_authorization_rejected(client, protected):
    _, token = protected
    response = await client.get(
        "/api/v1/protected",
        headers=[
            ("Authorization", f"Bearer {token}"),
            ("Authorization", f"Bearer {token}"),
        ],
    )
    assert response.status_code == 401
