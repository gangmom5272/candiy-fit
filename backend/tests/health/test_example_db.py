from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.health import service
from app.health.models import (
    ActivityDaily,
    ExerciseSession,
    HeartDaily,
    SleepSession,
    SyncState,
)
from scripts.health_query_example import run_example
from tests.health.test_calculations import NOW

pytestmark = pytest.mark.db


async def test_direct_call_example_matches_contract_and_cleans_its_rows(
    health_db, monkeypatch
):
    factory, _ = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory() as session:
        before = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (
                ActivityDaily,
                ExerciseSession,
                HeartDaily,
                SleepSession,
                SyncState,
            )
        ]
    output = await run_example(factory, NOW)
    results = {row["metric"]: row for row in output["results"]}
    assert results["steps"]["summary"]["avg"] == 7465
    assert results["steps"]["in_progress"]["value"] == 4210
    assert results["heart_rates"]["summary"]["avg"] == float(Decimal("73.83"))
    assert results["sleeps"]["summary"]["avg"] == 395.8
    assert results["oxygen_saturations"]["summary"]["avg"] == 95.73
    assert results["exercises"]["summary"]["avg"] == 6
    assert results["exercises"]["summary"]["total"] == 30
    assert results["exercises"]["in_progress"]["value"] == 15
    assert len(results["exercises"]["detail"]["sessions"]) == 3
    sessions = results["exercises"]["detail"]["sessions"]
    assert [item["counts_as_exercise"] for item in sessions] == [True, False, True]
    assert sessions[1]["avg_speed_mps"] == 0.5
    assert results["exercises"]["days"][-2]["value"] == 0
    assert results["exercises"]["days"][-2]["missing"] is False
    assert all("user_id" not in row for row in output["results"])
    assert output["not_ready"] == dict(
        code="HEALTH_DATA_UNAVAILABLE", reason="DATA_NOT_READY", metrics=["steps"]
    )
    async with factory() as session:
        after = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (
                ActivityDaily,
                ExerciseSession,
                HeartDaily,
                SleepSession,
                SyncState,
            )
        ]
    assert before == after
