from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, text, update

from app.health import repository, service
from app.health.errors import HealthDataLookupError
from app.health.models import ActivityDaily, ExerciseSession, SyncState
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_exercise import DAY, exercise
from tests.health.test_exercise_repository_db import stored_session

pytestmark = pytest.mark.db


@pytest.mark.parametrize("counted", [True, False])
async def test_exercise_service_checks_counted_aggregate_and_keeps_all_sessions(
    health_db, monkeypatch, counted
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    await seed_exercises(factory, owner, other)
    excluded = exercise(
        start_at=exercise().start_at - timedelta(hours=1),
        end_at=exercise().start_at,
        duration_minutes=30,
        exercise_type=79,
        counts_as_exercise=False,
        avg_speed_mps=Decimal("0.50"),
    )
    async with factory.begin() as db:
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner)
            .values(counts_as_exercise=counted)
        )
        if not counted:
            await db.execute(
                update(ActivityDaily)
                .where(ActivityDaily.end_user_id == owner)
                .values(exercise_minutes=0, exercise_count=0)
            )
        db.add(stored_session(owner, "uncounted", excluded))
    result = await service.get_metric_result(
        owner, "exercises", DAY, TODAY, session_factory=factory
    )
    assert [day.value for day in result.days] == ([60, 30] if counted else [0, 0])
    assert all(not day.missing and day.reason is None for day in result.days)
    assert result.summary.total == (60 if counted else 0)
    assert result.summary.days_with_data == 1
    assert len(result.detail.sessions) == 3
    payload = result.to_public().model_dump(mode="json")
    assert payload["detail"]["sessions"][0]["counts_as_exercise"] is False
    assert payload["detail"]["sessions"][0]["avg_speed_mps"] == 0.5


async def seed_exercises(factory, owner, other):
    yesterday = exercise()
    today = exercise(
        date=TODAY,
        start_at=NOW - timedelta(minutes=30),
        end_at=NOW,
        duration_minutes=30,
    )
    async with factory.begin() as session:
        session.add_all(
            [
                SyncState(
                    end_user_id=owner,
                    metric="exercise_sessions",
                    initial_sync_done=True,
                    first_data_at=NOW - timedelta(days=3),
                    last_data_at=NOW,
                    last_success_at=NOW,
                    last_status="fail",
                ),
                ActivityDaily(end_user_id=owner, date=DAY - timedelta(days=2), steps=0),
                ActivityDaily(
                    end_user_id=owner, date=DAY, exercise_count=1, exercise_minutes=60
                ),
                ActivityDaily(
                    end_user_id=owner, date=TODAY, exercise_count=1, exercise_minutes=30
                ),
                ActivityDaily(
                    end_user_id=other, date=DAY, exercise_count=1, exercise_minutes=60
                ),
                stored_session(owner, "yesterday", yesterday),
                stored_session(owner, "today", today),
                stored_session(other, "yesterday", yesterday),
            ]
        )


async def test_exercise_service_zero_missing_today_and_public_details(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    await seed_exercises(factory, owner, other)
    result = await service.get_metric_result(
        owner, "exercises", DAY - timedelta(days=3), TODAY, session_factory=factory
    )
    assert [day.value for day in result.days] == [None, 0, None, 60, 30]
    assert [day.reason for day in result.days] == [
        "BEFORE_LINK",
        None,
        "NO_RECORD",
        None,
        None,
    ]
    assert result.summary.total == 60 and result.summary.avg == 30
    assert result.summary.days_with_data == 2 and result.summary.completed_days == 3
    assert result.summary.missing_days == [DAY - timedelta(days=1)]
    assert result.in_progress.value == 30 and result.in_progress.as_of == NOW
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.last_success_at == NOW
    public = result.to_public().model_dump(mode="json")
    assert "user_id" not in public
    assert [item["duration_minutes"] for item in public["detail"]["sessions"]] == [
        60,
        30,
    ]
    assert public["detail"]["sessions"][0]["type_name"] == "축구"


@pytest.mark.parametrize("has_daily", [True, False])
async def test_exercise_no_sessions_uses_daily_presence_even_when_steps_missing(
    health_db, monkeypatch, has_daily
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as session:
        session.add(
            SyncState(
                end_user_id=owner,
                metric="exercise_sessions",
                initial_sync_done=True,
                first_data_at=NOW - timedelta(days=3),
                last_data_at=NOW,
                last_success_at=NOW,
            )
        )
        if has_daily:
            # 정상 무운동과 전체 이상치 제거는 현재 집계 결과에서 동일하다.
            session.add(ActivityDaily(end_user_id=owner, date=DAY, steps=None))
    result = await service.get_metric_result(
        owner, "exercises", DAY, DAY, session_factory=factory
    )
    assert result.days[0].value == (0 if has_daily else None)
    assert result.days[0].reason == (None if has_daily else "NO_RECORD")
    assert result.summary.total == result.summary.avg == (0 if has_daily else None)
    assert result.summary.days_with_data == int(has_daily)
    assert result.detail.sessions == []


@pytest.mark.parametrize(
    "corruption", ["minutes", "count", "missing_daily", "invalid_session"]
)
async def test_exercise_inconsistent_aggregates_are_lookup_failure(
    health_db, monkeypatch, corruption
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    await seed_exercises(factory, owner, other)
    async with factory.begin() as session:
        condition = (ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY)
        if corruption == "missing_daily":
            await session.execute(delete(ActivityDaily).where(*condition))
        elif corruption == "invalid_session":
            await session.execute(
                update(ExerciseSession)
                .where(
                    ExerciseSession.end_user_id == owner, ExerciseSession.date == DAY
                )
                .values(duration_minutes=0)
            )
        else:
            values = (
                {"exercise_minutes": 99}
                if corruption == "minutes"
                else {"exercise_count": 2}
            )
            await session.execute(
                update(ActivityDaily).where(*condition).values(**values)
            )
    with pytest.raises(HealthDataLookupError) as error:
        await service.get_metric_result(
            owner, "exercises", DAY, DAY, session_factory=factory
        )
    assert error.value.metrics == ("exercises",)


async def test_exercise_reaggregation_between_daily_and_sessions_keeps_one_snapshot(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    await seed_exercises(factory, owner, other)
    original = repository.list_exercise_sessions

    async def commit_before_sessions(db_session, user_id, start, end):
        assert (
            await db_session.scalar(text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        assert await db_session.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(ActivityDaily)
                .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY)
                .values(exercise_minutes=90)
            )
            await writer.execute(
                update(ExerciseSession)
                .where(
                    ExerciseSession.end_user_id == owner, ExerciseSession.date == DAY
                )
                .values(
                    duration_minutes=90,
                    end_at=exercise().end_at + timedelta(minutes=30),
                )
            )
            await writer.execute(
                update(SyncState)
                .where(
                    SyncState.end_user_id == owner,
                    SyncState.metric == "exercise_sessions",
                )
                .values(last_success_at=NOW + timedelta(minutes=1))
            )
        return await original(db_session, user_id, start, end)

    monkeypatch.setattr(repository, "list_exercise_sessions", commit_before_sessions)
    result = await service.get_metric_result(
        owner, "exercises", DAY, DAY, session_factory=factory
    )
    assert (
        result.summary.total
        == result.days[0].value
        == result.detail.sessions[0].duration_minutes
        == 60
    )
    assert result.data_status.last_success_at == NOW
    monkeypatch.setattr(repository, "list_exercise_sessions", original)
    refreshed = await service.get_metric_result(
        owner, "exercises", DAY, DAY, session_factory=factory
    )
    assert (
        refreshed.summary.total
        == refreshed.days[0].value
        == refreshed.detail.sessions[0].duration_minutes
        == 90
    )
    assert refreshed.data_status.last_success_at == NOW + timedelta(minutes=1)
