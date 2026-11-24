import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.health import daily_message_service, router
from app.health.daily_message import build_daily_message
from app.health.daily_message_read import ReadCleanupError
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)
from app.health.schemas import KST
from tests.health.test_daily_message_rules import message_input
from tests.health.test_router import health_api as health_api

URL = "/api/v1/health/daily-message"


@pytest.fixture
def message_api(health_api, app, monkeypatch):
    clock = [datetime(2026, 10, 29, 23, 59, 59, tzinfo=KST)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock)
    engine = Mock()
    app.state.engine = engine

    async def result(owner, *, today, request_id, engine):
        assert health_api.events[-1] == "member_closed"
        assert request_id
        return build_daily_message(message_input()).model_copy(
            update={"user_id": owner, "date": today}
        )

    lookup = AsyncMock(side_effect=result)
    monkeypatch.setattr(daily_message_service, "get_daily_message", lookup)
    return health_api, lookup, clock


async def test_http_result_has_contract_keys_and_safe_headers(client, message_api):
    auth, lookup, _ = message_api
    response = await client.get(
        URL, headers=auth.headers | {"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {
        "date",
        "status",
        "code",
        "area",
        "title",
        "description",
        "dataAsOf",
        "scoreComputedAt",
        "notices",
    }
    assert set(data["dataAsOf"]) == {"steps", "exercises", "sleeps"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == lookup.call_args.kwargs["request_id"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert str(auth.owner) not in response.text


@pytest.mark.parametrize(
    "query", ["?date=2026-10-29", "?userId=synthetic-private", "?mode=new&mode=new"]
)
async def test_unknown_queries_do_not_reach_service(client, message_api, query):
    auth, lookup, _ = message_api
    response = await client.get(URL + query, headers=auth.headers)
    assert response.status_code == 422 and "synthetic-private" not in response.text
    lookup.assert_not_awaited()


async def test_body_and_missing_auth_are_rejected(client, message_api):
    auth, lookup, _ = message_api
    assert (
        await client.request("GET", URL, headers=auth.headers, json={})
    ).status_code == 422
    response = await client.get(URL)
    assert (
        response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    )
    lookup.assert_not_awaited()


@pytest.mark.parametrize(
    "error,status,code",
    [
        (HealthDataForbiddenError(), 403, "HEALTH_DATA_FORBIDDEN"),
        (HealthDataNotLinkedError(), 409, "CANDIY_USER_NOT_LINKED"),
        (HealthDataLookupError(()), 503, "HEALTH_DATA_UNAVAILABLE"),
        (HealthDataNotReadyError(("sleeps",)), 503, "HEALTH_DATA_UNAVAILABLE"),
        (ReadCleanupError(), 500, "INTERNAL_ERROR"),
        (RuntimeError("synthetic-private"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_errors_keep_whole_scope_and_no_store(
    client, message_api, error, status, code
):
    auth, lookup, _ = message_api
    lookup.side_effect = error
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic-private" not in response.text


async def test_date_is_captured_before_member_auth_wait_and_new_request_refreshes(
    client, message_api
):
    auth, lookup, clock = message_api
    first_day = clock[0].date()

    async def member(*args):
        clock[0] += timedelta(seconds=2)
        return Mock(id=auth.owner)

    auth.db.get.side_effect = member
    first = await client.get(URL, headers=auth.headers)
    assert first.json()["data"]["date"] == first_day.isoformat()
    assert lookup.call_args.kwargs["today"] == first_day
    second = await client.get(URL, headers=auth.headers)
    assert second.json()["data"]["date"] == (first_day + timedelta(days=1)).isoformat()


async def test_empty_body_wait_does_not_move_request_date(client, message_api):
    auth, lookup, clock = message_api
    original = clock[0].date()

    async def content():
        clock[0] += timedelta(seconds=2)
        yield b""

    response = await client.request("GET", URL, headers=auth.headers, content=content())
    assert response.status_code == 200
    assert lookup.call_args.kwargs["today"] == original


async def test_foreign_result_is_blocked_before_conversion(
    client, message_api, monkeypatch
):
    auth, lookup, _ = message_api
    lookup.side_effect = None
    lookup.return_value = build_daily_message(message_input()).model_copy(
        update={"user_id": uuid4()}
    )
    convert = Mock(side_effect=AssertionError("synthetic"))
    monkeypatch.setattr(router, "daily_message_response", convert)
    assert (await client.get(URL, headers=auth.headers)).status_code == 403
    convert.assert_not_called()


async def test_external_cancellation_is_not_an_http_error(client, message_api):
    auth, lookup, _ = message_api
    lookup.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await client.get(URL, headers=auth.headers)


@pytest.mark.parametrize("case", ["invalid", "expired", "deleted"])
async def test_auth_rejection_never_reads_message(
    client, message_api, monkeypatch, case
):
    from app.auth import tokens
    from tests.health.test_calculations import NOW

    auth, lookup, _ = message_api
    headers = auth.headers
    if case == "invalid":
        headers = {"Authorization": "Bearer synthetic-invalid"}
    elif case == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(days=1))
    else:
        auth.db.get.return_value = None
    response = await client.get(URL, headers=headers)
    assert response.status_code == 401
    lookup.assert_not_awaited()


async def test_serialization_fault_uses_safe_lookup_response(
    client, message_api, monkeypatch
):
    from pydantic_core import PydanticSerializationError

    auth, _, _ = message_api
    monkeypatch.setattr(
        router,
        "daily_message_response",
        Mock(side_effect=PydanticSerializationError("synthetic-private")),
    )
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": ["steps", "exercises", "sleeps"],
    }
    assert "synthetic-private" not in response.text
