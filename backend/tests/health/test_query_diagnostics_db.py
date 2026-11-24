import io
import json

import pytest
from sqlalchemy import text

from app.health import repository
from app.health.diagnostics import health_logging
from tests.health.test_router_db import metric_api_db as metric_api_db

pytestmark = pytest.mark.db


@pytest.mark.parametrize("route", ["metric", "timeline"])
@pytest.mark.parametrize("boundary", ["list_sync_states", "has_health_history"])
async def test_real_db_timeout_is_correlated_and_next_request_recovers(
    client, metric_api_db, monkeypatch, route, boundary
):
    _, _, headers = metric_api_db
    path = "metrics/steps" if route == "metric" else "timeline"
    url = f"/api/v1/health/{path}?start=2026-10-22&end=2026-10-22"

    async def timeout(db, owner):
        await db.execute(text("SET LOCAL statement_timeout = '10ms'"))
        await db.execute(text("SELECT pg_sleep(0.1)"))

    stream = io.StringIO()
    with health_logging(stream):
        with monkeypatch.context() as patch:
            patch.setattr(repository, boundary, timeout)
            response = await client.get(url, headers=headers[0])
        recovered = await client.get(url, headers=headers[0])
    assert response.status_code == 503
    assert response.json()["error"]["context"]["reason"] == "LOOKUP_FAILED"
    assert recovered.status_code == 503
    assert recovered.json()["error"]["context"]["reason"] == "DATA_NOT_READY"
    event = json.loads(stream.getvalue())
    assert event["event"] == "health_query_failed"
    assert event["request_id"] == response.headers["X-Request-Id"]
    assert event["request_id"] != recovered.headers["X-Request-Id"]
    assert event["cause"] == "timeout" and event["route"] == route
    assert event["stage"] == "lookup"
    assert "pg_sleep" not in stream.getvalue()
