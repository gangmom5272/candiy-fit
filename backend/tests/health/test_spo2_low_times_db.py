"""실제 PostgreSQL 저장값에서 REST 및 AI 포트까지 네 상태를 보존한다."""

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.coaching.health_port import HealthQueryPort
from app.health import service
from app.health.models import SleepSession
from tests.health.test_calculations import TODAY
from tests.health.test_router_db import metric_api_db as metric_api_db
from tests.health.test_sleep_detail import detailed_night
from tests.health.test_sleep_detail_db import seed_detail

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    "count,size", [(None, None), (0, None), (2, None), (0, 0), (6, 6)]
)
async def test_database_to_rest_and_full_sessions(client, metric_api_db, count, size):
    factory, owners, headers = metric_api_db
    await seed_detail(factory, owners, missing=count is None)
    time = detailed_night().start_at + timedelta(minutes=5)
    times = None if size is None else [time] * size
    async with factory.begin() as db:
        await db.execute(
            update(SleepSession)
            .where(SleepSession.end_user_id == owners[0])
            .values(spo2_low_count=count, spo2_low_times=times)
        )
    start = TODAY - timedelta(days=1)
    response = await client.get(
        f"/api/v1/health/metrics/sleeps?start={start}&end={TODAY}",
        headers=headers[0],
    )
    assert response.status_code == 200
    expected = None if size is None else ["2026-10-20T14:05:00Z"] * size
    nights = response.json()["data"]["detail"]["nights"]
    assert len(nights) == 1
    assert nights[0]["spo2LowTimes"] == expected
    assert nights[0]["spo2LowCount"] == count
    sessions = await service.get_sleep_sessions(
        owners[0], start, TODAY, session_factory=factory
    )
    assert len(sessions.sessions) == 2
    assert all(
        row["spo2_low_times"] == expected
        for row in sessions.model_dump(mode="json")["sessions"]
    )
    port = await HealthQueryPort(factory).get_metric_result(
        str(owners[0]), "sleep", start, TODAY
    )
    assert port.snapshot["detail"]["nights"][0]["spo2_low_times"] == expected
