from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, update

from app.health import errors, repository, service
from app.health.models import RawHealthRecord, SyncState
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_measurement_records import CASES

pytestmark = pytest.mark.db


def row(owner, record_id="synthetic-measurement", **changes):
    return RawHealthRecord(
        **(
            dict(
                end_user_id=owner,
                metric="weights",
                data_id=record_id,
                app="synthetic.app",
                start_at=NOW,
                end_at=None,
                payload={"weight_kg": 20.125},
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
                metric="weights",
                initial_sync_done=True,
                last_success_at=NOW,
                last_status="success",
            )
            | changes
        )
    )


async def call(factory, owner, metric="weights", start=TODAY, end=TODAY):
    return await service.get_measurement_records(
        owner, metric, start, end, session_factory=factory
    )


@pytest.mark.parametrize("metric,key,unit,maximum,value", CASES)
async def test_mapping_bounds_and_values_excluded_from_aggregates(
    health_db, monkeypatch, metric, key, unit, maximum, value
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner, metric=metric))
        for i, number in enumerate([0, maximum, value]):
            db.add(row(owner, str(i), metric=metric, payload={key: number}))
    result = await call(factory, owner, metric)
    assert result.metric == metric and result.unit == unit
    assert [r.value for r in result.records] == [0, maximum, Decimal(str(value))]
    assert [r["value"] for r in result.model_dump(mode="json")["records"]] == [
        0,
        maximum,
        value,
    ]


async def test_kst_period_owner_metric_order_and_projection(health_db, monkeypatch):
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
                    payload={"weight_kg": 20.125, "unrelated": ["x" * 1000] * 1000},
                )
            )
        db.add(
            row(owner, "before", start_at=start - timedelta(microseconds=1), payload={})
        )
        db.add(row(owner, "after", start_at=end, payload={}))
        db.add(row(owner, "metric", metric="oxygen_saturations", payload={}))
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
    assert "unrelated" not in result.model_dump_json()
    assert len(statements) == 2
    sql, parameters = statements[1]
    assert "LIMIT" in sql and 1001 in parameters
    assert "jsonb_typeof" in sql and "CASE" in sql
    assert "raw_health_record.payload," not in sql
    assert (
        await call(
            factory,
            owner,
            start=TODAY - timedelta(days=30),
            end=TODAY - timedelta(days=2),
        )
    ).records == []


@pytest.mark.parametrize("metric,key,unit,maximum,value", CASES)
@pytest.mark.parametrize(
    "invalid", [None, "42.5", True, -1, {}, [], "over_max", "missing"]
)
async def test_invalid_json_fails_instead_of_dropping_row(
    health_db, monkeypatch, metric, key, unit, maximum, value, invalid
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    payload = (
        {}
        if invalid == "missing"
        else {key: maximum + 0.01 if invalid == "over_max" else invalid}
    )
    async with factory.begin() as db:
        db.add(ready(owner, metric=metric))
        db.add(row(owner, "valid", metric=metric, payload={key: value}))
        db.add(row(owner, "invalid", metric=metric, payload=payload))
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner, metric)
    assert caught.value.metrics == ((metric,) if metric == "oxygen_saturations" else ())


@pytest.mark.parametrize("payload", [None, [], "wrong root"])
async def test_wrong_payload_root(health_db, monkeypatch, payload):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, payload=payload))
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


async def test_large_wrong_value_is_not_transferred(health_db, monkeypatch):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, payload={"weight_kg": ["x" * 1000] * 1000}))
    original = repository.list_measurement_records
    transferred = []

    async def observe(db, query):
        execute = db.execute

        async def capture(*args, **kwargs):
            result = (await execute(*args, **kwargs)).freeze()
            transferred.extend(result().mappings().all())
            return result()

        monkeypatch.setattr(db, "execute", capture)
        return await original(db, query)

    monkeypatch.setattr(repository, "list_measurement_records", observe)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)
    assert len(transferred) == 1
    assert all(not isinstance(v, (dict, list)) for v in transferred[0].values())
    assert len(str(dict(transferred[0]))) < 2000


@pytest.mark.parametrize("count", [1000, 1001])
async def test_record_limit_before_invalid_row(health_db, monkeypatch, count):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add_all(
            [
                row(owner, f"r-{i:04}", **({"payload": {}} if count == 1001 else {}))
                for i in range(count)
            ]
        )
    if count == 1001:
        with pytest.raises(errors.MeasurementRecordLimitError) as caught:
            await call(factory, owner)
        assert caught.value.code == "MEASUREMENT_RECORD_LIMIT_EXCEEDED"
    else:
        assert len((await call(factory, owner)).records) == 1000


@pytest.mark.parametrize("metric,key,unit,maximum,value", CASES)
@pytest.mark.parametrize("changes", [{"end_at": NOW}, {"app": ""}, {"data_id": ""}])
async def test_point_record_and_required_metadata(
    health_db, monkeypatch, metric, key, unit, maximum, value, changes
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner, metric=metric))
        db.add(row(owner, metric=metric, payload={key: value}, **changes))
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner, metric)
    assert caught.value.metrics == ((metric,) if metric == "oxygen_saturations" else ())


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
                    payload={"weight_kg": 21.25},
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
    assert result.records[0].value == Decimal("20.125")
    assert result.records[0].ingested_at > result.data_status.last_success_at
    assert result.data_status.last_sync_status == "fail"
    monkeypatch.setattr(repository, "list_sync_states", original)
    following = await call(factory, owner)
    assert len(following.records) == 1
    assert following.records[0].record_id == result.records[0].record_id
    assert following.records[0].value == Decimal("21.25")
    assert following.records[0].last_modified_at == NOW + timedelta(minutes=1)
    assert following.data_status.last_sync_status == "success"
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"


@pytest.mark.parametrize(
    "metric,key", [("oxygen_saturations", "percentage"), ("weights", "weight_kg")]
)
@pytest.mark.parametrize(
    "instant", ["9999-12-31T23:59:59-09:00", "0001-01-01T00:00:00+09:00"]
)
async def test_database_timestamp_decode_overflow_is_lookup_failure(
    health_db, monkeypatch, metric, key, instant
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner, metric=metric))
        db.add(row(owner, metric=metric, payload={key: 20.125}))
        await db.flush()
        # PostgreSQL은 Python datetime의 범위 밖 UTC 시각도 저장할 수 있다.
        await db.execute(
            text(
                "UPDATE raw_health_record "
                "SET last_modified_at = CAST(CAST(:instant AS text) AS timestamptz) "
                "WHERE end_user_id = :owner"
            ),
            {"instant": instant, "owner": owner},
        )
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner, metric)
    assert caught.value.reason == "LOOKUP_FAILED"
    assert caught.value.metrics == ((metric,) if metric == "oxygen_saturations" else ())
    async with factory.begin() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"
        await db.execute(
            update(RawHealthRecord)
            .where(RawHealthRecord.end_user_id == owner)
            .values(last_modified_at=NOW)
        )
    assert len((await call(factory, owner, metric)).records) == 1
