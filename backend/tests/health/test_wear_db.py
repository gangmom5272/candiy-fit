from datetime import timedelta

import pytest
from sqlalchemy import select, text, update

from app.coaching.health_port import HealthQueryPort
from app.health import repository, service
from app.health.models import ActivityDaily, HeartDaily, SyncState
from app.health.rest_schemas import metric_response
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_heart import hours

pytestmark = pytest.mark.db


def ready(owner, metric, **changes):
    return SyncState(
        **(
            dict(
                end_user_id=owner,
                metric=metric,
                initial_sync_done=True,
                first_data_at=NOW - timedelta(days=10),
                last_data_at=NOW,
                last_success_at=NOW,
                last_status="success",
            )
            | changes
        )
    )


def heart_row(owner, day, count):
    return HeartDaily(
        end_user_id=owner,
        date=day,
        hr_avg=70 if count else None,
        hr_min=50 if count else None,
        hr_max=140 if count else None,
        sample_count=count,
        hourly_hr=hours(count),
    )


@pytest.mark.parametrize(
    "offset,count,heart_state,expected",
    [
        (-11, 10, {}, (None, None)),
        (-1, 10, None, (None, None)),
        (-1, 10, {"initial_sync_done": False}, (None, None)),
        (
            -1,
            10,
            {"last_status": "fail", "last_success_at": NOW - timedelta(days=1)},
            (None, None),
        ),
        (
            -2,
            9,
            {"last_status": "fail", "last_success_at": NOW - timedelta(days=1)},
            (9, False),
        ),
        (0, 9, {}, (9, None)),
        (0, 10, {}, (10, True)),
        (0, None, {}, (None, None)),
        (-1, 0, {}, (0, False)),
        (-1, 9, {}, (9, False)),
        (-1, 10, {}, (10, True)),
        (-1, 24, {}, (24, True)),
        (-1, None, {}, (0, False)),
    ],
)
async def test_postgres_wear_rules_and_owner_isolation(
    health_db, monkeypatch, offset, count, heart_state, expected
):
    factory, (owner, other) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    day = TODAY + timedelta(days=offset)
    async with factory.begin() as db:
        db.add(ready(owner, "steps"))
        db.add(ActivityDaily(end_user_id=owner, date=day, steps=5000))
        db.add(heart_row(other, day, 24))
        if heart_state is not None:
            db.add(ready(owner, "heart_rates", **heart_state))
        if count is not None:
            db.add(heart_row(owner, day, count))
    result = await service.get_metric_result(
        owner, "steps", day, day, session_factory=factory
    )
    assert (result.days[0].wear_hours, result.days[0].is_well_worn) == expected
    public = metric_response(result.to_public()).model_dump(mode="json", by_alias=True)
    assert (public["days"][0]["wearHours"], public["days"][0]["isWellWorn"]) == expected
    port = await HealthQueryPort(factory).get_metric_result(
        str(owner), "steps", day, day
    )
    assert (
        port.snapshot["days"][0]["wear_hours"],
        port.snapshot["days"][0]["is_well_worn"],
    ) == expected
    if offset == -1:
        assert result.summary.avg == 5000
        assert result.summary.total == 5000
    async with factory() as db:
        stored = await db.scalar(
            select(HeartDaily).where(
                HeartDaily.end_user_id == owner, HeartDaily.date == day
            )
        )
        assert stored is None if count is None else len(stored.hourly_hr) == count


@pytest.mark.parametrize(
    "count,expected", [(None, (None, None)), (9, (9, None)), (10, (10, True))]
)
async def test_unsynced_past_day_in_postgres(health_db, monkeypatch, count, expected):
    factory, (owner, _) = health_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    day = TODAY - timedelta(days=2)
    async with factory.begin() as db:
        db.add_all(
            [
                ready(owner, metric, last_data_at=NOW - timedelta(days=2))
                for metric in ("steps", "heart_rates")
            ]
        )
        if count is not None:
            db.add(heart_row(owner, day, count))
    result = await service.get_metric_result(
        owner, "steps", day, day, session_factory=factory
    )
    assert (result.days[0].wear_hours, result.days[0].is_well_worn) == expected


async def test_wear_uses_same_readonly_snapshot_as_metric(health_db, monkeypatch):
    factory, (owner, _) = health_db
    day = TODAY - timedelta(days=1)
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as db:
        db.add_all(
            [
                ready(owner, "steps"),
                ready(owner, "heart_rates"),
                heart_row(owner, day, 9),
            ]
        )
    original = repository.list_hourly_hearts
    changed = False

    async def concurrent_update(db, user_id, start, end):
        nonlocal changed
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        if not changed:
            async with factory.begin() as writer:
                await writer.execute(
                    update(HeartDaily)
                    .where(HeartDaily.end_user_id == owner)
                    .values(hourly_hr=hours(10))
                )
            changed = True
        return await original(db, user_id, start, end)

    monkeypatch.setattr(repository, "list_hourly_hearts", concurrent_update)
    before = await service.get_metric_result(
        owner, "steps", day, day, session_factory=factory
    )
    after = await service.get_metric_result(
        owner, "steps", day, day, session_factory=factory
    )
    assert (before.days[0].wear_hours, before.days[0].is_well_worn) == (9, False)
    assert (after.days[0].wear_hours, after.days[0].is_well_worn) == (10, True)
