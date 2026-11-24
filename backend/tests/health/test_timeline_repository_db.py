from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.health import repository
from app.health.errors import InvalidHealthData
from app.health.models import ActivityDaily, HeartDaily, SleepSession
from app.health.schemas import KST
from tests.health.test_timeline_contract import TODAY

pytestmark = pytest.mark.db


def stored_sleep(owner, day=TODAY, data_id="main", **changes):
    end = datetime.combine(day, datetime.min.time(), tzinfo=KST) + timedelta(hours=6)
    return SleepSession(
        **(
            dict(
                end_user_id=owner,
                date=day,
                data_id=data_id,
                start_at=end - timedelta(hours=7),
                end_at=end,
                is_main=True,
                session_minutes=420,
                sleep_timeline=None,
            )
            | changes
        )
    )


async def test_bulk_projections_isolate_owner_period_and_survive_session_close(
    health_db,
):
    factory, (owner, other) = health_db
    async with factory.begin() as db:
        for member in (owner, other):
            for offset in (-1, 0, 1, 2):
                day = TODAY + timedelta(days=offset)
                db.add_all(
                    [
                        ActivityDaily(
                            end_user_id=member,
                            date=day,
                            steps=99999,
                            hourly_steps=[0] * 24 if offset == 0 else None,
                        ),
                        HeartDaily(
                            end_user_id=member,
                            date=day,
                            hourly_hr={
                                "23": {"avg": 70, "min": 60, "max": 80, "count": 2}
                            }
                            if offset == 0
                            else None,
                        ),
                        stored_sleep(member, day, str(offset)),
                    ]
                )
            db.add(stored_sleep(member, data_id="nap", is_main=False))
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        async with factory() as db:
            args = (db, owner, TODAY, TODAY + timedelta(days=1))
            steps = await repository.list_hourly_steps(*args)
            hearts = await repository.list_hourly_hearts(*args)
            sleeps = await repository.list_main_sleeps(*args)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    for records in (steps, hearts, sleeps):
        assert [r.date for r in records] == [TODAY, TODAY + timedelta(days=1)]
        assert all(r.model_dump_json() for r in records)
    assert steps[0].hourly_steps == [0] * 24 and steps[1].hourly_steps is None
    assert hearts[0].hourly_hr["23"]["count"] == 2
    assert hearts[1].hourly_hr is None
    assert sleeps[0].start_at.astimezone(KST).date() == TODAY - timedelta(days=1)
    assert sleeps[0].sleep_timeline is None
    assert len(statements) == 3
    assert not any("raw_health_record" in sql or "payload" in sql for sql in statements)


async def test_no_main_sleep_is_empty_and_multiple_main_sleeps_fail(health_db):
    factory, (owner, _) = health_db
    async with factory.begin() as db:
        db.add(stored_sleep(owner, data_id="nap", is_main=False))
    async with factory() as db:
        assert await repository.list_main_sleeps(db, owner, TODAY, TODAY) == []
    async with factory.begin() as db:
        db.add_all(
            [
                stored_sleep(owner, data_id="main-a"),
                stored_sleep(owner, data_id="main-b"),
            ]
        )
    async with factory() as db:
        with pytest.raises(InvalidHealthData):
            await repository.list_main_sleeps(db, owner, TODAY, TODAY)


async def test_saved_sleep_stages_are_read_without_loading_raw_payload(health_db):
    factory, (owner, _) = health_db
    timeline = [
        {
            "start": "2026-10-25T23:00:00+09:00",
            "end": "2026-10-26T06:00:00+09:00",
            "stage": "light",
        }
    ]
    async with factory.begin() as db:
        db.add(stored_sleep(owner, sleep_timeline=timeline))
    async with factory() as db:
        records = await repository.list_main_sleeps(db, owner, TODAY, TODAY)
    assert records[0].sleep_timeline == timeline
