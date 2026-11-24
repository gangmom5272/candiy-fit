from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, update

from app.health import repository, service
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.models import ActivityDaily, ExerciseSession, SyncState
from app.health.schemas import KST
from tests.health.test_calculations import NOW, TODAY

pytestmark = pytest.mark.db


def session(owner, key, day, *, minutes=20, counted=True, **changes):
    start = datetime.combine(day, time(23, 50), tzinfo=KST)
    return ExerciseSession(
        **(
            dict(
                end_user_id=owner,
                data_id=key,
                date=day,
                code=79,
                start_at=start,
                end_at=start + timedelta(minutes=minutes),
                duration_minutes=minutes,
                counts_as_exercise=counted,
            )
            | changes
        )
    )


def ready(owner):
    return SyncState(
        end_user_id=owner,
        metric="exercise_sessions",
        initial_sync_done=True,
        last_success_at=NOW,
        last_status="success",
    )


async def test_new_member_checks_state_and_history_with_two_selects(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(other))
        db.add(ActivityDaily(end_user_id=other, date=TODAY))
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(HealthDataNotReadyError) as error:
            await service.get_activity_daily(
                owner, TODAY, TODAY, session_factory=factory
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert error.value.metrics == ("exercises",)
    assert len(statements) == 2
    assert "sync_state" in statements[0].lower()
    assert "EXISTS (SELECT" in statements[1].upper()


async def test_daily_values_match_stored_aggregates_and_start_day_with_three_selects(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    start = TODAY - timedelta(days=3)
    async with factory.begin() as db:
        db.add(ready(owner))
        # 칼로리 최초 실패, 거리 상태 없음도 운동 조회를 막지 않는다.
        db.add(
            SyncState(
                end_user_id=owner,
                metric="calories_burned",
                initial_sync_done=False,
                last_status="fail",
            )
        )
        db.add(ActivityDaily(end_user_id=owner, date=start, steps=300, computed_at=NOW))
        db.add(
            ActivityDaily(
                end_user_id=owner,
                date=TODAY - timedelta(days=1),
                exercise_count=1,
                exercise_minutes=20,
                exercise_calories_kcal=Decimal("12.3"),
                exercise_distance_m=Decimal("100.1"),
                computed_at=NOW,
            )
        )
        # 자정을 넘는 인정 운동과 미인정 운동: 시간·횟수와 칼로리·거리의 대상이 다르다.
        db.add(
            session(
                owner,
                "counted",
                TODAY - timedelta(days=1),
                calories_kcal=Decimal("12.3"),
                distance_m=None,
                hr_avg=Decimal("-1"),
            )
        )
        db.add(
            session(
                owner,
                "uncounted",
                TODAY - timedelta(days=1),
                counted=False,
                calories_kcal=None,
                distance_m=Decimal("100.1"),
            )
        )
        db.add(
            ActivityDaily(
                end_user_id=owner,
                date=TODAY,
                exercise_calories_kcal=Decimal("0"),
                exercise_distance_m=Decimal("2.5"),
                computed_at=NOW,
            )
        )
        db.add(
            session(
                owner,
                "only-uncounted",
                TODAY,
                counted=False,
                calories_kcal=Decimal("0"),
                distance_m=Decimal("2.5"),
            )
        )
        db.add(ActivityDaily(end_user_id=other, date=TODAY, exercise_minutes=999))
        db.add(session(other, "foreign", TODAY, minutes=999))
        # 요청 범위 밖의 불일치 행은 이 조회에 영향을 주지 않는다.
        db.add(
            ActivityDaily(
                end_user_id=owner, date=start - timedelta(days=1), exercise_minutes=999
            )
        )
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = await service.get_activity_daily(
            owner, start, TODAY, session_factory=factory
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 3
    assert all(
        "hr_avg" not in statement and "raw_health_record" not in statement
        for statement in statements
    )
    assert [day.exercise_minutes for day in result.days] == [0, None, 20, 0]
    assert [day.exercise_count for day in result.days] == [0, None, 1, 0]
    assert result.days[1].reason == "NO_RECORD" and result.days[1].computed_at is None
    assert result.days[2].exercise_calories_kcal == Decimal("12.3")
    assert result.days[2].exercise_distance_m == Decimal("100.1")
    assert result.days[3].exercise_calories_kcal == 0 and result.days[3].is_in_progress
    assert result.source_statuses["calories_burned"].ready is False
    assert result.source_statuses["distances"].ready is None


@pytest.mark.parametrize(
    "case",
    ["minutes", "count", "orphan", "uncounted_orphan", "negative", "invalid_duration"],
)
async def test_inconsistent_or_invalid_aggregate_is_lookup_failure(
    health_db, monkeypatch, case
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        if case not in ("orphan", "uncounted_orphan"):
            db.add(
                ActivityDaily(
                    end_user_id=owner,
                    date=TODAY,
                    exercise_count=2 if case == "count" else 1,
                    exercise_minutes=21 if case == "minutes" else 20,
                    exercise_distance_m=Decimal("-1") if case == "negative" else None,
                )
            )
        db.add(session(owner, "synthetic", TODAY, counted=case != "uncounted_orphan"))
        if case == "invalid_duration":
            db.add(session(owner, "invalid", TODAY, minutes=0, counted=False))
    with pytest.raises(HealthDataLookupError) as error:
        await service.get_activity_daily(owner, TODAY, TODAY, session_factory=factory)
    assert error.value.metrics == ("exercises",)


async def test_status_rows_and_comparison_share_readonly_snapshot(
    health_db, monkeypatch
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(
            ActivityDaily(
                end_user_id=owner, date=TODAY, exercise_count=1, exercise_minutes=20
            )
        )
        db.add(session(owner, "synthetic", TODAY))
    original = repository.list_sync_states

    async def concurrent_write(db, member):
        states = await original(db, member)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(ActivityDaily)
                .where(ActivityDaily.end_user_id == owner)
                .values(exercise_minutes=30)
            )
            await writer.execute(
                update(ExerciseSession)
                .where(ExerciseSession.end_user_id == owner)
                .values(duration_minutes=30)
            )
            await writer.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(last_status="fail")
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", concurrent_write)
    result = await service.get_activity_daily(
        owner, TODAY, TODAY, session_factory=factory
    )
    assert result.days[0].exercise_minutes == 20
    assert result.source_statuses["exercise_sessions"].last_sync_status == "success"
    monkeypatch.setattr(repository, "list_sync_states", original)
    following = await service.get_activity_daily(
        owner, TODAY, TODAY, session_factory=factory
    )
    assert following.days[0].exercise_minutes == 30
    assert following.source_statuses["exercise_sessions"].last_sync_status == "fail"
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"
