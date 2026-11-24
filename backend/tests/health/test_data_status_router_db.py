from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.health import repository
from app.health.models import SyncState
from tests.health.test_calculations import NOW
from tests.health.test_data_status_response import METRICS
from tests.health.test_first_record_db import raw_record
from tests.health.test_router_db import metric_api_db as metric_api_db

pytestmark = pytest.mark.db
URL = "/api/v1/health/data-status"


async def test_real_jwt_and_db_keep_arrival_readiness_and_users_separate(
    client, metric_api_db
):
    factory, (owner, other), headers = metric_api_db
    arrival = NOW - timedelta(hours=1)
    async with factory.begin() as db:
        db.add_all(
            [
                raw_record(owner, "sleeps", arrival),
                raw_record(other, "exercise_sessions", NOW - timedelta(days=10)),
                SyncState(
                    end_user_id=owner,
                    metric="steps",
                    initial_sync_done=True,
                    first_data_at=NOW - timedelta(days=30),
                    last_status="fail",
                    last_success_at=NOW,
                    last_data_at=NOW,
                    last_error="http_503",
                ),
                SyncState(
                    end_user_id=owner,
                    metric="exercise_sessions",
                    initial_sync_done=True,
                    last_success_at=NOW,
                    last_status="success",
                ),
            ]
        )
    first = await client.get(URL, headers=headers[0])
    second = await client.get(URL, headers=headers[1])
    assert first.status_code == second.status_code == 200
    data = first.json()["data"]
    assert data["firstRecord"] == {
        "status": "CONFIRMED",
        "firstArrivedAt": arrival.isoformat().replace("+00:00", "Z"),
    }
    assert [item["metric"] for item in data["metrics"]] == list(METRICS)
    assert [item["ready"] for item in data["metrics"]] == [True, None, None, None, True]
    assert data["metrics"][0]["lastSyncStatus"] == "fail"
    assert data["checkedAt"] == NOW.isoformat().replace("+00:00", "Z")
    other_data = second.json()["data"]
    assert other_data["firstRecord"] == {
        "status": "UNCONFIRMED",
        "firstArrivedAt": None,
    }
    assert all(item["ready"] is None for item in other_data["metrics"])
    assert str(owner) not in first.text and str(other) not in second.text


async def test_http_404_sync_failure_is_status_not_a_status_api_409(
    client, metric_api_db
):
    factory, (owner, _), headers = metric_api_db
    async with factory.begin() as db:
        db.add(
            SyncState(
                end_user_id=owner,
                metric="steps",
                initial_sync_done=False,
                last_status="fail",
                last_error="http_404",
            )
        )
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["firstRecord"] == {"status": "UNCONFIRMED", "firstArrivedAt": None}
    assert data["metrics"][0]["ready"] is False
    assert data["metrics"][0]["lastSyncStatus"] == "fail"


@pytest.mark.parametrize("query", ["find_first_arrival", "list_sync_states"])
async def test_failure_of_either_query_is_whole_http_error(
    client, metric_api_db, monkeypatch, query
):
    _, _, headers = metric_api_db
    monkeypatch.setattr(
        repository, query, AsyncMock(side_effect=OSError("synthetic-private"))
    )
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 503
    assert "data" not in response.json()
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": list(METRICS),
    }
    assert "synthetic-private" not in response.text
