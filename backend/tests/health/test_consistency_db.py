import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import update

from app.health import repository, service
from app.health.models import SleepSession, SyncState
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_service_db import seed

pytestmark = pytest.mark.db


async def test_writer_commit_between_selects_cannot_mix_result_versions(
    health_db, monkeypatch
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    monkeypatch.setattr(service, "_now", lambda: NOW)
    snapshot_read = asyncio.Event()
    writer_committed = asyncio.Event()
    original = repository.list_sync_states

    async def pause_after_state(session, user_id):
        states = await original(session, user_id)
        snapshot_read.set()
        await writer_committed.wait()
        return states

    monkeypatch.setattr(repository, "list_sync_states", pause_after_state)

    async def writer():
        await snapshot_read.wait()
        try:
            async with factory.begin() as session:
                await session.execute(
                    update(SleepSession)
                    .where(
                        SleepSession.end_user_id == owner,
                        SleepSession.date == date(2026, 10, 21),
                        SleepSession.is_main.is_(True),
                    )
                    .values(sleep_minutes=500, rem_minutes=100)
                )
                await session.execute(
                    update(SyncState)
                    .where(SyncState.end_user_id == owner, SyncState.metric == "sleeps")
                    .values(
                        last_data_at=NOW - timedelta(minutes=1),
                        last_success_at=NOW,
                        last_status="success",
                    )
                )
        finally:
            writer_committed.set()

    writer_task = asyncio.create_task(writer())
    try:
        result = await service.get_metric_result(
            owner, "sleeps", date(2026, 10, 21), TODAY, session_factory=factory
        )
        await writer_task
    finally:
        if not writer_task.done():
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
    assert writer_committed.is_set()
    assert (
        result.days[0].value
        == result.summary.avg
        == result.detail.nights[0].sleep_minutes
        == 412
    )
    assert result.data_status.last_data_at == NOW - timedelta(
        days=1, hours=6, minutes=35
    )
    assert result.to_public().detail.nights[0].rem_minutes == 85

    monkeypatch.setattr(repository, "list_sync_states", original)
    refreshed = await service.get_metric_result(
        owner, "sleeps", date(2026, 10, 21), TODAY, session_factory=factory
    )
    assert (
        refreshed.days[0].value
        == refreshed.summary.avg
        == refreshed.detail.nights[0].sleep_minutes
        == 500
    )
    assert refreshed.data_status.last_data_at == NOW - timedelta(minutes=1)
    assert refreshed.detail.nights[0].rem_minutes == 100
