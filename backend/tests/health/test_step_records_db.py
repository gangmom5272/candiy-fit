from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text, update

from app.health import errors, repository, service
from app.health.models import RawHealthRecord, StepSnapshot
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_measurement_records_db import ready as measurement_ready
from tests.health.test_measurement_records_db import row as measurement_row

pytestmark = pytest.mark.db


def row(owner, record_id="synthetic-step", **changes):
    return measurement_row(
        owner, record_id, **(dict(metric="steps", payload={"count": 1200}) | changes)
    )


def ready(owner, **changes):
    return measurement_ready(owner, **(dict(metric="steps") | changes))


def version(owner, **changes):
    return StepSnapshot(
        **(
            dict(
                end_user_id=owner,
                data_id="synthetic-step",
                modified_at=NOW,
                step_count=1200,
                ingested_at=NOW,
            )
            | changes
        )
    )


async def call(factory, owner, history=False, **changes):
    args = (
        dict(user_id=owner, start=TODAY, end=TODAY, session_factory=factory) | changes
    )
    if history:
        return await service.get_step_record_history(record_id="synthetic-step", **args)
    return await service.get_step_records(**args)


async def test_list_filters_start_date_owner_metric_and_projects(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    lo = datetime(2026, 10, 21, 15, tzinfo=UTC)
    hi = lo + timedelta(days=1)
    async with factory.begin() as db:
        db.add(ready(owner))
        for identifier, start, end, source, count in [
            ("b", lo, None, "synthetic.one", 0),
            ("a", lo, hi + timedelta(hours=1), "synthetic.two", 1000000),
            ("last", hi - timedelta(microseconds=1), None, "synthetic.one", 1200.0),
        ]:
            db.add(
                row(
                    owner,
                    identifier,
                    start_at=start,
                    end_at=end,
                    app=source,
                    payload={"count": count, "unused": ["x" * 1000] * 1000},
                )
            )
        db.add(
            row(
                owner,
                "before",
                start_at=lo - timedelta(seconds=1),
                end_at=hi,
                payload={},
            )
        )
        db.add(row(owner, "after", start_at=hi, payload={}))
        db.add(row(owner, "other-metric", metric="weights", payload={}))
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
    assert [r.record_id for r in result.records] == ["a", "b", "last"]
    assert [r.step_count for r in result.records] == [1000000, 0, 1200]
    assert result.records[1].end_at is None
    assert result.records[0].source_app == "synthetic.two"
    assert all(r.date == TODAY for r in result.records)
    assert len(statements) == 2
    assert 1001 in statements[1][1]
    assert "raw_health_record.payload," not in statements[1][0]
    assert "unused" not in result.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"count": None},
        {"count": True},
        {"count": "1200"},
        {"count": -1},
        {"count": 1000001},
        {"count": 1200.5},
        {"count": 1e100},
        {"count": ["x" * 1000] * 1000},
        [],
        None,
    ],
)
async def test_bad_count_is_failure_not_cast_rounding_or_missing(
    health_db, monkeypatch, payload
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, payload=payload))
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner)
    assert caught.value.metrics == ("steps",)


@pytest.mark.parametrize("end", [NOW, NOW - timedelta(seconds=1)])
async def test_invalid_span_fails(health_db, monkeypatch, end):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, end_at=end))
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, owner)


async def test_history_filters_modified_date_and_preserves_versions(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    lo = datetime(2026, 10, 21, 15, tzinfo=UTC)
    hi = lo + timedelta(days=1)
    async with factory.begin() as db:
        db.add(ready(owner))
        # 원본 시작일이 이력 조회일 밖이어도 수정일에 해당하는 버전을 반환한다.
        db.add(row(owner, start_at=NOW - timedelta(days=2), payload={"count": 9999}))
        for i, (instant, count) in enumerate(
            [
                (lo - timedelta(microseconds=1), 100),
                (lo, 1200),
                (lo + timedelta(hours=1), 1200),
                (lo + timedelta(hours=2), 1000),
                (hi - timedelta(microseconds=1), 0),
                (hi, 8888),
            ]
        ):
            db.add(
                version(
                    owner,
                    modified_at=instant,
                    step_count=count,
                    ingested_at=NOW + timedelta(minutes=i),
                )
            )
        db.add(version(owner, data_id="another-record", step_count=-1))
        db.add(version(other, step_count=-1))
    result = await call(factory, owner, True)
    assert result.record.date == TODAY - timedelta(days=2)
    assert result.record.step_count == 9999
    assert result.record.end_at is None
    assert [v.step_count for v in result.versions] == [1200, 1200, 1000, 0]
    assert [v.modified_at for v in result.versions] == sorted(
        v.modified_at for v in result.versions
    )
    assert set(result.versions[0].model_dump()) == {
        "modified_at",
        "step_count",
        "ingested_at",
    }


@pytest.mark.parametrize(
    "mode", ["missing", "foreign", "orphan", "other_metric", "empty"]
)
async def test_history_existence_and_empty_are_distinct(health_db, monkeypatch, mode):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        if mode == "foreign":
            db.add(row(other))
            db.add(version(other))
        elif mode == "orphan":
            db.add(version(owner))
        elif mode == "other_metric":
            db.add(row(owner, metric="weights"))
        elif mode == "empty":
            db.add(row(owner))
    if mode == "empty":
        assert (await call(factory, owner, True)).versions == []
    else:
        with pytest.raises(errors.StepRecordNotFoundError):
            await call(factory, owner, True)


@pytest.mark.parametrize("count", [-1, 1000001])
async def test_invalid_stored_version(health_db, monkeypatch, count):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner))
        db.add(version(owner, step_count=count))
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner, True)
    assert caught.value.metrics == ("steps",)


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("count", [1000, 1001])
async def test_limits_before_value_validation(health_db, monkeypatch, history, count):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        if history:
            db.add(row(owner))
            db.add_all(
                [
                    version(
                        owner,
                        modified_at=NOW + timedelta(seconds=i),
                        step_count=-1 if count == 1001 else i,
                    )
                    for i in range(count)
                ]
            )
        else:
            db.add_all(
                [
                    row(
                        owner,
                        f"r-{i:04}",
                        payload={} if count == 1001 else {"count": i},
                    )
                    for i in range(count)
                ]
            )
    if count == 1001:
        expected = (
            errors.StepHistoryLimitError if history else errors.StepRecordLimitError
        )
        with pytest.raises(expected):
            await call(factory, owner, history)
    else:
        result = await call(factory, owner, history)
        assert len(result.versions if history else result.records) == 1000


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize(
    "instant", ["9999-12-31T23:59:59-09:00", "0001-01-01T00:00:00+09:00"]
)
async def test_database_decode_failure(health_db, monkeypatch, history, instant):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner))
        if history:
            db.add(version(owner))
        await db.flush()
        statement = (
            "UPDATE step_snapshot SET ingested_at = CAST(CAST(:instant AS text) AS timestamptz) WHERE end_user_id=:owner"
            if history
            else "UPDATE raw_health_record SET last_modified_at = CAST(CAST(:instant AS text) AS timestamptz) WHERE end_user_id=:owner"
        )
        await db.execute(text(statement), {"instant": instant, "owner": owner})
    with pytest.raises(errors.HealthDataLookupError) as caught:
        await call(factory, owner, history)
    assert caught.value.metrics == ("steps",)


async def test_history_same_snapshot_and_three_selects(health_db, monkeypatch):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(
            ready(owner, last_status="fail", last_success_at=NOW - timedelta(days=1))
        )
        db.add(row(owner))
        db.add(version(owner))
    original = repository.find_step_record

    async def concurrent_update(db, query):
        record = await original(db, query)
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        async with factory.begin() as writer:
            await writer.execute(
                update(RawHealthRecord)
                .where(RawHealthRecord.end_user_id == owner)
                .values(payload={"count": 2400})
            )
            writer.add(
                version(owner, modified_at=NOW + timedelta(minutes=1), step_count=2400)
            )
        return record

    monkeypatch.setattr(repository, "find_step_record", concurrent_update)
    result = await call(factory, owner, True)
    assert result.record.step_count == 1200
    assert [v.step_count for v in result.versions] == [1200]
    assert result.data_status.last_sync_status == "fail"
    monkeypatch.setattr(repository, "find_step_record", original)
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = await call(factory, owner, True)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 3
    assert result.record.step_count == 2400
    assert [v.step_count for v in result.versions] == [1200, 2400]
