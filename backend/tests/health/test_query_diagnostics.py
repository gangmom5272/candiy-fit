import asyncio
import io
import json
import math

import pytest
from pydantic_core import PydanticSerializationError
from sqlalchemy.exc import OperationalError

from app.health import router
from app.health.diagnostics import health_logging
from app.health.errors import (
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
)
from tests.health.test_router import health_api as health_api
from tests.health.test_timeline_router import timeline_api as timeline_api

CANARY = "SYNTHETIC-SQL-TOKEN-HEALTH-PRIVATE"


@pytest.fixture(params=["metric", "timeline"])
def endpoint(request, health_api, timeline_api):
    if request.param == "metric":
        return (
            "metric",
            "/api/v1/health/metrics/steps?start=2026-10-16&end=2026-10-22",
            health_api.lookup,
        )
    return (
        "timeline",
        "/api/v1/health/timeline?start=2026-10-26&end=2026-10-26",
        timeline_api[1],
    )


@pytest.mark.parametrize("reason", ["timeout", "database", "validation", "state"])
async def test_failure_has_safe_request_correlated_event(
    client, health_api, endpoint, reason
):
    route, url, lookup = endpoint
    error = HealthDataLookupError(("steps",))
    error.__cause__ = {
        "timeout": TimeoutError(CANARY),
        "database": OperationalError(CANARY, {"private": CANARY}, OSError(CANARY)),
        "validation": InvalidHealthData(CANARY),
        "state": None,
    }[reason]
    lookup.side_effect = error
    stream = io.StringIO()
    with health_logging(stream):
        response = await client.get(
            url, headers=health_api.headers | {"X-Request-Id": CANARY}
        )
    assert response.status_code == 503
    assert response.json()["error"]["context"]["reason"] == "LOOKUP_FAILED"
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event == {
        "event": "health_query_failed",
        "request_id": response.headers["X-Request-Id"],
        "route": route,
        "stage": "lookup",
        "cause": reason,
        "seconds": event["seconds"],
    }
    assert math.isfinite(event["seconds"]) and event["seconds"] >= 0
    for private in (
        CANARY,
        str(health_api.owner),
        health_api.headers["Authorization"],
        "2026-10",
    ):
        assert private not in stream.getvalue()
    assert CANARY not in response.text


@pytest.mark.parametrize(
    "error", [None, HealthDataNotReadyError(), HealthDataNotLinkedError()]
)
async def test_success_and_readiness_do_not_emit_failure(
    client, health_api, endpoint, error
):
    _, url, lookup = endpoint
    if error is not None:
        lookup.side_effect = error
    stream = io.StringIO()
    with health_logging(stream):
        response = await client.get(url, headers=health_api.headers)
    assert response.status_code == (
        200
        if error is None
        else 503
        if isinstance(error, HealthDataNotReadyError)
        else 409
    )
    assert stream.getvalue() == ""


async def test_conversion_failure_has_response_stage(
    client, health_api, endpoint, monkeypatch
):
    route, url, _ = endpoint
    name = "metric_response" if route == "metric" else "timeline_response"

    def fail(*args):
        raise PydanticSerializationError(CANARY)

    monkeypatch.setattr(router, name, fail)
    stream = io.StringIO()
    with health_logging(stream):
        response = await client.get(url, headers=health_api.headers)
    assert response.status_code == 503
    event = json.loads(stream.getvalue())
    assert event["stage"] == "response" and event["cause"] == "validation"
    assert event["request_id"] == response.headers["X-Request-Id"]
    assert CANARY not in stream.getvalue()


async def test_concurrent_requests_keep_ids_separate(client, health_api, endpoint):
    _, url, lookup = endpoint

    async def fail(*args, **kwargs):
        await asyncio.sleep(0)
        raise HealthDataLookupError() from TimeoutError(CANARY)

    lookup.side_effect = fail
    stream = io.StringIO()
    with health_logging(stream):
        responses = await asyncio.gather(
            *(client.get(url, headers=health_api.headers) for _ in range(4))
        )
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(events) == 4
    assert {e["request_id"] for e in events} == {
        r.headers["X-Request-Id"] for r in responses
    }


async def test_cancellation_propagates_without_failure_event(
    client, health_api, endpoint
):
    _, url, lookup = endpoint
    lookup.side_effect = asyncio.CancelledError()
    stream = io.StringIO()
    with health_logging(stream), pytest.raises(asyncio.CancelledError):
        await client.get(url, headers=health_api.headers)
    assert stream.getvalue() == ""


async def test_output_failure_preserves_public_error(client, health_api, endpoint):
    _, url, lookup = endpoint
    lookup.side_effect = HealthDataLookupError()

    class BrokenStream(io.StringIO):
        def write(self, value):
            raise OSError(CANARY)

    with health_logging(BrokenStream()):
        response = await client.get(url, headers=health_api.headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"]["reason"] == "LOOKUP_FAILED"


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -1, "private"])
def test_new_event_drops_untrusted_fields(seconds):
    from app.health.diagnostics import emit_health_event

    stream = io.StringIO()
    with health_logging(stream):
        emit_health_event(
            "health_query_failed",
            request_id=CANARY,
            route=CANARY,
            stage=CANARY,
            cause=CANARY,
            seconds=seconds,
            sql=CANARY,
            user_id=CANARY,
            token=CANARY,
        )
    assert json.loads(stream.getvalue()) == {
        "event": "health_query_failed",
        "route": "unknown",
        "stage": "unknown",
        "cause": "unknown",
    }
    assert CANARY not in stream.getvalue()
