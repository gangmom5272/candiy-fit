from datetime import timedelta
from decimal import Decimal

import pytest

from app.health.models import ActivityDaily, HeartDaily, SleepSession, SyncState
from app.health.repository import list_daily_records, list_sync_states
from app.health.schemas import HealthQuery
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_heart import hours
from tests.health.test_sleep import night, night_values

pytestmark = pytest.mark.db


def query(user, metric="steps"):
    return HealthQuery.model_validate(
        dict(user_id=user, metric=metric, start=TODAY - timedelta(days=1), end=TODAY),
        context={"today": TODAY},
    )


async def test_member_period_and_all_metric_states_are_isolated(health_db):
    factory, (owner, other) = health_db
    async with factory.begin() as session:
        session.add_all(
            [
                ActivityDaily(
                    end_user_id=owner, date=TODAY - timedelta(days=1), steps=0
                ),
                ActivityDaily(end_user_id=owner, date=TODAY, steps=None),
                ActivityDaily(
                    end_user_id=owner, date=TODAY - timedelta(days=2), steps=7000
                ),
                ActivityDaily(end_user_id=other, date=TODAY, steps=99999),
                SyncState(
                    end_user_id=owner,
                    metric="steps",
                    initial_sync_done=True,
                    last_data_at=NOW,
                    last_error="http_404",
                ),
                SyncState(
                    end_user_id=owner,
                    metric="weights",
                    initial_sync_done=False,
                    first_data_at=NOW - timedelta(days=90),
                    last_data_at=NOW + timedelta(hours=1),
                ),
                SyncState(end_user_id=other, metric="steps", initial_sync_done=True),
            ]
        )
    async with factory() as session:
        records = await list_daily_records(session, query(owner))
        states = await list_sync_states(session, owner)
    assert [(row.date, row.value) for row in records] == [
        (TODAY - timedelta(days=1), 0),
        (TODAY, None),
    ]
    assert {state.metric for state in states} == {"steps", "weights"}
    assert all(state.end_user_id == owner for state in states)
    assert {state.metric: state.last_error for state in states} == {
        "steps": "http_404",
        "weights": None,
    }


async def test_postgres_jsonb_numeric_and_main_sleep_are_preserved(health_db):
    factory, (owner, other) = health_db
    main = night(22, None)
    async with factory.begin() as session:
        session.add(
            HeartDaily(
                end_user_id=owner,
                date=TODAY,
                hr_avg=Decimal("72.3"),
                sample_count=20,
                hourly_hr=hours(2),
                hr_min=50,
                hr_max=100,
            )
        )
        for user, data_id, is_main in [
            (owner, "main", True),
            (owner, "nap", False),
            (other, "foreign", True),
        ]:
            session.add(
                SleepSession(
                    end_user_id=user,
                    data_id=data_id,
                    is_main=is_main,
                    session_minutes=480 if is_main else 30,
                    **night_values(22, None),
                    spo2_avg=Decimal("95.7"),
                    spo2_min=Decimal(92),
                    spo2_max=Decimal(99),
                )
            )
    async with factory() as session:
        heart = await list_daily_records(session, query(owner, "heart_rates"))
        sleep = await list_daily_records(session, query(owner, "sleeps"))
        oxygen = await list_daily_records(session, query(owner, "oxygen_saturations"))
    assert heart[0].weight == 2 and heart[0].value == Decimal("72.3")
    assert (
        len(sleep) == 1
        and sleep[0].value is None
        and sleep[0].night.end_at == main.end_at
    )
    assert len(oxygen) == 1 and oxygen[0].value == Decimal("95.7")
