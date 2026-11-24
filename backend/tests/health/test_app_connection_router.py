from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.auth import tokens
from app.auth.web import SESSION_COOKIE
from app.core.errors import TemporaryStorageError
from app.users import service
from app.users.schemas import AppConnection
from tests.health.test_calculations import NOW
from tests.health.test_router import health_api as health_api

URL = "/api/v1/health/app-connection"


@pytest.fixture
def connection_api(health_api, monkeypatch):
    save = AsyncMock(return_value=AppConnection(status="CONNECTED", connected_at=NOW))
    monkeypatch.setattr(service, "complete_app_connection", save)
    return health_api, save


async def test_put_saves_only_authenticated_member(client, connection_api):
    auth, save = connection_api
    response = await client.put(URL, headers=auth.headers)
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "status": "CONNECTED",
            "connectedAt": NOW.isoformat().replace("+00:00", "Z"),
        }
    }
    save.assert_awaited_once_with(auth.db, auth.owner)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


@pytest.mark.parametrize("kind", ["missing", "invalid", "expired", "cookie", "mixed"])
async def test_put_requires_bearer_and_rejects_web_cookie(
    client, connection_api, monkeypatch, kind
):
    auth, save = connection_api
    headers = auth.headers.copy()
    if kind in {"missing", "cookie"}:
        headers = {}
    if kind == "invalid":
        headers["Authorization"] = "Bearer synthetic-invalid"
    if kind == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    if kind in {"cookie", "mixed"}:
        client.cookies.set(SESSION_COOKIE, "synthetic-web-cookie")
    response = await client.put(URL, headers=headers)
    assert response.status_code == 401
    save.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {}},
        {"json": {"userId": "other"}},
        {"content": "null"},
        {"params": {"userId": "other"}},
        {"params": {"status": "CONNECTED"}},
    ],
)
async def test_put_rejects_all_body_and_query_input(client, connection_api, kwargs):
    auth, save = connection_api
    response = await client.put(URL, headers=auth.headers, **kwargs)
    assert response.status_code == 422
    save.assert_not_awaited()


@pytest.mark.parametrize(
    "error,status,code",
    [
        (service.UserNotFoundError(), 401, "AUTH_REQUIRED"),
        (TemporaryStorageError("private"), 503, "APP_CONNECTION_UNAVAILABLE"),
        (RuntimeError("private"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_put_failure_never_returns_connected(
    client, connection_api, error, status, code
):
    auth, save = connection_api
    save.side_effect = error
    response = await client.put(URL, headers=auth.headers)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "data" not in response.json() and "private" not in response.text


def test_put_openapi_is_bodyless_and_bearer_only(app):
    operation = app.openapi()["paths"][URL]["put"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert not operation.get("parameters") and "requestBody" not in operation
