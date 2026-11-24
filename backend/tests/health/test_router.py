from contextlib import asynccontextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic_core import PydanticSerializationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.health import rest_schemas, router, service
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)
from tests.health.test_calculations import NOW, calculate
from tests.health.test_rest_response import result_for

URL = "/api/v1/health/metrics/steps?start=2026-10-16&end=2026-10-22"


@pytest.fixture
def health_api(app, settings, monkeypatch):
    owner = uuid4()
    events = []
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = SimpleNamespace(id=owner)
    db.in_transaction.return_value = False

    @asynccontextmanager
    async def factory():
        events.append("member_open")
        try:
            yield db
        finally:
            events.append("member_closed")

    async def query(user_id, metric, start, end, *, session_factory):
        assert events[-1] == "member_closed"
        assert session_factory is factory
        return result_for(metric).model_copy(update={"user_id": user_id})

    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)

    class Clock(rest_schemas.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(rest_schemas, "datetime", Clock)
    app.state.sessionmaker = factory
    lookup = AsyncMock(side_effect=query)
    monkeypatch.setattr(service, "get_metric_result", lookup)
    token = tokens.issue_access_token(owner, settings, now=NOW).token
    return SimpleNamespace(
        owner=owner,
        headers={"Authorization": f"Bearer {token}"},
        lookup=lookup,
        db=db,
        events=events,
    )


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
)
async def test_metric_http_uses_authenticated_owner_and_closed_member_session(
    client, health_api, metric
):
    response = await client.get(
        URL.replace("/steps?", f"/{metric}?"),
        headers=health_api.headers | {"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["metric"] == metric
    assert "dataStatus" in response.json()["data"]
    assert str(health_api.owner) not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    args = health_api.lookup.await_args.args
    assert args[0] == health_api.owner and args[1] == metric
    assert args[2].isoformat() == "2026-10-16" and args[3].isoformat() == "2026-10-22"


async def test_metric_http_accepts_old_period_and_passes_parsed_dates(
    client, app, health_api
):
    start, end = date(2000, 1, 1), date(2000, 1, 2)
    health_api.lookup.side_effect = None
    health_api.lookup.return_value = calculate(
        [], metric="exercises", start=start, end=end
    ).model_copy(update={"user_id": health_api.owner})
    response = await client.get(
        "/api/v1/health/metrics/exercises?start=2000-01-01&end=2000-01-02",
        headers=health_api.headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["metric"] == "exercises"
    assert data["period"] == {"start": "2000-01-01", "end": "2000-01-02"}
    health_api.lookup.assert_awaited_once_with(
        health_api.owner,
        "exercises",
        start,
        end,
        session_factory=app.state.sessionmaker,
    )


@pytest.mark.parametrize("case", ["missing", "invalid", "expired", "deleted"])
async def test_metric_http_auth_failure_never_queries_health(
    client, health_api, monkeypatch, case
):
    headers = health_api.headers
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"Authorization": "Bearer synthetic-private"}
    elif case == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    else:
        health_api.db.get.return_value = None
    response = await client.get(URL, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "AUTH_EXPIRED" if case == "expired" else "AUTH_REQUIRED"
    )
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    health_api.lookup.assert_not_awaited()


@pytest.mark.parametrize(
    "path,field,reason",
    [
        ("unknown?start=2026-10-16&end=2026-10-22", "metric", "invalid_choice"),
        ("steps?end=2026-10-22", "start", "missing"),
        ("steps?start=2000-01-01", "end", "missing"),
        (
            "steps?start=2000-01-01&end=2000-01-02&end=2000-01-03",
            "end",
            "duplicate",
        ),
        ("steps?start=synthetic-private&end=2000-01-01", "start", "invalid_format"),
        (
            "exercise_sessions?start=2000-01-01&end=2000-01-02",
            "metric",
            "invalid_choice",
        ),
        ("steps?start=2026-10-22&end=2026-10-21", "start", "out_of_range"),
        ("steps?start=2026-10-22&end=2026-10-23", "end", "out_of_range"),
        ("steps?start=2025-10-20&end=2026-10-22", "end", "out_of_range"),
        ("steps?start=2026-10-16T00:00:00Z&end=2026-10-22", "start", "invalid_format"),
        (
            "steps?start=2026-10-16&end=2026-10-22&userId=synthetic-private",
            "userId",
            "extra_forbidden",
        ),
        (
            "steps?start=2026-10-16&start=2026-10-17&end=2026-10-22",
            "start",
            "duplicate",
        ),
    ],
)
async def test_metric_http_rejects_bad_input_before_query(
    client, health_api, path, field, reason
):
    response = await client.get(
        f"/api/v1/health/metrics/{path}", headers=health_api.headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    assert "synthetic-private" not in response.text
    health_api.lookup.assert_not_awaited()


async def test_metric_http_rejects_get_body(client, health_api):
    response = await client.request(
        "GET", URL, headers=health_api.headers, json={"userId": "synthetic-private"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "body", "reason": "extra_forbidden"}
    ]
    health_api.lookup.assert_not_awaited()


async def test_metric_http_checks_result_owner_before_conversion(
    client, health_api, monkeypatch
):
    health_api.lookup.side_effect = None
    health_api.lookup.return_value = result_for("steps").model_copy(
        update={"user_id": uuid4()}
    )
    conversion = Mock(side_effect=AssertionError("must not serialize another user"))
    monkeypatch.setattr(router, "metric_response", conversion)
    response = await client.get(URL, headers=health_api.headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HEALTH_DATA_FORBIDDEN"
    conversion.assert_not_called()


@pytest.mark.parametrize(
    "failure,status,code,reason",
    [
        (HealthDataForbiddenError(("steps",)), 403, "HEALTH_DATA_FORBIDDEN", None),
        (HealthDataNotLinkedError(), 409, "CANDIY_USER_NOT_LINKED", None),
        (
            HealthDataNotReadyError(("steps",)),
            503,
            "HEALTH_DATA_UNAVAILABLE",
            "DATA_NOT_READY",
        ),
        (
            HealthDataLookupError(("steps",)),
            503,
            "HEALTH_DATA_UNAVAILABLE",
            "LOOKUP_FAILED",
        ),
        (RuntimeError("synthetic-private"), 500, "INTERNAL_ERROR", None),
    ],
)
async def test_metric_http_maps_only_known_failures(
    client, health_api, failure, status, code, reason
):
    health_api.lookup.side_effect = failure
    response = await client.get(URL, headers=health_api.headers)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error.get("context") == (
        {"reason": reason, "metrics": ["steps"]} if reason else None
    )
    assert "synthetic-private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


async def test_metric_http_invalid_result_is_lookup_failure_not_input_error(
    client, health_api
):
    health_api.lookup.side_effect = None
    health_api.lookup.return_value = result_for("steps").model_copy(
        update={
            "user_id": health_api.owner,
            "summary": result_for("steps").summary.model_copy(
                update={"days_with_data": -1}
            ),
        }
    )
    response = await client.get(URL, headers=health_api.headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": ["steps"],
    }


async def test_metric_http_serialization_failure_is_sanitized(
    client, health_api, monkeypatch
):
    monkeypatch.setattr(
        router,
        "metric_response",
        Mock(side_effect=PydanticSerializationError("synthetic-private")),
    )
    response = await client.get(URL, headers=health_api.headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"]["reason"] == "LOOKUP_FAILED"
    assert "synthetic-private" not in response.text


def test_metric_openapi_declares_public_contract(app):
    document = app.openapi()
    operation = document["paths"]["/api/v1/health/metrics/{metric}"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"metric", "start", "end"}
    assert parameters["metric"]["schema"]["enum"] == [
        "steps",
        "heart_rates",
        "oxygen_saturations",
        "sleeps",
        "exercises",
        "weights",
        "body_fats",
        "basal_metabolic_rates",
        "heights",
    ]
    assert all(parameters[name]["required"] for name in parameters)
    assert parameters["start"]["schema"]["format"] == "date"
    assert operation["security"] == [{"HTTPBearer": []}, {"APIKeyCookie": []}]
    session = document["components"]["schemas"]["ExerciseSessionResponse"]
    assert {"recordId", "countsAsExercise", "avgSpeedMps"} <= set(session["required"])
    assert session["properties"]["countsAsExercise"]["type"] == "boolean"
    assert (
        "userId"
        not in document["components"]["schemas"]["MetricResponse"]["properties"]
    )
