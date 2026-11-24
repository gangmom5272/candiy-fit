from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, update

from app.health import repository, service
from app.health.errors import HealthDataLookupError
from app.health.models import BodyDaily, SyncState
from tests.health.test_calculations import NOW, TODAY

pytestmark = pytest.mark.db


def row(owner, metric="weights", unit="kg", **changes):
    return BodyDaily(
        **(
            dict(
                end_user_id=owner,
                metric=metric,
                unit=unit,
                date=TODAY,
                value=Decimal("70.25"),
                measured_at=NOW,
                computed_at=NOW,
                data_id="synthetic-body",
            )
            | changes
        )
    )


def ready(owner, metric="weights"):
    return SyncState(
        end_user_id=owner,
        metric=metric,
        initial_sync_done=True,
        last_success_at=NOW,
        last_status="success",
    )


@pytest.mark.parametrize(
    "metric,unit,value",
    [
        ("weights", "kg", "70.25"),
        ("body_fats", "%", "22.35"),
        ("basal_metabolic_rates", "kcal/day", "1500.45"),
        ("heights", "m", "1.75"),
    ],
)
async def test_stored_values_owner_metric_period_isolation_and_two_selects(
    health_db, monkeypatch, metric, unit, value
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    start = TODAY - timedelta(days=1)
    async with factory.begin() as db:
        db.add(ready(owner, metric))
        # 다른 신체 지표는 미준비이며 잘못된 저장값도 이 조회를 막지 않는다.
        other_metric = "heights" if metric != "heights" else "weights"
        db.add(
            SyncState(end_user_id=owner, metric=other_metric, initial_sync_done=False)
        )
        db.add(row(owner, metric, unit, value=Decimal(value)))
        db.add(row(owner, other_metric, value=Decimal("-1")))
        db.add(row(other, metric, unit, value=Decimal("-1")))
        db.add(
            row(
                owner, metric, unit, date=start - timedelta(days=1), value=Decimal("-1")
            )
        )
    statements = []
    engine = factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = await service.get_body_daily(
            owner, metric, start, TODAY, session_factory=factory
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 2
    assert "raw_health_record" not in statements[1] and "data_id" not in statements[1]
    assert result.unit == unit and result.days[1].value == Decimal(value)
    assert result.days[0].reason == "NO_RECORD" and result.days[0].value is None
    assert result.days[1].measured_at == result.days[1].computed_at == NOW
    assert "data_id" not in result.days[1].model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"unit": "m"},
        {"value": Decimal("-1")},
        {"value": Decimal("0")},
        {"value": Decimal("NaN")},
        {"measured_at": NOW - timedelta(days=1)},
    ],
)
async def test_invalid_stored_row_is_lookup_failure(health_db, monkeypatch, changes):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner, **changes))
    with pytest.raises(HealthDataLookupError) as error:
        await service.get_body_daily(
            owner, "weights", TODAY, TODAY, session_factory=factory
        )
    assert error.value.metrics == ()


async def test_readonly_repeatable_snapshot_preserves_status_and_last_stored_measurement(
    health_db, monkeypatch
):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add(row(owner))
    original = repository.list_sync_states

    async def concurrent_write(db, member):
        states = await original(db, member)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(BodyDaily)
                .where(BodyDaily.end_user_id == owner)
                .values(
                    value=Decimal("70.50"),
                    measured_at=NOW + timedelta(minutes=1),
                    computed_at=NOW + timedelta(minutes=2),
                )
            )
            await writer.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(last_status="fail")
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", concurrent_write)
    result = await service.get_body_daily(
        owner, "weights", TODAY, TODAY, session_factory=factory
    )
    assert result.days[0].value == Decimal("70.25")
    assert result.data_status.last_sync_status == "success"
    monkeypatch.setattr(repository, "list_sync_states", original)
    following = await service.get_body_daily(
        owner, "weights", TODAY, TODAY, session_factory=factory
    )
    assert following.days[0].value == Decimal("70.50")
    assert following.days[0].measured_at == NOW + timedelta(minutes=1)
    assert following.days[0].computed_at == NOW + timedelta(minutes=2)
    assert following.data_status.last_sync_status == "fail"
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"
