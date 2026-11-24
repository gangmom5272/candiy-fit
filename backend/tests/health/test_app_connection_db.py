import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.auth import web_sessions
from app.auth.models import AuthSession
from app.auth.service import create_login_session
from app.auth.web import SESSION_COOKIE
from app.users import service
from app.users.models import User
from tests.health.test_calculations import NOW
from tests.health.test_router_db import metric_api_db as metric_api_db

pytestmark = pytest.mark.db


async def test_new_member_and_concurrent_reports_preserve_first_completion(
    metric_api_db,
):
    factory, (owner, other), _ = metric_api_db
    async with factory() as db:
        initial = await service.get_app_connection(db, owner)
    assert initial.status == "NOT_CONNECTED" and initial.connected_at is None

    async def report():
        async with factory() as db:
            return await service.complete_app_connection(db, owner)

    results = await asyncio.gather(*(report() for _ in range(8)))
    assert all(item == results[0] for item in results)
    assert results[0].status == "CONNECTED" and results[0].connected_at is not None
    async with factory() as db:
        saved = await db.get(User, owner)
        updated_at = saved.updated_at
        assert (await service.get_app_connection(db, other)).status == "NOT_CONNECTED"
    assert await report() == results[0]
    async with factory() as db:
        assert (await db.get(User, owner)).updated_at == updated_at
    async with factory() as db:
        with pytest.raises(service.UserNotFoundError):
            await service.complete_app_connection(db, uuid4())


async def test_put_then_get_with_real_bearer_preserves_other_member(
    client, metric_api_db
):
    factory, (_, other), headers = metric_api_db
    url = "/api/v1/health/app-connection"
    before = await client.get("/api/v1/health/data-status", headers=headers[0])
    assert before.json()["data"]["appConnection"] == {
        "status": "NOT_CONNECTED",
        "connectedAt": None,
    }
    first = await client.put(url, headers=headers[0])
    second = await client.put(url, headers=headers[0])
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    after = await client.get("/api/v1/health/data-status", headers=headers[0])
    data = after.json()["data"]
    assert data["appConnection"] == first.json()["data"]
    assert data["firstRecord"]["status"] == "UNCONFIRMED"
    assert all(metric["ready"] is None for metric in data["metrics"])
    async with factory() as db:
        assert (await service.get_app_connection(db, other)).status == "NOT_CONNECTED"


async def test_put_deleted_member_cannot_recreate_member(client, metric_api_db):
    factory, (owner, _), headers = metric_api_db
    async with factory.begin() as db:
        await db.execute(delete(User).where(User.id == owner))
    response = await client.put("/api/v1/health/app-connection", headers=headers[0])
    assert response.status_code == 401
    async with factory() as db:
        assert await db.get(User, owner) is None


async def test_web_cookie_reads_completion_but_cannot_report(
    client, metric_api_db, settings, monkeypatch
):
    factory, (owner, _), headers = metric_api_db
    response = await client.put("/api/v1/health/app-connection", headers=headers[0])
    assert response.status_code == 200
    async with factory.begin() as db:
        issued = await create_login_session(db, owner, "WEB", settings, now=NOW)
    monkeypatch.setattr(web_sessions, "_utc_now", lambda: NOW)
    try:
        client.cookies.set(SESSION_COOKIE, issued.token)
        response = await client.get("/api/v1/health/data-status")
        assert response.status_code == 200
        assert response.json()["data"]["appConnection"]["status"] == "CONNECTED"
        assert (await client.put("/api/v1/health/app-connection")).status_code == 401
    finally:
        client.cookies.clear()
        async with factory.begin() as db:
            await db.execute(
                delete(AuthSession).where(AuthSession.id == issued.session_id)
            )


@pytest.mark.parametrize(
    "status,with_time",
    [
        ("CONNECTED", False),
        ("UNKNOWN", True),
        ("NOT_CONNECTED", True),
        ("INVALID", False),
    ],
)
async def test_database_rejects_inconsistent_setup_state(
    metric_api_db, status, with_time
):
    factory, (owner, _), _ = metric_api_db
    with pytest.raises(IntegrityError):
        async with factory.begin() as db:
            await db.execute(
                update(User)
                .where(User.id == owner)
                .values(
                    app_connection_status=status,
                    app_connected_at=text("now()") if with_time else None,
                )
            )
    async with factory() as db:
        assert (await service.get_app_connection(db, owner)).status == "NOT_CONNECTED"


async def test_migration_preserves_existing_members_as_unknown(metric_api_db):
    factory, owners, _ = metric_api_db
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    # fixture가 전용 candiy_health_test DB 이름을 검사한 뒤에만 실행한다.
    config.cmd_opts = SimpleNamespace(
        x=["url=" + os.environ["HEALTH_TEST_DATABASE_URL"]]
    )
    async with factory() as db:
        before = (
            await db.execute(
                select(User.id, User.display_name, User.created_at).where(
                    User.id.in_(owners)
                )
            )
        ).all()
    try:
        await asyncio.to_thread(command.downgrade, config, "77fd2d34a342")
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    async with factory() as db:
        after = (
            await db.execute(
                select(User.id, User.display_name, User.created_at).where(
                    User.id.in_(owners)
                )
            )
        ).all()
        assert sorted(before) == sorted(after)
        for owner in owners:
            state = await service.get_app_connection(db, owner)
            assert state.status == "UNKNOWN" and state.connected_at is None
    async with factory() as db:
        assert (
            await service.complete_app_connection(db, owners[0])
        ).status == "CONNECTED"
    await asyncio.to_thread(command.check, config)
