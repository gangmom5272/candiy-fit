import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, update

from app.health import repository, service
from app.health.calculations import exercise_records
from app.health.models import ActivityDaily, ExerciseSession, SyncState
from app.health.repository import list_exercise_sessions
from tests.health.test_activity_daily_db import session as activity_session
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_exercise import DAY, exercise

pytestmark = pytest.mark.db


async def test_metric_activity_uses_stored_values_same_snapshot_and_no_extra_selects(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(
            SyncState(
                end_user_id=owner,
                metric="exercise_sessions",
                initial_sync_done=True,
                first_data_at=NOW - timedelta(days=10),
                last_data_at=NOW,
                last_success_at=NOW,
                last_status="fail",
            )
        )
        # 선택 원천 미준비·상태 없음은 저장된 운동 값을 숨기지 않는다.
        db.add(
            SyncState(
                end_user_id=owner,
                metric="calories_burned",
                initial_sync_done=False,
                last_status="fail",
            )
        )
        db.add(
            ActivityDaily(
                end_user_id=owner,
                date=DAY,
                exercise_minutes=20,
                exercise_count=1,
                exercise_calories_kcal=240,
                exercise_distance_m=4200,
                computed_at=NOW,
            )
        )
        db.add(
            activity_session(owner, "counted", DAY, calories_kcal=200, distance_m=3000)
        )
        db.add(
            activity_session(
                owner,
                "uncounted",
                DAY,
                counted=False,
                calories_kcal=40,
                distance_m=1200,
            )
        )
        db.add(
            ActivityDaily(
                end_user_id=owner, date=TODAY, exercise_calories_kcal=0, computed_at=NOW
            )
        )
        db.add(ActivityDaily(end_user_id=other, date=DAY, exercise_calories_kcal=999))

    original = repository.list_sync_states
    snapshot_read, committed = asyncio.Event(), asyncio.Event()

    async def pause_after_states(db, user_id):
        result = await original(db, user_id)
        snapshot_read.set()
        await committed.wait()
        return result

    monkeypatch.setattr(repository, "list_sync_states", pause_after_states)

    async def writer():
        await snapshot_read.wait()
        try:
            async with factory.begin() as db:
                await db.execute(
                    update(ActivityDaily)
                    .where(
                        ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY
                    )
                    .values(exercise_calories_kcal=500)
                )
        finally:
            committed.set()

    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    task = asyncio.create_task(writer())
    try:
        result = await service.get_metric_result(
            owner, "exercises", DAY, TODAY, session_factory=factory
        )
        await task
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(statements) == 3  # 상태·활동·세션. 심박 미준비라 기존 착용 조회는 없다.
    detail = result.detail.exercise_activity
    assert detail.days[0].exercise_calories_kcal == 240
    assert detail.days[0].exercise_distance_m == 4200
    assert detail.summary.exercise_count.total == 1
    assert detail.summary.exercise_minutes.total == result.summary.total == 20
    assert detail.days[1].exercise_calories_kcal == 0
    assert detail.days[1].exercise_distance_m is None
    assert detail.source_statuses["exercise_sessions"].last_sync_status == "fail"
    assert detail.source_statuses["calories_burned"].ready is False
    assert detail.source_statuses["distances"].ready is None
    assert len(result.detail.sessions) == 2
    monkeypatch.setattr(repository, "list_sync_states", original)
    refreshed = await service.get_metric_result(
        owner, "exercises", DAY, TODAY, session_factory=factory
    )
    assert refreshed.detail.exercise_activity.days[0].exercise_calories_kcal == 500


def stored_session(owner, data_id, detail, **changes):
    values = detail.model_dump(
        exclude={
            "record_id",
            "exercise_type",
            "type_name",
            "category",
            "avg_heart_rate",
            "max_heart_rate",
        }
    )
    return ExerciseSession(
        end_user_id=owner,
        data_id=data_id,
        code=detail.exercise_type,
        hr_avg=detail.avg_heart_rate,
        hr_max=detail.max_heart_rate,
        **(values | changes),
    )


async def test_exercise_lookup_isolates_owner_and_kst_start_dates_with_code_join(
    health_db,
):
    factory, (owner, other) = health_db
    evening = exercise(
        record_id="evening", duration_minutes=59, distance_m=Decimal("1234.5")
    )
    morning = exercise(
        record_id="slow-walk",
        start_at=evening.start_at - timedelta(hours=23),
        end_at=evening.start_at - timedelta(hours=22),
        exercise_type=79,
        type_name="걷기",
        calories_kcal=None,
        avg_heart_rate=None,
        max_heart_rate=None,
        counts_as_exercise=False,
        avg_speed_mps=Decimal("0.50"),
        distance_m=Decimal("0.0"),
    )
    unknown = exercise(
        record_id="unknown",
        start_at=evening.start_at - timedelta(hours=2),
        end_at=evening.start_at - timedelta(hours=1),
        exercise_type=9999,
        type_name="기타 운동",
        category="other",
        title="합성 운동",
    )
    async with factory.begin() as session:
        session.add_all(
            [
                stored_session(owner, "evening", evening),
                stored_session(other, "evening", evening, distance_m=Decimal("9999.9")),
                stored_session(owner, "unknown", unknown),
                stored_session(
                    owner, "slow-walk", morning, avg_speed_mps=Decimal("0.50")
                ),
                stored_session(
                    owner,
                    "before",
                    evening,
                    date=DAY - timedelta(days=1),
                    start_at=evening.start_at - timedelta(days=1),
                    end_at=evening.end_at - timedelta(days=1),
                ),
                stored_session(
                    owner,
                    "after",
                    evening,
                    date=DAY + timedelta(days=1),
                    start_at=evening.start_at + timedelta(days=1),
                    end_at=evening.end_at + timedelta(days=1),
                ),
            ]
        )
    async with factory() as session:
        sessions = await list_exercise_sessions(session, owner, DAY, DAY)
        empty = await list_exercise_sessions(
            session, owner, DAY - timedelta(days=3), DAY - timedelta(days=2)
        )
    assert sessions == [morning, unknown, evening]
    assert [item.distance_m for item in sessions] == [0, None, Decimal("1234.5")]
    assert empty == []
    records = exercise_records(sessions)
    assert len(records) == 1
    assert records[0].date == DAY and records[0].value == 119
    assert records[0].sessions == tuple(sessions)


async def test_exercise_lookup_returns_every_day_in_inclusive_period(health_db):
    factory, (owner, _) = health_db
    base = exercise()
    details = [
        exercise(
            record_id=f"period-{offset + 1}",
            date=DAY + timedelta(days=offset),
            start_at=base.start_at + timedelta(days=offset),
            end_at=base.end_at + timedelta(days=offset),
        )
        for offset in range(-1, 4)
    ]
    async with factory.begin() as session:
        session.add_all(
            stored_session(owner, f"period-{index}", detail)
            for index, detail in reversed(list(enumerate(details)))
        )
    async with factory() as session:
        sessions = await list_exercise_sessions(
            session, owner, DAY, DAY + timedelta(days=2)
        )

    # 시작일·중간일·종료일을 포함하고 양쪽 범위 밖 기록은 제외한다.
    assert sessions == details[1:4]
    assert [item.date for item in sessions] == [
        DAY,
        DAY + timedelta(days=1),
        DAY + timedelta(days=2),
    ]
