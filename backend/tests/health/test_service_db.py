from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text, update

from app.health import repository, service
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.models import ActivityDaily, HeartDaily, SleepSession, SyncState
from tests.health.test_calculations import NOW, TODAY, steps
from tests.health.test_heart import hours
from tests.health.test_sleep import night_values

pytestmark = pytest.mark.db


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: NOW)


async def seed(factory, owner):
    async with factory.begin() as session:
        for metric in ("steps", "heart_rates", "sleeps", "oxygen_saturations"):
            session.add(
                SyncState(
                    end_user_id=owner,
                    metric=metric,
                    initial_sync_done=True,
                    first_data_at=NOW - timedelta(days=13),
                    last_data_at=NOW - timedelta(minutes=7)
                    if metric in ("steps", "heart_rates")
                    else NOW - timedelta(days=1, hours=6, minutes=35),
                    last_success_at=NOW - timedelta(minutes=5),
                    last_status="success",
                )
            )
        for record in steps():
            session.add(
                ActivityDaily(end_user_id=owner, date=record.date, steps=record.value)
            )
        for day, avg, count, samples, low, high in [
            (19, 70, 14, 150, 50, 110),
            (20, 76, 15, 2000, 52, 140),
            (21, 95, 1, 8, 60, 130),
        ]:
            session.add(
                HeartDaily(
                    end_user_id=owner,
                    date=date(2026, 10, day),
                    hr_avg=Decimal(avg),
                    sample_count=samples,
                    hourly_hr=hours(count, avg),
                    hr_min=low,
                    hr_max=high,
                )
            )
        for day, minutes, oxygen in [
            (16, 418, "96.3"),
            (17, 441, "95.5"),
            (18, 368, None),
            (20, 340, "95.1"),
            (21, 412, "96.0"),
        ]:
            session.add(
                SleepSession(
                    end_user_id=owner,
                    data_id=f"main-{day}",
                    is_main=True,
                    session_minutes=480,
                    **night_values(day, minutes),
                    spo2_avg=Decimal(oxygen) if oxygen else None,
                    spo2_min=Decimal(90) if oxygen else None,
                    spo2_max=Decimal(99) if oxygen else None,
                )
            )
        session.add(
            SleepSession(
                end_user_id=owner,
                data_id="nap",
                is_main=False,
                session_minutes=30,
                **night_values(16, 20),
                spo2_avg=Decimal(80),
                spo2_min=Decimal(75),
                spo2_max=Decimal(85),
            )
        )


async def test_all_four_metrics_match_fixed_contract_in_postgres(health_db, clock):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    results = {}
    for metric in ("steps", "heart_rates", "sleeps", "oxygen_saturations"):
        results[metric] = await service.get_metric_result(
            owner, metric, date(2026, 10, 16), TODAY, session_factory=factory
        )
    steps_result = results["steps"]
    assert (
        steps_result.summary.avg,
        steps_result.summary.total,
        steps_result.in_progress.value,
    ) == (7465, 29860, 4210)
    assert steps_result.summary.completed_days == 6
    assert steps_result.summary.linked_from == date(2026, 10, 9)
    heart = results["heart_rates"]
    assert (
        heart.summary.avg == Decimal("73.83") and heart.detail.measured_max.value == 140
    )
    assert heart.detail.daily_ranges[-1].min is None
    sleep = results["sleeps"]
    assert sleep.summary.avg == Decimal("395.80") and len(sleep.detail.nights) == 5
    assert sleep.summary.missing_days == [
        date(2026, 10, 19)
    ] and sleep.summary.unsynced_days == [TODAY]
    oxygen = results["oxygen_saturations"]
    assert oxygen.summary.avg == Decimal("95.73") and oxygen.summary.days_with_data == 4
    for result in results.values():
        assert result.user_id == owner
        assert "user_id" not in result.to_public().model_dump(mode="json")
        assert len(result.days) == 7
        assert result.data_status.checked_at == NOW


@pytest.mark.parametrize(
    "ready,last_status,expected",
    [
        (False, "success", HealthDataNotReadyError),
        (False, "fail", HealthDataNotReadyError),
        (True, "success", None),
        (True, "fail", None),
    ],
)
async def test_readiness_and_normal_no_records_are_distinct(
    health_db, clock, ready, last_status, expected
):
    factory, (owner, _) = health_db
    async with factory.begin() as session:
        session.add(
            SyncState(
                end_user_id=owner,
                metric="steps",
                initial_sync_done=ready,
                last_status=last_status,
                last_success_at=NOW if ready else None,
                last_error="aggregate:RuntimeError" if last_status == "fail" else None,
            )
        )
    if expected:
        with pytest.raises(expected):
            await service.get_metric_result(
                owner, "steps", TODAY, TODAY, session_factory=factory
            )
    else:
        result = await service.get_metric_result(
            owner, "steps", TODAY, TODAY, session_factory=factory
        )
        assert result.summary.avg is None and result.days[0].reason == "BEFORE_LINK"
        assert result.data_status.last_sync_status == last_status


async def test_missing_state_does_not_imply_not_linked(health_db, clock):
    factory, (owner, _) = health_db
    with pytest.raises(HealthDataNotReadyError):
        await service.get_metric_result(
            owner, "steps", TODAY, TODAY, session_factory=factory
        )


@pytest.mark.parametrize("failure", ["aggregate:RuntimeError", "http_503"])
async def test_recent_failure_preserves_existing_values_and_success_boundary(
    health_db, clock, failure
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner, SyncState.metric == "steps")
            .values(last_status="fail", last_error=failure)
        )
    result = await service.get_metric_result(
        owner, "steps", date(2026, 10, 16), TODAY, session_factory=factory
    )
    assert result.summary.avg == 7465 and result.data_status.last_sync_status == "fail"
    assert result.data_status.last_data_at == NOW - timedelta(minutes=7)
    assert result.data_status.last_success_at == NOW - timedelta(minutes=5)
    assert "last_error" not in result.to_public().model_dump_json()


async def test_service_uses_readonly_repeatable_read_without_changing_pool_default(
    health_db, clock, monkeypatch
):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    original = repository.list_sync_states

    async def inspect_transaction(session, user_id):
        assert (
            await session.scalar(text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        return await original(session, user_id)

    monkeypatch.setattr(repository, "list_sync_states", inspect_transaction)
    await service.get_metric_result(
        owner, "steps", TODAY, TODAY, session_factory=factory
    )
    async with factory() as session:
        assert (
            await session.scalar(text("SHOW transaction_isolation")) == "read committed"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"


@pytest.mark.parametrize("main_count", [0, 2])
async def test_invalid_main_sleep_contract_is_lookup_failure(
    health_db, clock, main_count
):
    factory, (owner, _) = health_db
    async with factory.begin() as session:
        session.add(
            SyncState(
                end_user_id=owner,
                metric="sleeps",
                initial_sync_done=True,
                first_data_at=NOW - timedelta(days=13),
                last_data_at=NOW,
                last_success_at=NOW,
                last_status="success",
            )
        )
        for number in range(max(main_count, 1)):
            session.add(
                SleepSession(
                    end_user_id=owner,
                    data_id=f"bad-main-{number}",
                    is_main=bool(main_count),
                    session_minutes=480,
                    **night_values(21, 412),
                )
            )
    with pytest.raises(HealthDataLookupError):
        await service.get_metric_result(
            owner, "sleeps", date(2026, 10, 21), TODAY, session_factory=factory
        )
