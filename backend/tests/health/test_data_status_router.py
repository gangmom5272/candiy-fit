from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic_core import PydanticSerializationError

from app.auth import tokens
from app.health import router, service
from app.health.errors import HealthDataLookupError
from app.health.schemas import HealthDataStatusResult
from app.users.schemas import AppConnection
from tests.health.test_calculations import NOW
from tests.health.test_data_status_response import METRICS, readiness
from tests.health.test_router import health_api as health_api

URL = "/api/v1/health/data-status"


@pytest.fixture
def status_api(health_api, monkeypatch):
    result = HealthDataStatusResult(
        user_id=health_api.owner,
        app_connection=AppConnection(status="UNKNOWN", connected_at=None),
        first_arrived_at=NOW,
        metrics=[readiness(metric) for metric in METRICS],
        checked_at=NOW,
    )
    lookup = AsyncMock(return_value=result)
    monkeypatch.setattr(service, "get_data_status", lookup)
    return health_api, lookup


async def test_status_http_uses_authenticated_owner_and_public_contract(
    client, app, status_api
):
    auth, lookup = status_api
    response = await client.get(
        URL, headers=auth.headers | {"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"appConnection", "firstRecord", "metrics", "checkedAt"}
    assert data["appConnection"] == {"status": "UNKNOWN", "connectedAt": None}
    assert data["firstRecord"]["status"] == "CONFIRMED"
    assert data["firstRecord"]["firstArrivedAt"].endswith("Z")
    assert [item["metric"] for item in data["metrics"]] == list(METRICS)
    assert str(auth.owner) not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    lookup.assert_awaited_once_with(auth.owner, session_factory=app.state.sessionmaker)
    auth.lookup.assert_not_awaited()


@pytest.mark.parametrize("case", ["missing", "invalid", "expired", "deleted"])
async def test_status_auth_failure_never_queries_data(
    client, status_api, monkeypatch, case
):
    auth, lookup = status_api
    headers = auth.headers
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"Authorization": "Bearer synthetic-private"}
    elif case == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    else:
        auth.db.get.return_value = None
    response = await client.get(URL, headers=headers)
    assert response.status_code == 401
    lookup.assert_not_awaited()


@pytest.mark.parametrize("kind", ["query", "body"])
async def test_status_rejects_unexpected_input(client, status_api, kind):
    auth, lookup = status_api
    response = await client.request(
        "GET",
        URL,
        headers=auth.headers,
        **(
            {"params": {"userId": "synthetic-private"}}
            if kind == "query"
            else {"json": {}}
        ),
    )
    assert response.status_code == 422
    lookup.assert_not_awaited()


async def test_status_checks_result_owner_before_conversion(
    client, status_api, monkeypatch
):
    auth, lookup = status_api
    lookup.return_value = lookup.return_value.model_copy(update={"user_id": uuid4()})
    converter = Mock(side_effect=AssertionError("must not serialize another user"))
    monkeypatch.setattr(router, "data_status_response", converter)
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == 403
    converter.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["lookup", "serialization", "invalid_result", "unexpected"]
)
async def test_status_failure_is_whole_error_not_unconfirmed(
    client, status_api, monkeypatch, kind
):
    auth, lookup = status_api
    if kind == "lookup":
        lookup.side_effect = HealthDataLookupError(METRICS)
    elif kind == "serialization":
        monkeypatch.setattr(
            router,
            "data_status_response",
            Mock(side_effect=PydanticSerializationError("synthetic-private")),
        )
    elif kind == "invalid_result":
        lookup.return_value = lookup.return_value.model_copy(
            update={"first_arrived_at": "synthetic-private"}
        )
    else:
        lookup.side_effect = RuntimeError("synthetic-private")
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == (500 if kind == "unexpected" else 503)
    assert "data" not in response.json()
    assert "synthetic-private" not in response.text
    if kind != "unexpected":
        assert response.json()["error"]["context"] == {
            "reason": "LOOKUP_FAILED",
            "metrics": list(METRICS),
        }


def test_status_openapi_has_no_parameters_and_requires_auth(app):
    doc = app.openapi()
    operation = doc["paths"][URL]["get"]
    assert not operation.get("parameters") and "requestBody" not in operation
    assert operation["security"] == [{"HTTPBearer": []}, {"APIKeyCookie": []}]
    schema = doc["components"]["schemas"]["HealthDataStatusResponse"]
    assert set(schema["required"]) == {
        "appConnection",
        "firstRecord",
        "metrics",
        "checkedAt",
    }
    assert "userId" not in schema["properties"]
