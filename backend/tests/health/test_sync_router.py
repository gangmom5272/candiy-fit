from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.auth import tokens
from app.health.errors import HealthDataNotLinkedError
from app.health.sync_repository import MemberRemovedError
from app.health.sync_service import HealthSyncError
from tests.health.test_calculations import NOW
from tests.health.test_router import health_api as health_api

URL = "/api/v1/health/sync"


@pytest.fixture
def sync_api(app, health_api):
    runner = Mock(sync=AsyncMock(return_value="SYNCED"))
    app.state.member_sync = runner
    return health_api, runner


@pytest.mark.parametrize(
    "status", ["SYNCED", "SKIPPED_RECENT", "BUSY", "SYNTHETIC", "DISABLED"]
)
async def test_sync_uses_authenticated_owner_and_closes_member_session(
    client, sync_api, status
):
    auth, runner = sync_api

    async def sync(owner, request_id):
        assert auth.events[-1] == "member_closed"
        assert owner == auth.owner
        assert request_id
        return status

    runner.sync.side_effect = sync
    response = await client.post(
        URL, headers=auth.headers | {"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"status": status}}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["x-request-id"]
    assert str(auth.owner) not in response.text
    runner.sync.assert_awaited_once()


@pytest.mark.parametrize("case", ["missing", "invalid", "expired", "removed"])
async def test_sync_authentication_before_side_effect(
    client, sync_api, monkeypatch, case
):
    auth, runner = sync_api
    headers = auth.headers
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"Authorization": "Bearer synthetic-private"}
    elif case == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    else:
        auth.db.get.return_value = None
    response = await client.post(URL, headers=headers)
    assert response.status_code == 401
    runner.sync.assert_not_awaited()


@pytest.mark.parametrize(
    "options",
    [
        {"json": {}},
        {"json": {"userId": "another-member"}},
        {"content": "null"},
        {"params": {"userId": "another-member"}},
        {"params": {"force": "true"}},
    ],
)
async def test_sync_rejects_body_and_query(client, sync_api, options):
    auth, runner = sync_api
    response = await client.post(URL, headers=auth.headers, **options)
    assert response.status_code == 422
    runner.sync.assert_not_awaited()


@pytest.mark.parametrize(
    "reason",
    [
        "CONFIGURATION_ERROR",
        "CANDIY_AUTH_FAILED",
        "METRIC_FAILED",
        "AGGREGATE_FAILED",
        "STORAGE_UNAVAILABLE",
        "TIMEOUT",
        "CAPACITY_EXCEEDED",
        "SHUTTING_DOWN",
    ],
)
async def test_sync_errors_use_safe_contract(client, sync_api, reason):
    auth, runner = sync_api
    runner.sync.side_effect = HealthSyncError(reason, ("steps", "private-error"))
    response = await client.post(URL, headers=auth.headers)
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "HEALTH_SYNC_UNAVAILABLE",
            "message": "건강 기록을 갱신하지 못했어요. 잠시 후 다시 확인해 주세요.",
            "context": {"reason": reason, "metrics": ["steps"]},
        }
    }
    assert response.headers["cache-control"] == "no-store"


async def test_removed_member_during_worker_returns_user_auth_error(client, sync_api):
    auth, runner = sync_api
    runner.sync.side_effect = MemberRemovedError()
    response = await client.post(URL, headers=auth.headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_all_404_uses_existing_not_linked_response(client, sync_api):
    auth, runner = sync_api
    runner.sync.side_effect = HealthDataNotLinkedError()
    response = await client.post(URL, headers=auth.headers)
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "CANDIY_USER_NOT_LINKED",
            "message": "앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.",
        }
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert "http_404" not in response.text


@pytest.mark.parametrize("bad_result", [False, "PRIVATE_UNKNOWN_STATUS"])
async def test_sync_does_not_serialize_invalid_result(client, sync_api, bad_result):
    auth, runner = sync_api
    runner.sync.return_value = bad_result
    response = await client.post(URL, headers=auth.headers)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_unexpected_error_is_safe_and_not_reclassified(client, sync_api, caplog):
    auth, runner = sync_api
    runner.sync.side_effect = RuntimeError("private-health-body")
    response = await client.post(URL, headers=auth.headers)
    assert response.status_code == 500
    assert "private-health-body" not in response.text + caplog.text


async def test_app_owned_runner_defaults_to_disabled_without_lifespan(
    client, app, health_api
):
    # 실제 앱 팩토리의 기본 runner를 사용한다. DB·CANDiY 자격이 없어도 생략한다.
    response = await client.post(URL, headers=health_api.headers)
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "DISABLED"}}


async def test_sync_is_documented_as_post_without_input(client):
    response = await client.get("/openapi.json")
    operation = response.json()["paths"][URL]["post"]
    assert "requestBody" not in operation
    assert not operation.get("parameters")
    assert operation["security"]
