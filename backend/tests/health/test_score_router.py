from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic_core import PydanticSerializationError

from app.auth import tokens
from app.health import router, service
from app.health.schemas import LifestyleScoreRecord
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_router import health_api as health_api
from tests.health.test_score_contract import stored_score

URL = "/api/v1/health/lifestyle-score"


@pytest.fixture
def score_api(health_api, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock, raising=False)
    lookup = AsyncMock(
        return_value=LifestyleScoreRecord(
            **stored_score(
                end_user_id=health_api.owner,
                date=TODAY,
            )
        )
    )
    monkeypatch.setattr(service, "get_lifestyle_score", lookup)
    return health_api, lookup


async def test_omitted_date_uses_kst_today(client, app, score_api):
    auth, lookup = score_api
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["date"] == TODAY.isoformat()
    assert response.json()["data"]["isInProgress"] is True
    lookup.assert_awaited_once_with(
        auth.owner, TODAY, session_factory=app.state.sessionmaker
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [
        "?date=",
        "?date=null",
        "?date=20260229",
        "?date=2026-02-29",
        "?date=2026-10-23",
        "?date=2026-10-22&date=2026-10-22",
        "?start=2026-10-01",
        "?userId=synthetic",
        "?date=private-invalid-input",
    ],
)
async def test_invalid_queries_are_rejected_before_score_read(client, score_api, query):
    auth, lookup = score_api
    response = await client.get(URL + query, headers=auth.headers)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "private-invalid-input" not in response.text
    lookup.assert_not_awaited()


async def test_get_body_is_rejected(client, score_api):
    auth, lookup = score_api
    response = await client.request("GET", URL, headers=auth.headers, json={})
    assert response.status_code == 422
    lookup.assert_not_awaited()


@pytest.mark.parametrize("case", ["missing", "invalid", "expired", "deleted"])
async def test_auth_failures_do_not_read_scores(client, score_api, monkeypatch, case):
    auth, lookup = score_api
    headers = auth.headers
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"Authorization": "Bearer synthetic-invalid"}
    elif case == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(days=1))
    else:
        auth.db.get.return_value = None
    response = await client.get(URL, headers=headers)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == (
        "AUTH_EXPIRED" if case == "expired" else "AUTH_REQUIRED"
    )
    lookup.assert_not_awaited()


async def test_foreign_result_is_rejected_before_conversion(
    client, score_api, monkeypatch
):
    auth, lookup = score_api
    lookup.return_value = lookup.return_value.model_copy(
        update={"end_user_id": uuid4()}
    )
    convert = Mock(side_effect=AssertionError("must not serialize another member"))
    monkeypatch.setattr(router, "lifestyle_score_response", convert, raising=False)
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == 403, response.text
    convert.assert_not_called()


async def test_serialization_failure_has_safe_lookup_error(
    client, score_api, monkeypatch
):
    auth, _ = score_api
    monkeypatch.setattr(
        router,
        "lifestyle_score_response",
        Mock(
            side_effect=PydanticSerializationError("synthetic-private"),
        ),
        raising=False,
    )
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": [],
    }
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize(
    "instant,expected",
    [
        (datetime(2026, 10, 25, 14, 59, 59, tzinfo=UTC), "2026-10-25"),
        (datetime(2026, 10, 25, 15, tzinfo=UTC), "2026-10-26"),
    ],
)
async def test_kst_boundary_uses_one_clock_read(
    client, score_api, monkeypatch, instant, expected
):
    auth, lookup = score_api
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            assert len(calls) == 1
            return instant.astimezone(tz)

    async def read(owner, score_date, **kwargs):
        return LifestyleScoreRecord(**stored_score(end_user_id=owner, date=score_date))

    lookup.side_effect = read
    monkeypatch.setattr(router, "datetime", Clock, raising=False)
    response = await client.get(URL, headers=auth.headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["date"] == expected
    assert response.json()["data"]["isInProgress"] is True


def test_score_openapi_has_optional_date_and_public_response(app):
    spec = app.openapi()
    operation = spec["paths"][URL]["get"]
    assert [(p["name"], p["required"]) for p in operation["parameters"]] == [
        ("date", False)
    ]
    schema = spec["components"]["schemas"]["LifestyleScoreResponse"]
    assert set(schema["properties"]) == {
        "date",
        "isInProgress",
        "total",
        "areas",
        "computedAt",
        "reason",
    }
    for field in ("reason", "computedAt"):
        assert field in schema["required"]
        assert {"type": "null"} in schema["properties"][field]["anyOf"]
    heart = spec["components"]["schemas"]["ScoreHeartResponse"]
    for field in ("baselineNights", "checkWear"):
        assert field in heart["required"]
        assert {"type": "null"} in heart["properties"][field]["anyOf"]
