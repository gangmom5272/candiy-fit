"""237: 합성 PostgreSQL의 저장값 → 서비스 → REST/AI 근거 검증."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import psycopg
import pytest
from candiy_pipeline.aggregator import aggregate_member, dates_to_aggregate
from candiy_pipeline.rules import Sample, heart_stats
from sqlalchemy import update

from app.coaching.health_port import HealthQueryPort
from app.health import service
from app.health.models import RawHealthRecord, SleepSession, SyncState
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_router_db import metric_api_db as metric_api_db
from tests.health.test_sleep_detail import DETAIL, sleep_row

pytestmark = pytest.mark.db


async def seed_detail(factory, owners, *, missing=False):
    async with factory.begin() as db:
        for owner in owners:
            for metric in ("sleeps", "oxygen_saturations"):
                db.add(
                    SyncState(
                        end_user_id=owner,
                        metric=metric,
                        initial_sync_done=True,
                        first_data_at=NOW - timedelta(days=365),
                        last_data_at=NOW,
                        last_success_at=NOW,
                        last_status="fail",
                    )
                )
        # 심박 상태가 미준비여도 준비된 수면을 반환한다.
        db.add(
            SyncState(
                end_user_id=owners[0],
                metric="heart_rates",
                initial_sync_done=False,
            )
        )
        row = sleep_row(end_user_id=owners[0])
        if missing:
            row.update(
                dict.fromkeys(
                    (
                        "sleep_minutes",
                        "unspecified_minutes",
                        "unknown_minutes",
                        "hr_avg",
                        "hr_min",
                        "hr_max",
                        "spo2_avg",
                        "spo2_min",
                        "spo2_max",
                        "spo2_count",
                        "spo2_low_count",
                    )
                )
            )
        db.add(SleepSession(data_id="main", **row))
        db.add(
            SleepSession(
                data_id="nap",
                **sleep_row(
                    end_user_id=owners[0],
                    is_main=False,
                    hr_avg=Decimal("100.0"),
                    hr_max=120,
                ),
            )
        )
        db.add(
            SleepSession(
                data_id="foreign",
                **sleep_row(
                    end_user_id=owners[1],
                    hr_avg=Decimal("110.0"),
                    hr_max=120,
                ),
            )
        )


@pytest.mark.parametrize("missing", [False, True])
async def test_stored_detail_through_rest_and_ai_with_366_day_contract(
    client,
    metric_api_db,
    missing,
):
    factory, owners, headers = metric_api_db
    await seed_detail(factory, owners, missing=missing)
    start = TODAY - timedelta(days=365)
    url = f"/api/v1/health/metrics/sleeps?start={start}&end={TODAY}"
    response = await client.get(url, headers=headers[0])
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["days"]) == 366
    assert data["dataStatus"]["lastSyncStatus"] == "fail"
    assert len(data["detail"]["nights"]) == 1
    detail = data["detail"]["nights"][0]
    assert detail["sessionMinutes"] == 480
    assert detail["avgHeartRate"] == (None if missing else 58.3)
    assert detail["spo2LowCount"] == (None if missing else 0)
    assert detail["unspecifiedMinutes"] == (None if missing else 0)
    assert detail["unknownMinutes"] is None
    assert detail["date"] == "2026-10-21"
    assert detail["startAt"] == "2026-10-20T14:00:00Z"
    assert data["summary"]["avg"] == (None if missing else 400)
    port = await HealthQueryPort(factory).get_metric_result(
        str(owners[0]),
        "sleep",
        start,
        TODAY,
    )
    stored = port.snapshot["detail"]["nights"][0]
    assert set(DETAIL) <= stored.keys()
    if not missing:
        expected = {
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in DETAIL.items()
        }
        assert stored.items() >= expected.items()
    response = await client.get(url, headers=headers[1])
    assert response.json()["data"]["detail"]["nights"][0]["avgHeartRate"] == 110
    too_long = url.replace(str(start), str(start - timedelta(days=1)))
    assert (await client.get(too_long, headers=headers[0])).status_code == 422


async def test_invalid_heart_fails_sleep_but_not_oxygen(client, metric_api_db):
    factory, owners, headers = metric_api_db
    await seed_detail(factory, owners)
    async with factory.begin() as db:
        await db.execute(
            update(SleepSession)
            .where(
                SleepSession.end_user_id == owners[0],
                SleepSession.is_main.is_(True),
            )
            .values(hr_avg=Decimal("NaN"))
        )
    query = "?start=2026-10-21&end=2026-10-22"
    response = await client.get(
        "/api/v1/health/metrics/sleeps" + query, headers=headers[0]
    )
    assert response.status_code == 503
    response = await client.get(
        "/api/v1/health/metrics/oxygen_saturations" + query, headers=headers[0]
    )
    assert response.status_code == 200
    assert response.json()["data"]["summary"]["avg"] == 95.7


@pytest.mark.parametrize("sample", [60.4, 60.6])
async def test_pipeline_rounded_heart_survives_postgres_rest_and_ai(
    client, metric_api_db, sample
):
    factory, owners, headers = metric_api_db
    await seed_detail(factory, owners)
    stats = heart_stats([Sample(NOW, sample)])
    async with factory.begin() as db:
        await db.execute(
            update(SleepSession)
            .where(
                SleepSession.end_user_id == owners[0], SleepSession.is_main.is_(True)
            )
            .values(
                hr_avg=Decimal(str(stats["avg"])),
                hr_min=stats["min"],
                hr_max=stats["max"],
            )
        )
    response = await client.get(
        "/api/v1/health/metrics/sleeps?start=2026-10-21&end=2026-10-22",
        headers=headers[0],
    )
    assert response.status_code == 200
    detail = response.json()["data"]["detail"]["nights"][0]
    assert (detail["avgHeartRate"], detail["minHeartRate"], detail["maxHeartRate"]) == (
        stats["avg"],
        stats["min"],
        stats["max"],
    )
    assert response.json()["data"]["summary"]["avg"] == 400
    result = await HealthQueryPort(factory).get_metric_result(
        str(owners[0]), "sleep", TODAY - timedelta(days=1), TODAY
    )
    assert result.snapshot["detail"]["nights"][0]["avg_heart_rate"] == stats["avg"]


async def test_sleep_only_readiness_and_empty_rows_are_distinct(health_db, monkeypatch):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add(
            SyncState(
                end_user_id=owner,
                metric="sleeps",
                initial_sync_done=True,
                last_success_at=NOW,
                last_data_at=NOW,
                last_status="success",
            )
        )
    result = await service.get_metric_result(
        owner,
        "sleeps",
        TODAY,
        TODAY,
        session_factory=factory,
    )
    assert result.detail.nights == []
    assert result.summary.avg is None


@pytest.mark.parametrize(
    "sample, old_average, old_extreme, expected",
    [(95.15, "95.1", "95.2", 95.2), (95.35, "95.4", "95.3", 95.4)],
)
async def test_reaggregate_legacy_spo2_from_raw_through_rest_and_ai(
    client, metric_api_db, sample, old_average, old_extreme, expected
):
    factory, owners, headers = metric_api_db
    await seed_detail(factory, owners)
    row = sleep_row()
    async with factory.begin() as db:
        db.add(
            RawHealthRecord(
                end_user_id=owners[0],
                metric="sleeps",
                data_id="main",
                app="test",
                start_at=row["start_at"],
                end_at=row["end_at"],
                payload={"sleep_duration_total": "PT6H40M"},
                last_modified_at=NOW,
                source_created_at=NOW,
                source_updated_at=NOW,
            )
        )
        for index in range(7):
            db.add(
                RawHealthRecord(
                    end_user_id=owners[0],
                    metric="oxygen_saturations",
                    data_id=f"oxygen-{index}",
                    app="test",
                    start_at=row["start_at"] + timedelta(minutes=index + 1),
                    payload={"percentage": sample},
                    last_modified_at=NOW,
                    source_created_at=NOW,
                    source_updated_at=NOW,
                )
            )
        await db.execute(
            update(SleepSession)
            .where(
                SleepSession.end_user_id == owners[0], SleepSession.is_main.is_(True)
            )
            .values(
                spo2_avg=Decimal(old_average),
                spo2_min=Decimal(old_extreme),
                spo2_max=Decimal(old_extreme),
                spo2_count=7,
            )
        )
    query = "?start=2026-10-21&end=2026-10-22"
    for metric in ("sleeps", "oxygen_saturations"):
        response = await client.get(
            f"/api/v1/health/metrics/{metric}{query}", headers=headers[0]
        )
        assert response.status_code == 503

    def reaggregate():
        # health_db가 검사한 전용 합성 DB만 사용하며 파이프라인 .env는 읽지 않는다.
        url = factory.kw["bind"].url.set(drivername="postgresql")
        with psycopg.connect(
            url.render_as_string(hide_password=False), autocommit=True
        ) as conn:
            dates = dates_to_aggregate(conn, owners[0], all_dates=True)
            assert row["date"] in dates
            assert aggregate_member(conn, owners[0], dates)["sleep_session"] == 1

    await asyncio.to_thread(reaggregate)
    response = await client.get(
        "/api/v1/health/metrics/sleeps" + query, headers=headers[0]
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"]["avg"] == 400
    detail = data["detail"]["nights"][0]
    assert (detail["spo2Avg"], detail["spo2Min"], detail["spo2Max"]) == (
        expected,
        expected,
        expected,
    )
    assert (detail["spo2Count"], detail["spo2LowCount"]) == (7, 0)
    response = await client.get(
        "/api/v1/health/metrics/oxygen_saturations" + query, headers=headers[0]
    )
    assert response.status_code == 200
    assert response.json()["data"]["summary"]["avg"] == expected
    result = await HealthQueryPort(factory).get_metric_result(
        str(owners[0]), "sleep", TODAY - timedelta(days=1), TODAY
    )
    assert result.snapshot["detail"]["nights"][0]["spo2_avg"] == expected
    response = await client.get(
        "/api/v1/health/metrics/sleeps" + query, headers=headers[1]
    )
    assert response.json()["data"]["detail"]["nights"][0]["spo2Avg"] == 95.7
