from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.core.db import get_session

NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.fixture
def profile_api(app, settings, make_user, monkeypatch):
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    members = [make_user(), make_user()]
    db = AsyncMock(spec=AsyncSession)
    db.in_transaction.return_value = False
    db.get.side_effect = lambda model, key: next(
        (u for u in members if u.id == key), None
    )

    async def session():
        yield db

    app.dependency_overrides[get_session] = session

    def headers(user_id):
        return {
            "Authorization": "Bearer "
            + tokens.issue_access_token(user_id, settings, now=NOW).token
        }

    return members, db, headers


async def test_get_isolated_identity_and_public_contract(client, profile_api):
    members, db, headers = profile_api
    for user in members:
        response = await client.get(
            "/api/v1/users/me", headers=headers(user.id) | {"X-User-Id": str(uuid4())}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data) == {
            "id",
            "endUserId",
            "displayName",
            "birthYear",
            "gender",
            "heightCm",
            "weightKg",
            "socialType",
            "createdAt",
        }
        assert data["id"] == data["endUserId"] == str(user.id)
        assert data["heightCm"] == 165.25 and not isinstance(data["weightKg"], str)
        assert response.headers["cache-control"] == "no-store"
        assert UUID(response.headers["x-request-id"]).version == 4
    assert db.get.await_count == 2


@pytest.mark.parametrize(
    "mode,code",
    [
        ("missing", "AUTH_REQUIRED"),
        ("invalid", "AUTH_REQUIRED"),
        ("expired", "AUTH_EXPIRED"),
        ("absent_member", "AUTH_REQUIRED"),
    ],
)
async def test_get_authentication_failures(
    client, profile_api, monkeypatch, mode, code
):
    members, db, headers = profile_api
    auth = headers(uuid4() if mode == "absent_member" else members[0].id)
    if mode == "missing":
        auth = {}
    elif mode == "invalid":
        auth = {"Authorization": "Bearer synthetic-invalid"}
    elif mode == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    response = await client.get("/api/v1/users/me", headers=auth)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == code
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert db.get.await_count == (1 if mode == "absent_member" else 0)


@pytest.mark.parametrize(
    "error,status,code",
    [
        (ConnectionError("synthetic-private"), 503, "USER_UNAVAILABLE"),
        (
            ProgrammingError("private SQL", {}, Exception("private database detail")),
            500,
            "INTERNAL_ERROR",
        ),
        (RuntimeError("private bug"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_get_storage_failure_is_not_missing_member(
    client, profile_api, error, status, code, caplog
):
    members, db, headers = profile_api
    db.get.side_effect = error
    response = await client.get("/api/v1/users/me", headers=headers(members[0].id))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "details" not in response.json()["error"]
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert "private" not in response.text + caplog.text


@pytest.mark.parametrize("query,body", [("?userId=other", None), ("", "{}")])
async def test_get_rejects_query_and_body(client, profile_api, query, body):
    members, db, headers = profile_api
    response = await client.request(
        "GET", "/api/v1/users/me" + query, content=body, headers=headers(members[0].id)
    )
    assert response.status_code == 422
    db.get.assert_not_awaited()
