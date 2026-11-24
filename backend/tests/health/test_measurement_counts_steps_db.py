from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text, update

from app.coaching.health_port import HealthQueryPort
from app.health import repository, service
from app.health.models import ActivityDaily, HeartDaily, SyncState
from app.health.rest_schemas import timeline_response
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_router_db import metric_api_db as metric_api_db

pytestmark = pytest.mark.db


async def test_steps_only_readiness_owner_isolation_and_readonly_snapshot(
    health_db, monkeypatch
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    start = TODAY - timedelta(days=2)
    async with factory.begin() as db:
        db.add(
            SyncState(
                end_user_id=owner,
                metric="steps",
                initial_sync_done=True,
                last_success_at=NOW,
                last_status="fail",
            )
        )
        db.add(SyncState(end_user_id=owner, metric="sleeps", initial_sync_done=False))
        db.add(ActivityDaily(end_user_id=owner, date=start, hourly_steps=[0] * 24))
        db.add(
            ActivityDaily(end_user_id=owner, date=TODAY, hourly_steps=list(range(24)))
        )
        db.add(ActivityDaily(end_user_id=other, date=TODAY, hourly_steps=[999] * 24))
    original = repository.list_sync_states

    async def concurrent_write(db, member):
        states = await original(db, member)
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(ActivityDaily)
                .where(
                    ActivityDaily.end_user_id == owner,
                    ActivityDaily.date == TODAY,
                )
                .values(hourly_steps=[100] * 24)
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", concurrent_write)
    result = await service.get_hourly_steps_range(
        owner,
        start,
        TODAY,
        start_hour=8,
        end_hour=10,
        session_factory=factory,
    )
    assert [day.values for day in result.days] == [[0, 0], None, [8, 9]]
    assert [day.total for day in result.days] == [0, None, 17]
    assert result.total is None and result.data_status.last_sync_status == "fail"
    async with factory() as db:
        assert await db.scalar(text("SHOW transaction_read_only")) == "off"


@pytest.mark.parametrize(
    "average,low,high", [(70, 60, 80), (60.4, 60, 60), (59.6, 60, 60)]
)
async def test_heart_counts_survive_postgres_rest_ai_and_timeline(
    client, metric_api_db, average, low, high
):
    factory, (owner, other), headers = metric_api_db
    async with factory.begin() as db:
        for member in (owner, other):
            for metric in ("steps", "heart_rates", "sleeps", "exercise_sessions"):
                db.add(
                    SyncState(
                        end_user_id=member,
                        metric=metric,
                        initial_sync_done=True,
                        first_data_at=NOW - timedelta(days=2),
                        last_data_at=NOW,
                        last_success_at=NOW,
                        last_status="success",
                    )
                )
            db.add(
                HeartDaily(
                    end_user_id=member,
                    date=TODAY,
                    sample_count=7 if member == owner else 99,
                    hr_avg=Decimal(str(average)),
                    hr_min=low,
                    hr_max=high,
                    hourly_hr={
                        "8": {
                            "avg": average,
                            "min": low,
                            "max": high,
                            "count": 7 if member == owner else 99,
                        }
                    },
                )
            )
    response = await client.get(
        f"/api/v1/health/metrics/heart_rates?start={TODAY}&end={TODAY}",
        headers=headers[0],
    )
    assert response.status_code == 200
    assert response.json()["data"]["detail"]["dailyRanges"][0]["sampleCount"] == 7
    assert response.json()["data"]["inProgress"]["value"] == average
    snapshot = await HealthQueryPort(factory).get_metric_result(
        str(owner),
        "heart_rate",
        TODAY,
        TODAY,
    )
    assert snapshot.snapshot["detail"]["daily_ranges"][0]["sample_count"] == 7
    assert snapshot.snapshot["in_progress"]["value"] == average
    timeline = await service.get_timeline(
        owner, TODAY, TODAY, today=TODAY, session_factory=factory
    )
    hour = timeline_response(timeline).model_dump(mode="json")["days"][0][
        "hourlyHeartRates"
    ][8]
    assert hour == {"avg": average, "min": low, "max": high, "sampleCount": 7}
