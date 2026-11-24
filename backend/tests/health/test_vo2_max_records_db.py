from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, update

from app.health import errors, repository, service
from app.health.models import RawHealthRecord, SyncState
from tests.health.test_calculations import NOW, TODAY

pytestmark = pytest.mark.db


def row(owner, record_id="synthetic-vo2", **changes):
    return RawHealthRecord(
        **(
            dict(
                end_user_id=owner,
                metric="vo2_maxes",
                data_id=record_id,
                app="synthetic.app",
                start_at=NOW,
                end_at=None,
                payload={"vo2_ml_per_min_kg": 42.125, "measurement_method": 0},
                last_modified_at=NOW,
                source_created_at=NOW,
                source_updated_at=NOW,
                ingested_at=NOW,
            )
            | changes
        )
    )


def ready(owner, **changes):
    return SyncState(
        **(
            dict(
                end_user_id=owner,
                metric="vo2_maxes",
                initial_sync_done=True,
                last_success_at=NOW,
                last_status="success",
            )
            | changes
        )
    )


async def call(factory, owner, start=TODAY, end=TODAY):
    return await service.get_vo2_max_records(owner, start, end, session_factory=factory)


async def test_kst_half_open_period_owner_metric_order_and_projection(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    start = datetime(2026, 10, 21, 15, tzinfo=UTC)
    end = start + timedelta(days=1)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(
            SyncState(end_user_id=owner, metric="heart_rates", initial_sync_done=False)
        )
        for identifier, instant, app in [
            ("z", start, "synthetic.one"),
            ("a", start, "synthetic.two"),
            ("last", end - timedelta(microseconds=1), "synthetic.one"),
        ]:
            db.add(
                row(
                    owner,
                    identifier,
                    start_at=instant,
                    app=app,
                    payload={
                        "vo2_ml_per_min_kg": 42.125,
                        "measurement_method": 0,
                        "unrelated": ["x" * 1000] * 1000,
                    },
                )
            )
        db.add(
            row(owner, "before", start_at=start - timedelta(microseconds=1), payload={})
        )
        db.add(row(owner, "after", start_at=end, payload={}))
        db.add(row(owner, "metric", metric="weights", payload={}))
        db.add(row(other, "foreign", payload={}))
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = await call(factory, owner)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [r.record_id for r in result.records] == ["a", "z", "last"]
    assert result.records[0].source_app == "synthetic.two"
    assert result.records[-1].measured_at == end - timedelta(microseconds=1)
    assert all(r.date == TODAY for r in result.records)
    assert result.records[0].value == Decimal("42.125")
    assert "unrelated" not in result.model_dump_json()
    assert len(statements) == 2
    sql, parameters = statements[1]
    assert "LIMIT" in sql and 1001 in parameters
    assert "jsonb_typeof" in sql and "CASE" in sql
    assert "raw_health_record.payload," not in sql
    assert (
        await call(
            factory, owner, TODAY - timedelta(days=30), TODAY - timedelta(days=2)
        )
    ).records == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"vo2_ml_per_min_kg": None, "measurement_method": 0},
        {"vo2_ml_per_min_kg": "42.5", "measurement_method": 0},
        {"vo2_ml_per_min_kg": True, "measurement_method": 0},
        {"vo2_ml_per_min_kg": -1, "measurement_method": 0},
        {"vo2_ml_per_min_kg": 100.01, "measurement_method": 0},
        {"vo2_ml_per_min_kg": {"unexpected": 42}, "measurement_method": 0},
        {"vo2_ml_per_min_kg": 42},
        {"vo2_ml_per_min_kg": 42, "measurement_method": None},
        {"vo2_ml_per_min_kg": 42, "measurement_method": "0"},
        {"vo2_ml_per_min_kg": 42, "measurement_method": True},
        {"vo2_ml_per_min_kg": 42, "measurement_method": 1.5},
        {"vo2_ml_per_min_kg": 42, "measurement_method": 1.0},
        {"vo2_ml_per_min_kg": 42, "measurement_method": 6},
        {"vo2_ml_per_min_kg": 42, "measurement_method": -1},
        {"vo2_ml_per_min_kg": 42, "measurement_method": {"code": 0}},
        [],
        "wrong root",
        None,
    ],
)
async def test_invalid_json_is_lookup_failure_not_cast_error_or_empty(
    health_db, monkeypatch, payload
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, payload=payload))
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner)
    assert caught.value.metrics == ()


@pytest.mark.parametrize("key", ["vo2_ml_per_min_kg", "measurement_method"])
async def test_large_wrong_field_is_not_transferred(health_db, monkeypatch, key):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    payload = {
        "vo2_ml_per_min_kg": 42.5,
        "measurement_method": 0,
        key: ["x" * 1000] * 1000,
    }
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, payload=payload))
    original = repository.list_vo2_max_records
    transferred = []

    async def observe(db, query):
        execute = db.execute

        async def capture(*args, **kwargs):
            result = (await execute(*args, **kwargs)).freeze()
            transferred.extend(result().mappings().all())
            return result()

        monkeypatch.setattr(db, "execute", capture)
        return await original(db, query)

    monkeypatch.setattr(repository, "list_vo2_max_records", observe)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)
    assert len(transferred) == 1
    assert all(not isinstance(v, (dict, list)) for v in transferred[0].values())
    assert len(str(dict(transferred[0]))) < 2000


@pytest.mark.parametrize(
    "value,method", [(0, 0), (100, 5), (42.125, 1), (42.5, 2), (42.5, 3), (42.5, 4)]
)
async def test_stored_numeric_and_method_boundaries(
    health_db, monkeypatch, value, method
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(
            row(
                owner,
                payload={"vo2_ml_per_min_kg": value, "measurement_method": method},
            )
        )
    result = await call(factory, owner)
    assert result.records[0].value == Decimal(str(value))
    assert result.records[0].measurement_method == method


@pytest.mark.parametrize("count", [1000, 1001])
async def test_record_limit_precedes_invalid_json(health_db, monkeypatch, count):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add_all(
            [
                row(owner, f"vo2-{i:04}", **({"payload": {}} if count == 1001 else {}))
                for i in range(count)
            ]
        )
    if count == 1001:
        with pytest.raises(errors.Vo2MaxRecordLimitError) as caught:
            await call(factory, owner)
        assert caught.value.code == "VO2_MAX_RECORD_LIMIT_EXCEEDED"
    else:
        assert len((await call(factory, owner)).records) == 1000


async def test_point_measurement_must_not_have_end(health_db, monkeypatch):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, end_at=NOW + timedelta(minutes=1)))
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


async def test_current_raw_version_and_readonly_snapshot(health_db, monkeypatch):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    previous = NOW - timedelta(days=1)
    async with factory.begin() as db:
        db.add(
            ready(
                owner,
                last_success_at=previous,
                last_data_at=previous,
                last_status="fail",
            )
        )
        db.add(row(owner))
    original = repository.list_sync_states

    async def concurrent_update(db, member):
        states = await original(db, member)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(RawHealthRecord)
                .where(RawHealthRecord.end_user_id == owner)
                .values(
                    payload={"vo2_ml_per_min_kg": 47.5, "measurement_method": 1},
                    last_modified_at=NOW + timedelta(minutes=1),
                    ingested_at=NOW + timedelta(minutes=2),
                )
            )
            await writer.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(
                    last_status="success", last_success_at=NOW + timedelta(minutes=3)
                )
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", concurrent_update)
    result = await call(factory, owner)
    assert result.records[0].value == Decimal("42.125")
    assert result.records[0].ingested_at > result.data_status.last_success_at
    assert result.data_status.last_sync_status == "fail"
    monkeypatch.setattr(repository, "list_sync_states", original)
    following = await call(factory, owner)
    assert len(following.records) == 1
    assert following.records[0].record_id == result.records[0].record_id
    assert following.records[0].value == Decimal("47.5")
    assert following.records[0].last_modified_at == NOW + timedelta(minutes=1)
    assert following.records[0].ingested_at == NOW + timedelta(minutes=2)
    assert following.data_status.last_sync_status == "success"
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"
