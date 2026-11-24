from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.health.test_body_daily_db import ready, row
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_router_db import metric_api_db as metric_api_db

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    "metric,unit,value",
    [
        ("weights", "kg", "70.25"),
        ("body_fats", "%", "22.35"),
        ("basal_metabolic_rates", "kcal/day", "1500.45"),
        ("heights", "m", "1.755"),
    ],
)
async def test_database_body_to_rest_keeps_owner_period_and_today(
    client, metric_api_db, metric, unit, value
):
    factory, owners, headers = metric_api_db
    yesterday = TODAY - timedelta(days=1)
    async with factory.begin() as db:
        db.add_all(
            [
                ready(owners[0], metric),
                ready(owners[1], metric),
                row(owners[0], metric, unit, value=Decimal(value)),
                row(
                    owners[0],
                    metric,
                    unit,
                    value=Decimal(value),
                    date=yesterday,
                    measured_at=NOW - timedelta(days=1),
                ),
                row(owners[1], metric, unit, value=Decimal("99")),
            ]
        )
    response = await client.get(
        f"/api/v1/health/metrics/{metric}?start={yesterday - timedelta(days=1)}&end={TODAY}",
        headers=headers[0],
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # 기존 body_daily NUMERIC의 저장 정밀도를 따르며 변환부에서 복원하지 않는다.
    stored_value = float(
        Decimal(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    )
    assert data["unit"] == unit
    assert data["summary"]["daysUsedForSummary"] == 1
    assert data["summary"]["dailyMin"]["value"] == stored_value
    assert data["summary"]["total"] is None
    assert data["days"][0]["reason"] == "NO_RECORD"
    assert data["days"][1]["value"] == stored_value
    assert data["inProgress"]["value"] == stored_value
    assert data["dataStatus"]["syncedUntil"] is None


@pytest.mark.parametrize(
    "metric", ["weights", "body_fats", "basal_metabolic_rates", "heights"]
)
async def test_new_member_body_is_not_ready_not_empty_success(
    client, metric_api_db, metric
):
    _, _, headers = metric_api_db
    response = await client.get(
        f"/api/v1/health/metrics/{metric}?start={TODAY}&end={TODAY}", headers=headers[0]
    )
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "DATA_NOT_READY",
        "metrics": [metric],
    }
