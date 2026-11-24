from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update

from app.health import repository, router
from app.health.models import ActivityDaily, HeartDaily, SleepSession, SyncState
from tests.health.test_exercise import exercise
from tests.health.test_exercise_repository_db import stored_session
from tests.health.test_router_db import metric_api_db as metric_api_db
from tests.health.test_timeline_contract import TODAY, spec_example
from tests.health.test_timeline_repository_db import stored_sleep
from tests.health.test_timeline_router import QUERY, URL
from tests.health.test_timeline_service_db import seed_ready

pytestmark = pytest.mark.db


@pytest.fixture
def timeline_api_db(metric_api_db, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock)
    return metric_api_db


async def test_complete_v11_example_through_jwt_postgres_and_endpoint(
    client, timeline_api_db
):
    factory, (owner, other), headers = timeline_api_db
    await seed_ready(factory, owner)
    await seed_ready(factory, other)
    example = spec_example()
    day = example["data"]["days"][0]
    target = date.fromisoformat(day["date"])
    sleep = day["mainSleep"]
    session = day["exerciseSegments"][0]
    async with factory.begin() as db:
        db.add(
            ActivityDaily(
                end_user_id=owner, date=target, hourly_steps=day["hourlySteps"]
            )
        )
        db.add(ActivityDaily(end_user_id=other, date=target, hourly_steps=[999] * 24))
        db.add(
            HeartDaily(
                end_user_id=owner,
                date=target,
                hourly_hr={
                    str(i): h | {"count": 42}
                    for i, h in enumerate(day["hourlyHeartRates"])
                    if h is not None
                },
            )
        )
        db.add(
            SleepSession(
                end_user_id=owner,
                data_id="main",
                date=target,
                is_main=True,
                session_minutes=360,
                start_at=datetime.fromisoformat(sleep["startAt"]),
                end_at=datetime.fromisoformat(sleep["endAt"]),
                sleep_timeline=[
                    {"start": s["startAt"], "end": s["endAt"], "stage": s["stage"]}
                    for s in reversed(day["sleepSegments"])
                ],
            )
        )
        db.add(stored_sleep(owner, day=target, data_id="nap", is_main=False))
        db.add(
            stored_session(
                owner,
                "counted-walk",
                exercise(
                    date=target,
                    start_at=datetime.fromisoformat(session["startAt"]),
                    end_at=datetime.fromisoformat(session["endAt"]),
                    exercise_type=session["exerciseType"],
                    title=session["title"],
                    counts_as_exercise=True,
                ),
            )
        )
        db.add(
            stored_session(
                owner,
                "uncounted-walk",
                exercise(
                    date=target,
                    start_at=datetime.fromisoformat(session["startAt"])
                    - timedelta(hours=2),
                    end_at=datetime.fromisoformat(session["endAt"])
                    - timedelta(hours=2),
                    exercise_type=79,
                    counts_as_exercise=False,
                ),
            )
        )
    query = f"?start={target}&end={target}"
    response = await client.get(URL + query, headers=headers[0])
    assert response.status_code == 200, response.text
    assert response.json() == example
    assert response.headers["cache-control"] == "no-store"
    assert str(owner) not in response.text and str(other) not in response.text
    other_response = await client.get(URL + query, headers=headers[1])
    assert other_response.status_code == 200
    other_day = other_response.json()["data"]["days"][0]
    assert other_day["hourlySteps"] == [999] * 24
    assert other_day["mainSleep"] is None and other_day["exerciseSegments"] == []


@pytest.mark.parametrize("counted", [True, False])
async def test_cross_midnight_sessions_are_filtered_without_clipping_or_duplication(
    client, timeline_api_db, counted
):
    factory, (owner, _), headers = timeline_api_db
    await seed_ready(factory, owner)
    previous = TODAY - timedelta(days=1)
    async with factory.begin() as db:
        db.add(stored_sleep(owner))
        db.add(stored_sleep(owner, day=TODAY + timedelta(days=1), data_id="tomorrow"))
        db.add(
            stored_session(
                owner,
                "crossing",
                exercise(
                    date=previous,
                    start_at="2026-10-25T23:30:00+09:00",
                    end_at="2026-10-26T00:30:00+09:00",
                    exercise_type=9999,
                    counts_as_exercise=counted,
                ),
            )
        )
    response = await client.get(
        URL + f"?start={previous}&end={TODAY}", headers=headers[0]
    )
    assert response.status_code == 200, response.text
    earlier, current = response.json()["data"]["days"]
    assert earlier["mainSleep"] is None and earlier["sleepSegments"] == []
    assert current["mainSleep"] == {
        "startAt": "2026-10-25T14:00:00Z",
        "endAt": "2026-10-25T21:00:00Z",
    }
    assert current["sleepSegments"] == [] and current["exerciseSegments"] == []
    expected = [
        {
            "startAt": "2026-10-25T14:30:00Z",
            "endAt": "2026-10-25T15:30:00Z",
            "exerciseType": 9999,
            "typeName": "기타 운동",
            "title": None,
        }
    ]
    assert earlier["exerciseSegments"] == (expected if counted else [])


async def test_ready_empty_31_days_and_zero_steps_are_distinct(client, timeline_api_db):
    factory, (owner, _), headers = timeline_api_db
    await seed_ready(factory, owner)
    start = TODAY - timedelta(days=30)
    async with factory.begin() as db:
        db.add(ActivityDaily(end_user_id=owner, date=TODAY, hourly_steps=[0] * 24))
        db.add(
            ActivityDaily(
                end_user_id=owner,
                date=TODAY - timedelta(days=1),
                steps=100,
                hourly_steps=None,
            )
        )
    response = await client.get(URL + f"?start={start}&end={TODAY}", headers=headers[0])
    assert response.status_code == 200, response.text
    days = response.json()["data"]["days"]
    assert [d["date"] for d in days] == [
        (start + timedelta(days=i)).isoformat() for i in range(31)
    ]
    assert [d["isInProgress"] for d in days] == [False] * 30 + [True]
    assert days[-1]["hourlySteps"] == [0] * 24
    for day in days[:-1]:
        assert day["hourlySteps"] is None and day["hourlyHeartRates"] is None
        assert (
            day["mainSleep"] is None
            and day["sleepSegments"] == []
            and day["exerciseSegments"] == []
        )


@pytest.mark.parametrize(
    "signal,code,reason,metrics",
    [
        (
            "no_states",
            503,
            "DATA_NOT_READY",
            ["steps", "heart_rates", "sleeps", "exercises"],
        ),
        ("missing", 503, "LOOKUP_FAILED", ["exercises"]),
        ("unready", 503, "DATA_NOT_READY", ["exercises"]),
        ("no_success", 503, "LOOKUP_FAILED", ["exercises"]),
        ("all404", 409, None, None),
    ],
)
async def test_stored_readiness_and_link_signals_map_to_public_errors(
    client, timeline_api_db, signal, code, reason, metrics
):
    factory, (owner, _), headers = timeline_api_db
    if signal != "no_states":
        await seed_ready(factory, owner)
        async with factory.begin() as db:
            if signal == "missing":
                await db.execute(
                    delete(SyncState).where(
                        SyncState.end_user_id == owner,
                        SyncState.metric == "exercise_sessions",
                    )
                )
            elif signal == "all404":
                await db.execute(
                    update(SyncState)
                    .where(SyncState.end_user_id == owner)
                    .values(
                        initial_sync_done=False,
                        last_status="fail",
                        last_error="http_404",
                    )
                )
            else:
                values = (
                    {"initial_sync_done": False}
                    if signal == "unready"
                    else {"last_success_at": None}
                )
                await db.execute(
                    update(SyncState)
                    .where(
                        SyncState.end_user_id == owner,
                        SyncState.metric == "exercise_sessions",
                    )
                    .values(**values)
                )
    response = await client.get(URL + QUERY, headers=headers[0])
    assert response.status_code == code, response.text
    error = response.json()["error"]
    assert error["code"] == (
        "CANDIY_USER_NOT_LINKED" if code == 409 else "HEALTH_DATA_UNAVAILABLE"
    )
    if reason:
        assert error["context"] == {"reason": reason, "metrics": metrics}
    assert "exercise_sessions" not in response.text and "http_404" not in response.text


@pytest.mark.parametrize(
    "kind,metric",
    [
        ("steps", "steps"),
        ("heart", "heart_rates"),
        ("sleep", "sleeps"),
        ("query", "exercises"),
    ],
)
async def test_invalid_storage_or_mid_query_failure_is_not_partial_success(
    client, timeline_api_db, monkeypatch, kind, metric
):
    factory, (owner, _), headers = timeline_api_db
    await seed_ready(factory, owner)
    async with factory.begin() as db:
        if kind == "steps":
            db.add(ActivityDaily(end_user_id=owner, date=TODAY, hourly_steps=[1] * 23))
        elif kind == "heart":
            db.add(
                HeartDaily(
                    end_user_id=owner,
                    date=TODAY,
                    hourly_hr={"25": {"avg": 70, "min": 60, "max": 80}},
                )
            )
        elif kind == "sleep":
            db.add(stored_sleep(owner, sleep_timeline=[{"stage": "light"}]))
        else:
            monkeypatch.setattr(
                repository,
                "list_exercise_sessions",
                AsyncMock(side_effect=OSError("synthetic-private")),
            )
    response = await client.get(URL + QUERY, headers=headers[0])
    assert response.status_code == 503, response.text
    assert "data" not in response.json()
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": [metric],
    }
    assert "synthetic-private" not in response.text
