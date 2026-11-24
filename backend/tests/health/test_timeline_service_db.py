from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text, update

from app.health import repository, service
from app.health.errors import HealthDataNotLinkedError
from app.health.models import ActivityDaily, SyncState
from app.health.schemas import KST
from tests.health.test_timeline_contract import TODAY

pytestmark = pytest.mark.db


async def seed_ready(factory, owner):
    now = datetime.combine(TODAY, datetime.min.time(), tzinfo=KST)
    async with factory.begin() as db:
        for metric in ("steps", "heart_rates", "sleeps", "exercise_sessions"):
            db.add(
                SyncState(
                    end_user_id=owner,
                    metric=metric,
                    initial_sync_done=True,
                    last_success_at=now,
                    last_status="success",
                )
            )


async def test_timeline_uses_one_readonly_snapshot_and_leaves_pool_defaults(
    health_db, monkeypatch
):
    factory, (owner, _) = health_db
    await seed_ready(factory, owner)
    async with factory.begin() as db:
        db.add(ActivityDaily(end_user_id=owner, date=TODAY, hourly_steps=[1] * 24))
    original = repository.list_sync_states

    async def write_after_snapshot(db, user):
        states = await original(db, user)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(ActivityDaily)
                .where(ActivityDaily.end_user_id == owner)
                .values(hourly_steps=[2] * 24)
            )
            await writer.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(last_status="fail", last_error="http_404")
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", write_after_snapshot)
    result = await service.get_timeline(
        owner, TODAY, TODAY, today=TODAY, session_factory=factory
    )
    assert result.days[0].hourly_steps == [1] * 24
    monkeypatch.setattr(repository, "list_sync_states", original)
    with pytest.raises(HealthDataNotLinkedError):
        await service.get_timeline(
            owner, TODAY, TODAY, today=TODAY, session_factory=factory
        )
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_isolation")) == "read committed"
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"


@pytest.mark.parametrize("length", [1, 31])
async def test_period_has_constant_five_selects_and_no_raw_queries(health_db, length):
    factory, (owner, _) = health_db
    await seed_ready(factory, owner)
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = await service.get_timeline(
            owner,
            TODAY - timedelta(days=length - 1),
            TODAY,
            today=TODAY,
            session_factory=factory,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(result.days) == length
    selects = [sql for sql in statements if sql.startswith("SELECT")]
    assert len(selects) == 5
    assert not any("raw_health_record" in sql or "payload" in sql for sql in selects)
