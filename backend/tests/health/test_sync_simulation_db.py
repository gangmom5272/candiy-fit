"""실제 CANDiY는 호출하지 않는다. loopback Postman simulation과 전용 DB가 필요하다."""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url

from app.auth.tokens import issue_access_token
from app.core.config import Settings
from app.health import sync_service
from app.health.models import (
    ActivityDaily,
    LifestyleScore,
    RawHealthRecord,
    StepSnapshot,
    SyncState,
)
from app.health.schemas import KST
from app.health.sync_service import MemberSyncRunner
from app.users.models import User

pytestmark = pytest.mark.db
URL = "/api/v1/health/sync"


@pytest.fixture
async def sync_simulation(health_db, app, settings):
    base = os.environ.get("CANDIY_SYNC_TEST_BASE_URL", "")
    if not base:
        pytest.skip(
            "CANDIY_SYNC_TEST_BASE_URL에 loopback Postman simulation을 지정하세요"
        )
    if urlsplit(base).hostname not in ("127.0.0.1", "localhost"):
        pytest.fail("실제 CANDiY 대신 loopback simulation만 허용합니다")
    factory, owners = health_db
    url = make_url(os.environ["HEALTH_TEST_DATABASE_URL"])
    configured = Settings(
        _env_file=None,
        **(
            settings.model_dump()
            | {
                "postgres_host": url.host,
                "postgres_port": url.port,
                "postgres_db": url.database,
                "postgres_user": url.username,
                "postgres_password": url.password,
                "pipeline_enabled": True,
                "pipeline_include_members": True,
                "candiy_base_url": base,
                "candiy_client_id": "synthetic-client",
                "candiy_client_secret": "synthetic-secret",
            }
        ),
    )
    day = (datetime.now(KST) - timedelta(days=1)).date()
    app.state.sessionmaker = factory
    runner = MemberSyncRunner(configured)
    app.state.member_sync = runner
    async with factory.begin() as db:
        db.add(
            User(
                id=owners[0],
                social_type="GOOGLE",
                social_user_id=f"sync223-{owners[0]}",
                display_name="합성회원",
                birth_year=2000,
                gender="FEMALE",
                height_cm=165,
                weight_kg=55,
            )
        )
    headers = {
        "Authorization": f"Bearer {issue_access_token(owners[0], configured, now=datetime.now(UTC)).token}"
    }
    async with httpx.AsyncClient(base_url=base, trust_env=False) as control:

        async def scenario(mode="default", **extra):
            result = await control.post(
                base + "/__control",
                json={"mode": mode, "owner": str(owners[0]), "day": str(day), **extra},
            )
            result.raise_for_status()

        try:
            await scenario()
            yield factory, owners[0], day, headers, runner, control, scenario
        finally:
            await runner.aclose()
            async with factory.begin() as db:
                await db.execute(
                    delete(StepSnapshot).where(StepSnapshot.end_user_id == owners[0])
                )
                await db.execute(delete(User).where(User.id == owners[0]))


async def test_real_http_post_persists_aggregates_scores_and_gets(
    client, sync_simulation, caplog
):
    factory, owner, day, headers, runner, control, _ = sync_simulation
    response = await client.post(URL, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "SYNCED"}}
    async with factory() as db:
        raw = await db.scalar(
            select(RawHealthRecord).where(RawHealthRecord.end_user_id == owner)
        )
        daily = await db.get(ActivityDaily, (owner, day))
        score = await db.get(LifestyleScore, (owner, day))
        assert raw.metric == "steps" and daily.steps == 8000 and score.steps_score == 60
    metric = await client.get(
        f"/api/v1/health/metrics/steps?start={day}&end={day}", headers=headers
    )
    assert metric.status_code == 200
    assert metric.json()["data"]["summary"]["total"] == 8000
    points = await client.get(
        f"/api/v1/health/lifestyle-score?date={day}", headers=headers
    )
    assert points.status_code == 200
    assert points.json()["data"]["areas"]["activity"]["steps"]["value"] == 8000
    calls = (await control.get(str(control.base_url).rstrip("/") + "/__calls")).json()
    assert {c["owner"] for c in calls if c["route"] != "token"} == {str(owner)}
    assert {c["route"] for c in calls if c["route"] != "token"} == set(
        sync_service.METRICS
    )
    assert not runner._tasks
    assert str(owner) not in caplog.text
    assert (await client.post(URL, headers=headers)).json() == {
        "data": {"status": "SKIPPED_RECENT"}
    }


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("steps-fail", "METRIC_FAILED"),
        ("steps-404", "METRIC_FAILED"),
        ("mixed-404", "METRIC_FAILED"),
        ("auth-fail", "CANDIY_AUTH_FAILED"),
        ("token-invalid-json", "CANDIY_AUTH_FAILED"),
        ("token-missing", "CANDIY_AUTH_FAILED"),
        ("token-bad-expiry", "CANDIY_AUTH_FAILED"),
    ],
)
async def test_real_http_failures_are_not_success(
    client, sync_simulation, mode, reason, caplog
):
    factory, owner, _, headers, runner, control, scenario = sync_simulation
    await scenario(mode)
    response = await client.post(URL, headers=headers)
    assert response.status_code == 503
    assert "synthetic-secret" not in response.text + caplog.text
    context = response.json()["error"]["context"]
    assert context["reason"] == reason
    if mode in ("steps-fail", "steps-404"):
        assert context["metrics"] == ["steps"]
    if mode == "mixed-404":
        assert set(context["metrics"]) == set(sync_service.METRICS)
    if reason != "CANDIY_AUTH_FAILED":
        skipped = await client.post(URL, headers=headers)
        assert skipped.json() == {"data": {"status": "SKIPPED_RECENT"}}
    else:
        calls = await control.get(str(control.base_url).rstrip("/") + "/__calls")
        assert calls.json() == [{"route": "token"}]
        await scenario()
        assert (await client.post(URL, headers=headers)).json() == {
            "data": {"status": "SKIPPED_RECENT"}
        }
        async with factory.begin() as db:
            await db.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(last_attempt_at=datetime.now(UTC) - timedelta(minutes=3))
            )
        assert (await client.post(URL, headers=headers)).json() == {
            "data": {"status": "SYNCED"}
        }
    assert not runner._tasks


async def test_all_13_http_404s_match_existing_get_not_linked_response(
    client, sync_simulation
):
    factory, owner, day, headers, runner, control, scenario = sync_simulation
    await scenario("all-404")
    response = await client.post(URL, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANDIY_USER_NOT_LINKED"
    assert response.headers["cache-control"] == "no-store"
    assert "http_404" not in response.text and str(owner) not in response.text
    metric = await client.get(
        f"/api/v1/health/metrics/steps?start={day}&end={day}", headers=headers
    )
    assert metric.status_code == 409 and metric.json() == response.json()
    async with factory() as db:
        states = (
            await db.scalars(select(SyncState).where(SyncState.end_user_id == owner))
        ).all()
        assert {state.metric for state in states} == set(sync_service.METRICS)
        assert all(state.last_error == "http_404" for state in states)
    calls = (await control.get(str(control.base_url).rstrip("/") + "/__calls")).json()
    assert {call["route"] for call in calls if call["route"] != "token"} == set(
        sync_service.METRICS
    )
    assert (await client.post(URL, headers=headers)).json() == {
        "data": {"status": "SKIPPED_RECENT"}
    }
    assert not runner._tasks


async def test_all_empty_success_responses_are_synced_not_unlinked(
    client, sync_simulation
):
    factory, owner, day, headers, _, _, scenario = sync_simulation
    await scenario("empty")
    response = await client.post(URL, headers=headers)
    assert response.json() == {"data": {"status": "SYNCED"}}
    assert response.status_code == 200
    async with factory() as db:
        states = (
            await db.scalars(select(SyncState).where(SyncState.end_user_id == owner))
        ).all()
        assert len(states) == len(sync_service.METRICS)
        assert all(
            state.last_error is None and state.initial_sync_done for state in states
        )
    metric = await client.get(
        f"/api/v1/health/metrics/steps?start={day}&end={day}", headers=headers
    )
    assert metric.status_code == 200


async def test_real_http_timeout_retains_worker_until_cleanup(
    client, sync_simulation, monkeypatch
):
    factory, owner, _, headers, runner, _, scenario = sync_simulation
    monkeypatch.setattr(sync_service, "RESPONSE_WAIT_SECONDS", 0.03)
    await scenario("slow", delayMs=100)
    response = await client.post(URL, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"]["reason"] == "TIMEOUT"
    assert owner in runner._tasks
    assert (await client.post(URL, headers=headers)).json() == {
        "data": {"status": "BUSY"}
    }
    await runner.aclose()
    assert not runner._tasks
    async with factory() as db:
        assert await db.scalar(
            select(RawHealthRecord).where(RawHealthRecord.end_user_id == owner)
        )


async def test_scheduler_member_lock_returns_busy_without_http_call(
    client, sync_simulation
):
    factory, owner, _, headers, _, control, _ = sync_simulation
    async with factory() as db:
        await db.execute(
            text("SELECT pg_advisory_lock(726002, hashtext(:owner))"),
            {"owner": str(owner)},
        )
        try:
            response = await client.post(URL, headers=headers)
            assert response.json() == {"data": {"status": "BUSY"}}
            calls = await control.get(str(control.base_url).rstrip("/") + "/__calls")
            assert calls.json() == []
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(726002, hashtext(:owner))"),
                {"owner": str(owner)},
            )


async def test_repeated_request_uses_existing_recent_attempt_policy(
    client, sync_simulation
):
    factory, owner, _, headers, _, _, scenario = sync_simulation
    await scenario("steps-fail")
    assert (await client.post(URL, headers=headers)).status_code == 503
    await scenario()
    assert (await client.post(URL, headers=headers)).json()["data"][
        "status"
    ] == "SKIPPED_RECENT"
    async with factory.begin() as db:
        await db.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(last_attempt_at=datetime.now(UTC) - timedelta(minutes=3))
        )
    assert (await client.post(URL, headers=headers)).json()["data"][
        "status"
    ] == "SYNCED"
