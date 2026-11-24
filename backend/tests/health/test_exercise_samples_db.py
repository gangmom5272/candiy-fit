from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, update

from app.health import errors, repository, service
from app.health.models import ExerciseSession, RawHealthRecord, SyncState
from tests.health.test_calculations import NOW, TODAY

pytestmark = pytest.mark.db
START = NOW - timedelta(hours=1)


def session(owner, **changes):
    return ExerciseSession(
        **(
            dict(
                end_user_id=owner,
                data_id="exercise",
                date=TODAY,
                code=79,
                start_at=START,
                end_at=NOW,
                duration_minutes=60,
                computed_at=NOW - timedelta(days=1),
                counts_as_exercise=False,
            )
            | changes
        )
    )


def raw(owner, record_id="raw", **changes):
    return RawHealthRecord(
        **(
            dict(
                end_user_id=owner,
                metric="heart_rates",
                data_id=record_id,
                app="synthetic.app",
                start_at=START - timedelta(minutes=1),
                end_at=NOW + timedelta(minutes=1),
                payload={"samples": [sample(START), sample(NOW)]},
                last_modified_at=NOW,
                source_created_at=NOW,
                source_updated_at=NOW,
                ingested_at=NOW,
            )
            | changes
        )
    )


def sample(instant, value=120, key="beatsPerMinute"):
    return {"time": instant.isoformat(), key: value}


async def seed(factory, owner, *records):
    async with factory.begin() as db:
        db.add(session(owner))
        for metric in ("exercise_sessions", "heart_rates", "speeds"):
            db.add(
                SyncState(
                    end_user_id=owner,
                    metric=metric,
                    initial_sync_done=True,
                    last_status="fail",
                    last_success_at=NOW - timedelta(days=1),
                )
            )
        db.add_all(records)


async def call(factory, owner, metric="heart_rates", record_id="exercise"):
    return await service.get_exercise_samples(
        owner, record_id, metric, session_factory=factory
    )


async def test_overlap_boundaries_sort_source_and_no_aggregate_filter(health_db):
    factory, (owner, other) = health_db
    await seed(
        factory,
        owner,
        raw(
            owner,
            "z",
            app="synthetic.two",
            payload={
                "samples": [
                    sample(NOW, 250),
                    sample(START, 20),
                    sample(START - timedelta(microseconds=1)),
                    sample(NOW + timedelta(microseconds=1)),
                    sample(START, 21),
                ],
                "unrelated": ["x" * 1000] * 1000,
            },
        ),
        raw(owner, "a"),
        raw(other, "foreign", payload={}),
        raw(owner, "wrong-metric", metric="weights", payload={}),
        raw(
            owner,
            "outside",
            start_at=NOW + timedelta(seconds=1),
            end_at=NOW + timedelta(seconds=2),
            payload={},
        ),
    )
    result = await call(factory, owner)
    assert [(s.record_id, s.sample_index, s.value) for s in result.samples] == [
        ("a", 0, 120),
        ("z", 1, 20),
        ("z", 4, 21),
        ("a", 1, 120),
        ("z", 0, 250),
    ]
    assert result.samples[1].source_app == "synthetic.two"
    assert result.samples[0].ingested_at > result.source_status.last_success_at
    assert result.exercise.computed_at == NOW - timedelta(days=1)
    assert result.source_status.last_sync_status == "fail"
    assert "unrelated" not in result.model_dump_json()


async def test_speed_zero_and_outlier_are_preserved(health_db):
    factory, (owner, _) = health_db
    await seed(
        factory,
        owner,
        raw(
            owner,
            metric="speeds",
            payload={
                "samples": [
                    sample(START, 0, "speed_mps"),
                    sample(NOW, 20.125, "speed_mps"),
                ]
            },
        ),
    )
    result = await call(factory, owner, "speeds")
    assert result.unit == "m/s"
    assert [s.value for s in result.samples] == [0, Decimal("20.125")]


async def test_missing_and_foreign_exercise_are_identical(health_db):
    factory, (owner, other) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        db.add(session(other, data_id="foreign"))
    for identifier in ("missing", "foreign"):
        with pytest.raises(errors.ExerciseRecordNotFoundError):
            await call(factory, owner, record_id=identifier)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"samples": None},
        {"samples": {}},
        {"samples": "bad"},
        {"samples": [None]},
        {"samples": [sample(START, True)]},
        {"samples": [sample(START, "120")]},
        {"samples": [sample(START, 1.5)]},
        {"samples": [sample(START, -1)]},
        {"samples": [{"time": "bad", "beatsPerMinute": 120}]},
        {"samples": [{"time": "2026-10-22T04:00:00", "beatsPerMinute": 120}]},
        {"samples": [{"time": 0, "beatsPerMinute": 120}]},
        {"samples": [{"time": START.isoformat()}]},
    ],
)
async def test_malformed_sample_fails_instead_of_empty(health_db, payload):
    factory, (owner, _) = health_db
    await seed(factory, owner, raw(owner, payload=payload))
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


@pytest.mark.parametrize("records", [False, True])
async def test_no_records_or_empty_array_are_normal_empty(health_db, records):
    factory, (owner, _) = health_db
    await seed(
        factory, owner, *([raw(owner, payload={"samples": []})] if records else [])
    )
    assert (await call(factory, owner)).samples == []


@pytest.mark.parametrize(
    "changes", [{"end_at": None}, {"end_at": START - timedelta(minutes=2)}]
)
async def test_invalid_raw_interval_fails(health_db, changes):
    factory, (owner, _) = health_db
    await seed(factory, owner, raw(owner, start_at=START, **changes))
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


async def test_invalid_old_interval_does_not_poison_unrelated_exercise(health_db):
    factory, (owner, _) = health_db
    await seed(
        factory,
        owner,
        raw(owner, start_at=START - timedelta(days=7), end_at=None, payload={}),
    )
    assert (await call(factory, owner)).samples == []


async def test_sample_outside_parent_record_fails(health_db):
    factory, (owner, _) = health_db
    await seed(
        factory,
        owner,
        raw(owner, payload={"samples": [sample(NOW + timedelta(hours=1))]}),
    )
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


@pytest.mark.parametrize("kind", ["rows", "samples", "bytes", "duration"])
async def test_limit_stops_before_array_transfer(health_db, kind):
    factory, (owner, _) = health_db
    records = []
    if kind == "rows":
        records = [raw(owner, f"raw-{i:04}", payload={}) for i in range(1001)]
    elif kind == "samples":
        records = [raw(owner, payload={"samples": [None] * 20001})]
    elif kind == "bytes":
        records = [
            raw(owner, payload={"samples": [{"extra": "x" * (8 * 1024 * 1024)}]})
        ]
    await seed(factory, owner, *records)
    if kind == "duration":
        async with factory.begin() as db:
            await db.execute(
                update(ExerciseSession)
                .where(ExerciseSession.end_user_id == owner)
                .values(
                    start_at=NOW - timedelta(hours=25), date=TODAY - timedelta(days=1)
                )
            )
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(errors.ExerciseSampleLimitError):
            await call(factory, owner)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    # Metadata can inspect arrays in the DB; no SELECT of arrays to the app is allowed here.
    assert not any("sample_array" in sql for sql in statements)


async def test_total_array_budget_is_before_time_filter(health_db):
    factory, (owner, _) = health_db
    await seed(
        factory,
        owner,
        raw(owner, "a", payload={"samples": [sample(START)] * 10001}),
        raw(
            owner,
            "b",
            payload={"samples": [sample(START - timedelta(seconds=1))] * 10000},
        ),
    )
    with pytest.raises(errors.ExerciseSampleLimitError):
        await call(factory, owner)


async def test_exact_row_and_duration_limit(health_db):
    factory, (owner, _) = health_db
    await seed(
        factory,
        owner,
        *[raw(owner, f"row-{i}", payload={"samples": []}) for i in range(1000)],
    )
    async with factory.begin() as db:
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner)
            .values(start_at=NOW - timedelta(hours=24), date=TODAY - timedelta(days=1))
        )
    assert (await call(factory, owner)).samples == []


async def test_exact_sample_limit_and_large_unrelated_payload(health_db, monkeypatch):
    # 데이터 한계의 정확성 검사이며 1초 성능 보장이 아니다. 취소는 별도 테스트한다.
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 10.0)
    factory, (owner, _) = health_db
    await seed(
        factory,
        owner,
        raw(
            owner,
            payload={
                "samples": [sample(START)] * 20000,
                "unrelated": "x" * (9 * 1024 * 1024),
            },
        ),
    )
    result = await call(factory, owner)
    assert len(result.samples) == 20000
    assert result.samples[-1].sample_index == 19999


async def test_readonly_snapshot_survives_concurrent_raw_update(health_db, monkeypatch):
    factory, (owner, _) = health_db
    await seed(factory, owner, raw(owner))
    original = repository.list_sync_states

    async def update_after_snapshot(db, member):
        states = await original(db, member)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(RawHealthRecord)
                .where(RawHealthRecord.end_user_id == owner)
                .values(payload={"samples": [sample(START, 150)]})
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", update_after_snapshot)
    assert [s.value for s in (await call(factory, owner)).samples] == [120, 120]
    monkeypatch.setattr(repository, "list_sync_states", original)
    assert [s.value for s in (await call(factory, owner)).samples] == [150]
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"
