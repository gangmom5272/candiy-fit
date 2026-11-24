import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.schemas import DataResponse
from app.health.rest_schemas import FirstRecordResponse, data_status_response
from app.health.schemas import HealthDataStatusResult, MetricReadiness
from app.users.schemas import AppConnection
from tests.health.test_schemas import USER

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone(timedelta(hours=9)))
METRICS = ("steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises")


def readiness(metric="steps", **changes):
    return MetricReadiness(
        **(
            dict(
                metric=metric,
                ready=True,
                last_sync_status="fail",
                last_data_at=NOW,
                last_success_at=NOW + timedelta(minutes=5),
            )
            | changes
        )
    )


@pytest.mark.parametrize("confirmed", [True, False])
def test_status_response_aliases_nulls_and_utc_without_internal_fields(confirmed):
    result = HealthDataStatusResult(
        user_id=USER,
        app_connection=AppConnection(status="CONNECTED", connected_at=NOW),
        first_arrived_at=NOW if confirmed else None,
        metrics=[readiness(metric) for metric in METRICS],
        checked_at=NOW + timedelta(minutes=10),
    )
    response = data_status_response(result)
    payload = json.loads(DataResponse(data=response).model_dump_json())["data"]
    assert set(payload) == {"appConnection", "firstRecord", "metrics", "checkedAt"}
    assert payload["appConnection"] == {
        "status": "CONNECTED",
        "connectedAt": "2026-10-26T03:00:00Z",
    }
    assert payload["firstRecord"] == {
        "status": "CONFIRMED" if confirmed else "UNCONFIRMED",
        "firstArrivedAt": "2026-10-26T03:00:00Z" if confirmed else None,
    }
    assert [item["metric"] for item in payload["metrics"]] == list(METRICS)
    assert payload["metrics"][0] == {
        "metric": "steps",
        "ready": True,
        "lastSyncStatus": "fail",
        "lastDataAt": "2026-10-26T03:00:00Z",
        "lastSuccessAt": "2026-10-26T03:05:00Z",
    }
    assert payload["checkedAt"] == "2026-10-26T03:10:00Z"
    assert result.checked_at.tzinfo == UTC


@pytest.mark.parametrize("ready", [True, False, None])
def test_readiness_does_not_infer_records_or_success(ready):
    item = readiness(
        ready=ready, last_sync_status=None, last_data_at=None, last_success_at=None
    )
    assert item.ready is ready
    assert item.last_data_at is item.last_success_at is item.last_sync_status is None


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "exercise_sessions"},
        {"ready": "false"},
        {"last_sync_status": "pending"},
        {"last_data_at": NOW.replace(tzinfo=None)},
    ],
)
def test_invalid_stored_status_is_not_coerced_to_public_success(changes):
    with pytest.raises(ValidationError):
        readiness(**changes)


def test_first_record_has_no_placeholder_defaults():
    with pytest.raises(ValidationError):
        FirstRecordResponse()
    with pytest.raises(ValidationError):
        FirstRecordResponse(status="UNCONFIRMED")


@pytest.mark.parametrize("use_alias", [False, True])
def test_unconfirmed_record_rejects_an_arrival_time(use_alias):
    field = "firstArrivedAt" if use_alias else "first_arrived_at"
    with pytest.raises(ValidationError, match="UNCONFIRMED requires no arrival time"):
        FirstRecordResponse.model_validate({"status": "UNCONFIRMED", field: NOW})


def test_confirmed_record_requires_arrival_time():
    with pytest.raises(ValidationError, match="CONFIRMED requires an arrival time"):
        FirstRecordResponse(status="CONFIRMED", first_arrived_at=None)
