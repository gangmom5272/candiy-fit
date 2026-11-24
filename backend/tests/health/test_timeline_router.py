from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic_core import PydanticSerializationError

from app.auth import tokens
from app.health import router, service
from app.health.calculations import build_timeline
from tests.health.test_calculations import NOW
from tests.health.test_router import health_api as health_api
from tests.health.test_timeline_contract import TODAY

URL = "/api/v1/health/timeline"
QUERY = "?start=2026-10-26&end=2026-10-26"


@pytest.fixture
def timeline_api(health_api, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock)

    async def read(owner, start, end, *, today, session_factory):
        assert health_api.events[-1] == "member_closed"
        return build_timeline(
            owner, start, end, today=today, steps={}, hearts={}, sleeps={}, exercises={}
        )

    lookup = AsyncMock(side_effect=read)
    monkeypatch.setattr(service, "get_timeline", lookup)
    return health_api, lookup


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?start=2026-10-26",
        "?end=2026-10-26",
        "?start=null&end=2026-10-26",
        "?start=2026-02-30&end=2026-10-26",
        "?start=2026-10-26&end=2026-10-25",
        "?start=2026-09-25&end=2026-10-26",
        "?start=2026-10-26&end=2026-10-27",
        QUERY + "&userId=synthetic-private",
        QUERY + "&start=2026-10-26",
        "?start=synthetic-private&end=2026-10-26",
    ],
)
async def test_invalid_requests_never_reach_timeline_service(
    client, timeline_api, query
):
    auth, lookup = timeline_api
    response = await client.get(URL + query, headers=auth.headers)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "synthetic-private" not in response.text
    lookup.assert_not_awaited()


async def test_get_body_is_rejected(client, timeline_api):
    auth, lookup = timeline_api
    response = await client.request("GET", URL + QUERY, headers=auth.headers, json={})
    assert response.status_code == 422
    lookup.assert_not_awaited()


@pytest.mark.parametrize("case", ["missing", "invalid", "expired", "deleted"])
async def test_auth_failure_does_not_read_health_data(
    client, timeline_api, monkeypatch, case
):
    auth, lookup = timeline_api
    headers = auth.headers
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"Authorization": "Bearer synthetic-invalid"}
    elif case == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(days=1))
    else:
        auth.db.get.return_value = None
    response = await client.get(URL + QUERY, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "AUTH_EXPIRED" if case == "expired" else "AUTH_REQUIRED"
    )
    lookup.assert_not_awaited()


async def test_foreign_result_is_rejected_before_conversion(
    client, timeline_api, monkeypatch
):
    auth, lookup = timeline_api
    lookup.side_effect = None
    lookup.return_value = build_timeline(
        uuid4(), TODAY, TODAY, today=TODAY, steps={}, hearts={}, sleeps={}, exercises={}
    )
    convert = Mock(side_effect=AssertionError("foreign data"))
    monkeypatch.setattr(router, "timeline_response", convert)
    response = await client.get(URL + QUERY, headers=auth.headers)
    assert response.status_code == 403
    convert.assert_not_called()


async def test_serialization_failure_is_safe_lookup_error(
    client, timeline_api, monkeypatch
):
    auth, _ = timeline_api
    monkeypatch.setattr(
        router,
        "timeline_response",
        Mock(side_effect=PydanticSerializationError("synthetic-private")),
    )
    response = await client.get(URL + QUERY, headers=auth.headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": list(service.TIMELINE_METRICS),
    }
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize(
    "instant,expected",
    [
        (datetime(2026, 10, 25, 14, 59, 59, tzinfo=UTC), "2026-10-25"),
        (datetime(2026, 10, 25, 15, tzinfo=UTC), "2026-10-26"),
    ],
)
async def test_kst_today_is_read_once_for_validation_and_progress(
    client, timeline_api, monkeypatch, instant, expected
):
    auth, lookup = timeline_api
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            assert len(calls) == 1
            return instant.astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock)
    response = await client.get(
        URL + f"?start={expected}&end={expected}", headers=auth.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["days"][0]["isInProgress"] is True
    assert lookup.call_args.kwargs["today"].isoformat() == expected
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["x-request-id"]
    )


async def test_timeline_openapi_has_required_period_and_nested_camel_case(client):
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][URL]["get"]
    assert operation["tags"] == ["health"]
    assert operation["security"] == [{"HTTPBearer": []}, {"APIKeyCookie": []}]
    assert {
        p["name"]
        for p in operation["parameters"]
        if p["in"] == "query" and p["required"]
    } == {"start", "end"}
    models = schema["components"]["schemas"]
    day = models["TimelineDayResponse"]
    assert set(day["required"]) == {
        "date",
        "isInProgress",
        "hourlySteps",
        "hourlyHeartRates",
        "mainSleep",
        "sleepSegments",
        "exerciseSegments",
    }
    assert set(models["ExerciseSegmentResponse"]["properties"]) == {
        "startAt",
        "endAt",
        "exerciseType",
        "typeName",
        "title",
    }
