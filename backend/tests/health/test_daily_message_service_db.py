from datetime import timedelta

import pytest
from sqlalchemy import update

from app.health import daily_message_service as service
from app.health.daily_message_read import read_daily_message_block
from app.health.errors import HealthDataLookupError
from app.health.models import (
    ActivityDaily,
    ExerciseSession,
    LifestyleScore,
    SleepSession,
    SyncState,
)
from tests.health.test_daily_message_repository import NOW, TODAY

pytestmark = pytest.mark.db


async def seed_message(factory, owner):
    async with factory.begin() as session:
        for metric in (
            "steps",
            "exercise_sessions",
            "sleeps",
            "heart_rates",
            "oxygen_saturations",
        ):
            session.add(
                SyncState(
                    end_user_id=owner,
                    metric=metric,
                    initial_sync_done=metric
                    in ("steps", "exercise_sessions", "sleeps"),
                    first_data_at=NOW - timedelta(days=3),
                    last_data_at=NOW,
                    last_success_at=NOW,
                    last_status="success",
                )
            )
        session.add_all(
            [
                ActivityDaily(
                    end_user_id=owner,
                    date=TODAY,
                    steps=8200,
                    exercise_minutes=35,
                    exercise_count=1,
                ),
                ExerciseSession(
                    end_user_id=owner,
                    data_id="synthetic-exercise",
                    date=TODAY,
                    code=56,
                    start_at=NOW - timedelta(minutes=35),
                    end_at=NOW,
                    duration_minutes=35,
                ),
                SleepSession(
                    end_user_id=owner,
                    data_id="synthetic-main",
                    date=TODAY,
                    is_main=True,
                    start_at=NOW - timedelta(hours=8),
                    end_at=NOW,
                    session_minutes=480,
                    sleep_minutes=450,
                ),
                LifestyleScore(
                    end_user_id=owner,
                    date=TODAY,
                    sleep_score=90,
                    sleep_minutes=450,
                    computed_at=NOW,
                    hr_baseline_nights=0,
                ),
            ]
        )


@pytest.mark.parametrize(
    "metric,expected", [("steps", 8200), ("exercises", 35), ("sleeps", 450)]
)
async def test_real_metric_adapters_use_fixed_today_without_heart_dependencies(
    health_db, metric, expected
):
    factory, (owner, _) = health_db
    await seed_message(factory, owner)

    async def read(session):
        return await service._read_metric(session, owner, TODAY, metric, lambda _: None)

    result = await read_daily_message_block(
        factory.kw["bind"], read, metric=metric, request_id="synthetic"
    )
    assert result.value.value == expected and result.value.as_of == NOW


@pytest.mark.parametrize("metric", ["exercises", "sleeps"])
async def test_inconsistent_stored_records_fail_the_metric(health_db, metric):
    factory, (owner, _) = health_db
    await seed_message(factory, owner)
    async with factory.begin() as session:
        if metric == "exercises":
            await session.execute(
                update(ActivityDaily)
                .where(ActivityDaily.end_user_id == owner)
                .values(exercise_minutes=36)
            )
        else:
            await session.execute(
                update(SleepSession)
                .where(SleepSession.end_user_id == owner)
                .values(sleep_minutes=481)
            )

    async def read(session):
        return await service._read_metric(session, owner, TODAY, metric, lambda _: None)

    with pytest.raises(HealthDataLookupError):
        await read_daily_message_block(
            factory.kw["bind"], read, metric=metric, request_id="synthetic"
        )


@pytest.mark.parametrize("field", ["exercise_calories_kcal", "exercise_distance_m"])
async def test_invalid_optional_activity_keeps_daily_message(
    health_db, monkeypatch, field
):
    from app.health import service as metric_service

    factory, (owner, _) = health_db
    await seed_message(factory, owner)
    kwargs = dict(today=TODAY, request_id="synthetic", engine=factory.kw["bind"])
    before = await service.get_daily_message(owner, **kwargs)
    async with factory.begin() as session:
        await session.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner)
            .values(**{field: -1})
        )
    after = await service.get_daily_message(owner, **kwargs)
    assert before.code == "BALANCED_RECORDS"
    assert after == before
    monkeypatch.setattr(metric_service, "_now", lambda: NOW)
    with pytest.raises(HealthDataLookupError):
        await metric_service.get_metric_result(
            owner, "exercises", TODAY, TODAY, session_factory=factory
        )


async def test_composition_keeps_sleep_and_score_in_same_real_snapshot(
    health_db, monkeypatch
):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.health import repository

    factory, (owner, _) = health_db
    await seed_message(factory, owner)
    target = create_async_engine(factory.kw["bind"].url, pool_size=1, max_overflow=0)
    control = create_async_engine(factory.kw["bind"].url)
    original = repository.find_sleep_score
    changed = False

    async def score(session, user_id, day):
        nonlocal changed
        if not changed:
            changed = True
            async with control.begin() as connection:
                await connection.execute(
                    update(SleepSession)
                    .where(SleepSession.end_user_id == owner)
                    .values(sleep_minutes=420)
                )
                await connection.execute(
                    update(LifestyleScore)
                    .where(LifestyleScore.end_user_id == owner)
                    .values(sleep_minutes=420, computed_at=NOW + timedelta(seconds=1))
                )
        return await original(session, user_id, day)

    monkeypatch.setattr(repository, "find_sleep_score", score)
    try:
        first = await service.get_daily_message(
            owner, today=TODAY, request_id="synthetic", engine=target
        )
        second = await service.get_daily_message(
            owner, today=TODAY, request_id="synthetic", engine=target
        )
        assert first.code == second.code == "BALANCED_RECORDS"
        assert first.score_computed_at == NOW
        assert second.score_computed_at == NOW + timedelta(seconds=1)
        assert target.pool.checkedout() == control.pool.checkedout() == 0
    finally:
        await target.dispose()
        await control.dispose()
