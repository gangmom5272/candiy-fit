"""290: 실제 전용 DB에서 최초 수집 대기와 상태 유실·조회 실패를 구분한다."""

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from app.auth import tokens
from app.health import daily_message_service, repository, service
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.models import RawHealthRecord, SyncState
from app.users.models import User
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_router_db import metric_api_db as metric_api_db
from tests.health.test_service_db import seed

pytestmark = pytest.mark.db


async def lookup(kind, factory, owner):
    if kind == "activity":
        return await service.get_activity_daily(
            owner, TODAY, TODAY, session_factory=factory
        )
    if kind == "metric":
        return await service.get_metric_result(
            owner, "steps", TODAY, TODAY, session_factory=factory
        )
    if kind == "timeline":
        return await service.get_timeline(
            owner, TODAY, TODAY, today=TODAY, session_factory=factory
        )
    if kind == "score":
        return await service.get_lifestyle_score(owner, TODAY, session_factory=factory)
    return await daily_message_service.get_daily_message(
        owner, today=TODAY, request_id=str(uuid4()), engine=factory.kw["bind"]
    )


@pytest.mark.parametrize("kind", ["metric", "timeline", "score", "message", "activity"])
@pytest.mark.parametrize(
    "scenario", ["new", "orphan", "raw_only", "db_error", "history_db_error"]
)
async def test_initial_state_matrix(health_db, monkeypatch, kind, scenario):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    # 다른 회원의 기록이 최초 수집 판정에 영향을 주어서는 안 된다.
    await seed(factory, other)
    if scenario == "orphan":
        await seed(factory, owner)
        async with factory.begin() as db:
            await db.execute(delete(SyncState).where(SyncState.end_user_id == owner))
    if scenario == "raw_only":
        async with factory.begin() as db:
            db.add(
                RawHealthRecord(
                    end_user_id=owner,
                    metric="steps",
                    data_id="synthetic-290",
                    app="synthetic",
                    start_at=NOW,
                    payload={"steps": 0},
                    last_modified_at=NOW,
                    source_created_at=NOW,
                    source_updated_at=NOW,
                )
            )
    if scenario in ("db_error", "history_db_error"):

        async def unavailable(db, user_id):
            # 실제 PostgreSQL statement timeout. 네트워크 장애를 빈 결과로 흉내 내지 않는다.
            await db.execute(text("SET LOCAL statement_timeout = '10ms'"))
            await db.execute(text("SELECT pg_sleep(0.1)"))

        name = "list_sync_states" if scenario == "db_error" else "has_health_history"
        monkeypatch.setattr(repository, name, unavailable)
    expected = HealthDataNotReadyError if scenario == "new" else HealthDataLookupError
    with pytest.raises(expected):
        await lookup(kind, factory, owner)


@pytest.mark.parametrize(
    "path",
    [
        "metrics/steps?start=2026-10-22&end=2026-10-22",
        "timeline?start=2026-10-22&end=2026-10-22",
        "lifestyle-score?date=2026-10-22",
        "daily-message",
    ],
)
@pytest.mark.parametrize("orphan", [False, True])
async def test_initial_readiness_public_rest(client, app, metric_api_db, path, orphan):
    factory, (owner, other), headers = metric_api_db
    app.state.engine = factory.kw["bind"]
    await seed(factory, other)
    if orphan:
        await seed(factory, owner)
        async with factory.begin() as db:
            await db.execute(delete(SyncState).where(SyncState.end_user_id == owner))
    response = await client.get("/api/v1/health/" + path, headers=headers[0])
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "HEALTH_DATA_UNAVAILABLE"
    assert error["context"]["reason"] == (
        "LOOKUP_FAILED" if orphan else "DATA_NOT_READY"
    )
    assert response.headers["cache-control"] == "no-store"
    assert str(owner) not in response.text and str(other) not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "metrics/steps?start=2026-10-22&end=2026-10-22",
        "timeline?start=2026-10-22&end=2026-10-22",
        "lifestyle-score?date=2026-10-22",
        "daily-message",
    ],
)
@pytest.mark.parametrize(
    "case", ["missing", "expired", "tampered", "deleted", "unknown"]
)
async def test_auth_rejection_precedes_initial_history_lookup(
    client, app, metric_api_db, settings, monkeypatch, path, case
):
    factory, (owner, _), headers = metric_api_db
    app.state.engine = factory.kw["bind"]
    request_headers = headers[0]
    if case == "missing":
        request_headers = {}
    elif case == "expired":
        token = tokens.issue_access_token(
            owner, settings, now=NOW - timedelta(days=1)
        ).token
        request_headers = {"Authorization": f"Bearer {token}"}
    elif case == "tampered":
        token = request_headers["Authorization"].split(" ")[1]
        head, payload, signature = token.split(".")
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        request_headers = {"Authorization": f"Bearer {head}.{payload}.{signature}"}
    elif case == "deleted":
        async with factory.begin() as db:
            await db.execute(delete(User).where(User.id == owner))
    else:
        token = tokens.issue_access_token(uuid4(), settings, now=NOW).token
        request_headers = {"Authorization": f"Bearer {token}"}
    history = AsyncMock(side_effect=AssertionError("unauthorized history lookup"))
    monkeypatch.setattr(repository, "has_health_history", history)
    response = await client.get("/api/v1/health/" + path, headers=request_headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "AUTH_EXPIRED" if case == "expired" else "AUTH_REQUIRED"
    )
    history.assert_not_awaited()
    assert str(owner) not in response.text
    assert request_headers.get("Authorization", "synthetic-canary") not in response.text
