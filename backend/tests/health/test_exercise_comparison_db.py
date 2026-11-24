import json
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, select, text, update

from app.health import repository, service
from app.health.errors import (
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    UnknownExerciseTypeError,
)
from app.health.models import (
    ActivityDaily,
    ExerciseCode,
    ExerciseSession,
    SleepSession,
    SyncState,
)
from app.health.schemas import KST
from tests.health.test_calculations import NOW
from tests.health.test_exercise_comparison_calculations import START
from tests.health.test_exercise_comparison_calculations import session as exercise
from tests.health.test_exercise_repository_db import stored_session

pytestmark = pytest.mark.db
DATES = [START + timedelta(days=i) for i in range(6)]


async def seed(factory, owner, *, multiplier=1):
    async with factory.begin() as db:
        for metric in ("exercise_sessions", "steps", "sleeps"):
            db.add(
                SyncState(
                    end_user_id=owner,
                    metric=metric,
                    initial_sync_done=True,
                    first_data_at=NOW - timedelta(days=30),
                    last_data_at=NOW,
                    last_success_at=NOW,
                    last_status="success",
                )
            )
        for i, (day, steps, sleep) in enumerate(
            zip(
                DATES,
                [10000, 8000, 9000, 6000, 4000, 5000],
                [450, 420, 435, 360, 390, 375],
                strict=True,
            )
        ):
            db.add(
                ActivityDaily(
                    end_user_id=owner,
                    date=day,
                    steps=steps * multiplier,
                    exercise_count=int(i < 3),
                    exercise_minutes=60 if i < 3 else 0,
                )
            )
            if i < 3 or i == 5:
                db.add(
                    stored_session(
                        owner,
                        f"exercise-{i}",
                        exercise(day, code=56 if i < 3 else 79, counted=i < 3),
                    )
                )
            wake = datetime.combine(day + timedelta(days=1), time(7), KST)
            db.add(
                SleepSession(
                    end_user_id=owner,
                    data_id=f"sleep-{i}",
                    date=wake.date(),
                    start_at=wake - timedelta(hours=8),
                    end_at=wake,
                    is_main=True,
                    session_minutes=480,
                    sleep_minutes=sleep,
                )
            )


async def call(factory, owner, metric="steps", **kwargs):
    return await service.get_exercise_record_comparison(
        owner, metric, DATES[0], DATES[-1], session_factory=factory, **kwargs
    )


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: NOW)


async def snapshot(factory, owner):
    async with factory() as db:
        return [
            list(
                (
                    await db.execute(
                        select(model.__table__)
                        .where(model.end_user_id == owner)
                        .order_by(*model.__table__.primary_key.columns)
                    )
                ).mappings()
            )
            for model in (ActivityDaily, ExerciseSession, SleepSession, SyncState)
        ]


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
@pytest.mark.parametrize("field", ["exercise_calories_kcal", "exercise_distance_m"])
async def test_invalid_optional_activity_keeps_comparison(health_db, metric, field):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    before = await call(factory, owner, metric)
    async with factory.begin() as session:
        await session.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner)
            .values(**{field: -1})
        )
    assert await call(factory, owner, metric) == before


async def test_real_database_fixed_values_owner_isolation_and_no_writes(health_db):
    factory, (owner, other) = health_db
    await seed(factory, owner)
    await seed(factory, other, multiplier=2)
    before = await snapshot(factory, owner)
    steps = await call(factory, owner)
    sleeps = await call(factory, owner, "sleeps")
    other_steps = await call(factory, other)
    assert (steps.with_exercise.avg, steps.without_exercise.avg, steps.difference) == (
        9000,
        5000,
        4000,
    )
    assert (
        sleeps.with_exercise.avg,
        sleeps.without_exercise.avg,
        sleeps.difference,
    ) == (435, 375, 60)
    assert steps.reliable and sleeps.reliable
    for result in (steps, sleeps):
        public = json.loads(result.to_public().model_dump_json())
        assert result.reliable_min_days == public["reliable_min_days"] == 3
        assert "user_id" not in public
        assert public["with_exercise"]["days_with_data"] == 3
        assert public["without_exercise"]["days_with_data"] == 3
    assert other_steps.difference == 8000
    assert steps.user_id == owner and "user_id" not in steps.to_public().model_dump(
        mode="json"
    )
    assert sleeps.metric_period.end == DATES[-1] + timedelta(days=1)
    assert await snapshot(factory, owner) == before


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
@pytest.mark.parametrize("empty_both", [False, True])
async def test_empty_groups_keep_threshold_and_public_sample_counts(
    health_db, metric, empty_both
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    missing_dates = DATES if empty_both else DATES[:3]
    async with factory.begin() as db:
        if metric == "steps":
            await db.execute(
                update(ActivityDaily)
                .where(
                    ActivityDaily.end_user_id == owner,
                    ActivityDaily.date.in_(missing_dates),
                )
                .values(steps=None)
            )
        else:
            await db.execute(
                delete(SleepSession).where(
                    SleepSession.end_user_id == owner,
                    SleepSession.date.in_(
                        [d + timedelta(days=1) for d in missing_dates]
                    ),
                )
            )
    result = await call(factory, owner, metric)
    public = json.loads(result.to_public().model_dump_json())
    assert result.reliable_min_days == public["reliable_min_days"] == 3
    assert public["with_exercise"]["days_with_data"] == 0
    assert public["without_exercise"]["days_with_data"] == (0 if empty_both else 3)
    assert public["with_exercise"]["avg"] is None
    assert (public["without_exercise"]["avg"] is None) == empty_both
    assert public["difference"] is None
    assert public["comparable"] is False and public["reliable"] is False
    assert "user_id" not in public


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
async def test_other_comparison_groups_unknown_codes_but_keeps_stored_and_public_codes(
    health_db, metric
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        for day, code in [
            (DATES[0], 999),
            (DATES[1], 0),
            (DATES[2], 70),
            (DATES[5], 998),
        ]:
            await db.execute(
                update(ExerciseSession)
                .where(
                    ExerciseSession.end_user_id == owner, ExerciseSession.date == day
                )
                .values(code=code)
            )
        db.add_all(
            [
                stored_session(owner, "mixed-unknown", exercise(DATES[3], code=999)),
                stored_session(owner, "mixed-known", exercise(DATES[3], code=70)),
            ]
        )
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == DATES[3])
            .values(exercise_count=2, exercise_minutes=120)
        )
    before = await snapshot(factory, owner)
    result = await call(factory, owner, metric, exercise_type=0)
    assert [d.exercise_date for d in result.with_exercise.days] == [
        DATES[0],
        DATES[1],
        DATES[3],
    ]
    assert [d.exercise_date for d in result.without_exercise.days] == [
        DATES[4],
        DATES[5],
    ]
    assert [(d.exercise_date, d.reason) for d in result.excluded_days] == [
        (DATES[2], "OTHER_EXERCISE")
    ]
    assert (
        result.with_exercise.avg,
        result.without_exercise.avg,
        result.difference,
    ) == (
        (8000, 4500, 3500)
        if metric == "steps"
        else (410, Decimal("382.50"), Decimal("27.50"))
    )
    public = await service.get_metric_result(
        owner, "exercises", DATES[0], DATES[-1], session_factory=factory
    )
    assert 999 in {s.exercise_type for s in public.to_public().detail.sessions}
    assert await snapshot(factory, owner) == before


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
@pytest.mark.parametrize("requested_code", [0, 9999])
async def test_catalog_change_during_lookup_keeps_snapshot_and_next_call_uses_new_code(
    health_db, monkeypatch, metric, requested_code
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    code = 9999
    async with factory.begin() as db:
        assert await db.get(ExerciseCode, code) is None
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner)
            .values(code=code)
        )
    original = repository.list_exercise_codes
    changed = False

    async def register_after_read(db):
        nonlocal changed
        codes = await original(db)
        if not changed:
            async with factory.begin() as writer:
                writer.add(
                    ExerciseCode(
                        code=code,
                        name_en="SYNTHETIC_OTHER",
                        name_ko="기타 운동",
                        category="other",
                    )
                )
            changed = True
        return codes

    monkeypatch.setattr(repository, "list_exercise_codes", register_after_read)
    try:
        if requested_code == code:
            with pytest.raises(UnknownExerciseTypeError):
                await call(factory, owner, metric, exercise_type=code)
        else:
            first = await call(factory, owner, metric, exercise_type=0)
            assert first.with_exercise.days_with_data == 3
        second = await call(factory, owner, metric, exercise_type=0)
        exact = await call(factory, owner, metric, exercise_type=code)
        assert second.with_exercise.days_with_data == 0
        assert exact.with_exercise.days_with_data == 3
        assert second.without_exercise.days_with_data == 3
        assert {d.reason for d in second.excluded_days} == {"OTHER_EXERCISE"}
    finally:
        if changed:
            async with factory.begin() as db:
                await db.execute(delete(ExerciseCode).where(ExerciseCode.code == code))


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
@pytest.mark.parametrize("code", [999, -1])
async def test_unknown_request_rejected_without_changing_stored_session_code(
    health_db, metric, code
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        assert await db.get(ExerciseCode, code) is None
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner)
            .values(code=code)
        )
    before = await snapshot(factory, owner)
    with pytest.raises(UnknownExerciseTypeError):
        await call(factory, owner, metric, exercise_type=code)
    all_exercises = await call(factory, owner, metric)
    other = await call(factory, owner, metric, exercise_type=0)
    assert all_exercises.with_exercise.days_with_data == 3
    assert other.with_exercise.days_with_data == 3
    assert other.without_exercise.days_with_data == 3
    assert await snapshot(factory, owner) == before


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
@pytest.mark.parametrize("distance", [None, Decimal("0"), Decimal("1234.5")])
async def test_distance_fields_do_not_change_comparison(health_db, metric, distance):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    baseline = (await call(factory, owner, metric)).to_public().model_dump(mode="json")
    async with factory.begin() as db:
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner)
            .values(distance_m=distance)
        )
        # 160번의 거리 반환과 일별 전체 거리는 인정 기록 그룹·걸음/수면 표본에 관여하지 않는다.
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner)
            .values(exercise_distance_m=Decimal("9999.9"))
        )
    before = await snapshot(factory, owner)
    result = await call(factory, owner, metric)
    assert result.to_public().model_dump(mode="json") == baseline
    assert await snapshot(factory, owner) == before


@pytest.mark.parametrize("exercise_type", [None, 0, 56])
async def test_five_selects_no_heart_query_and_read_only_transaction(
    health_db, monkeypatch, exercise_type
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    original = repository.list_daily_records
    properties = []

    async def inspect_transaction(db, query):
        properties.append(
            (
                await db.scalar(text("SHOW transaction_isolation")),
                await db.scalar(text("SHOW transaction_read_only")),
            )
        )
        return await original(db, query)

    monkeypatch.setattr(repository, "list_daily_records", inspect_transaction)
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        await call(factory, owner, exercise_type=exercise_type)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert properties == [("repeatable read", "on")] * 2
    queries = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(queries) == 5
    assert not any("heart_daily" in sql for sql in queries)


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
async def test_concurrent_reaggregation_and_state_change_keep_one_snapshot(
    health_db, monkeypatch, metric
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    original = repository.list_daily_records
    updated = False

    async def reaggregate(db, query):
        nonlocal updated
        result = await original(db, query)
        if query.metric == "exercises" and not updated:
            updated = True
            async with factory.begin() as writer:
                await writer.execute(
                    update(ActivityDaily)
                    .where(
                        ActivityDaily.end_user_id == owner, ActivityDaily.date == START
                    )
                    .values(steps=1234)
                )
                await writer.execute(
                    update(SleepSession)
                    .where(
                        SleepSession.end_user_id == owner,
                        SleepSession.date == START + timedelta(days=1),
                    )
                    .values(sleep_minutes=123)
                )
                await writer.execute(
                    update(SyncState)
                    .where(SyncState.end_user_id == owner)
                    .values(last_status="fail")
                )
        return result

    monkeypatch.setattr(repository, "list_daily_records", reaggregate)
    first = await call(factory, owner, metric)
    second = await call(factory, owner, metric)
    assert first.with_exercise.days[0].value == (10000 if metric == "steps" else 450)
    assert second.with_exercise.days[0].value == (1234 if metric == "steps" else 123)
    assert first.metric_data_status.last_sync_status == "success"
    assert second.metric_data_status.last_sync_status == "fail"


@pytest.mark.parametrize("corruption", ["count", "minutes", "missing_daily"])
async def test_inconsistent_exercise_is_lookup_failure_not_no_record_group(
    health_db, corruption
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        condition = (ActivityDaily.end_user_id == owner, ActivityDaily.date == START)
        if corruption == "missing_daily":
            await db.execute(delete(ActivityDaily).where(*condition))
        else:
            await db.execute(
                update(ActivityDaily)
                .where(*condition)
                .values(
                    **(
                        {"exercise_count": 2}
                        if corruption == "count"
                        else {"exercise_minutes": 61}
                    )
                )
            )
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory, owner)
    assert caught.value.metrics == ("exercises",)


async def test_missing_sleep_zero_steps_and_other_only_exclusion(health_db):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == START)
            .values(steps=0)
        )
        await db.execute(
            delete(SleepSession).where(
                SleepSession.end_user_id == owner,
                SleepSession.date == START + timedelta(days=1),
            )
        )
        await db.execute(
            update(ExerciseSession)
            .where(
                ExerciseSession.end_user_id == owner, ExerciseSession.date == DATES[1]
            )
            .values(code=70)
        )
    steps = await call(factory, owner, exercise_type=56)
    sleeps = await call(factory, owner, "sleeps", exercise_type=56)
    assert [x.reason for x in steps.excluded_days] == ["ZERO_STEPS", "OTHER_EXERCISE"]
    assert [x.reason for x in sleeps.excluded_days] == ["NO_RECORD", "OTHER_EXERCISE"]
    assert (
        steps.with_exercise.days_with_data == sleeps.with_exercise.days_with_data == 1
    )
    assert steps.without_exercise.days_with_data == 3
    assert not steps.reliable and not sleeps.reliable
    assert steps.reliable_min_days == sleeps.reliable_min_days == 3


@pytest.mark.parametrize(
    "case,error",
    [("unready", HealthDataNotReadyError), ("unlinked", HealthDataNotLinkedError)],
)
async def test_database_readiness_and_unlinked_are_not_empty_comparisons(
    health_db, case, error
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(
                initial_sync_done=False,
                last_error="http_404" if case == "unlinked" else None,
            )
        )
    with pytest.raises(error):
        await call(factory, owner)
