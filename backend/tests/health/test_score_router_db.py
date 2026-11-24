from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from app.health import repository, router
from app.health.models import LifestyleScore, SyncState
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_router_db import metric_api_db as metric_api_db
from tests.health.test_score_contract import stored_score

pytestmark = pytest.mark.db
URL = "/api/v1/health/lifestyle-score"
INPUT_METRICS = ("steps", "exercise_sessions", "sleeps", "heart_rates")


async def insert_ready_states(factory, owner, *, missing=None, unready=None):
    async with factory.begin() as session:
        for metric in INPUT_METRICS:
            if metric != missing:
                session.add(
                    SyncState(
                        end_user_id=owner,
                        metric=metric,
                        initial_sync_done=metric != unready,
                        first_data_at=datetime(2026, 10, 12, tzinfo=UTC),
                        last_data_at=datetime(2026, 10, 15, tzinfo=UTC),
                        last_success_at=NOW,
                        last_status="fail",
                        last_error="http_503",
                    )
                )
        session.add(
            SyncState(
                end_user_id=owner,
                metric="weights",
                initial_sync_done=False,
                first_data_at=datetime(2026, 10, 9, 15, tzinfo=UTC),
                last_data_at=datetime(2026, 10, 19, 15, tzinfo=UTC),
            )
        )


@pytest.fixture
def score_api_db(metric_api_db, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(router, "datetime", Clock)
    return metric_api_db


async def insert_score(factory, owner, **changes):
    values = stored_score(
        **({"end_user_id": owner, "date": TODAY, "computed_at": NOW} | changes)
    )
    async with factory.begin() as session:
        session.add(LifestyleScore(**values))


async def test_real_jwt_member_and_score_read_keep_dates_and_users_separate(
    client, score_api_db
):
    factory, (owner, other), headers = score_api_db
    await insert_score(factory, owner)
    await insert_score(factory, other, total_score=99)
    await insert_score(factory, owner, date=date(2000, 1, 1), total_score=42)
    for auth, query, expected, progress in [
        (headers[0], "", 67, True),
        (headers[1], "", 99, True),
        (headers[0], "?date=2000-01-01", 42, False),
    ]:
        response = await client.get(URL + query, headers=auth)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert set(data) == {
            "date",
            "isInProgress",
            "total",
            "areas",
            "computedAt",
            "reason",
        }
        assert data["reason"] is None
        assert data["total"] == {"score": expected, "missingAreas": []}
        assert data["isInProgress"] is progress
        assert data["areas"]["activity"] == {
            "score": 80,
            "grade": 1,
            "steps": {"score": 48, "value": 5400},
            "exercise": {"score": 32, "minutes": 24},
        }
        assert data["computedAt"] == NOW.isoformat().replace("+00:00", "Z")
        assert str(owner) not in response.text and str(other) not in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("has_score", [True, False])
@pytest.mark.parametrize("signal", ["none", "unready", "mixed", "all404"])
async def test_stored_link_and_score_signals_have_distinct_http_results(
    client, score_api_db, signal, has_score
):
    factory, (owner, _), headers = score_api_db
    if has_score:
        await insert_score(factory, owner)
    async with factory.begin() as session:
        if signal != "none":
            session.add(
                SyncState(
                    end_user_id=owner,
                    metric="steps",
                    initial_sync_done=has_score and signal != "unready",
                    last_success_at=NOW if has_score else None,
                    last_status="fail",
                    last_error="http_503" if signal == "unready" else "http_404",
                )
            )
        if signal == "mixed":
            session.add(SyncState(end_user_id=owner, metric="weights", last_error=None))
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == (
        409 if signal == "all404" else 200 if has_score else 503
    ), response.text
    if signal == "all404":
        assert response.json()["error"]["code"] == "CANDIY_USER_NOT_LINKED"
    elif not has_score:
        assert response.json()["error"]["context"] == {
            "reason": "DATA_NOT_READY",
            "metrics": [],
        }
    assert "http_404" not in response.text and "last_error" not in response.text


@pytest.mark.parametrize("kind", ["partial", "all_null", "zero"])
async def test_partial_null_and_zero_scores_are_successful_saved_results(
    client, score_api_db, kind
):
    factory, (owner, _), headers = score_api_db
    changes = dict(
        total_score=80,
        sleep_score=None,
        sleep_grade=None,
        sleep_minutes=None,
        heart_score=None,
        heart_grade=None,
        sleep_hr_avg=None,
    )
    if kind == "all_null":
        changes.update(
            total_score=None,
            activity_score=None,
            activity_grade=None,
            steps_score=None,
            exercise_score=None,
            steps=0,
            exercise_minutes=None,
        )
    elif kind == "zero":
        changes.update(
            total_score=0,
            activity_score=0,
            activity_grade="care",
            steps_score=0,
            exercise_score=0,
            steps=800,
            exercise_minutes=0,
        )
    await insert_score(factory, owner, **changes)
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"]["score"] == changes["total_score"]
    assert data["total"]["missingAreas"] == (
        ["activity", "sleep", "heart"] if kind == "all_null" else ["sleep", "heart"]
    )
    assert data["areas"]["heart"]["baselineHrAvg"] == 58.0
    assert data["areas"]["heart"]["baselineNights"] == 20


async def test_invalid_stored_score_has_lookup_failed_not_data_not_ready(
    client, score_api_db
):
    factory, (owner, _), headers = score_api_db
    await insert_score(factory, owner, total_score=101)
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 503, response.text
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": [],
    }
    assert "101" not in response.text and "validation" not in response.text


@pytest.mark.parametrize("query", ["list_sync_states", "find_lifestyle_score"])
async def test_query_failure_is_a_whole_safe_error(
    client, score_api_db, monkeypatch, query
):
    _, _, headers = score_api_db
    monkeypatch.setattr(
        repository, query, AsyncMock(side_effect=OSError("synthetic-private"))
    )
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 503, response.text
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": [],
    }
    assert "data" not in response.json() and "synthetic-private" not in response.text


@pytest.mark.parametrize(
    "requested,reason",
    [
        ("2026-10-09", "BEFORE_LINK"),
        ("2026-10-10", "NO_RECORD"),
        ("2026-10-19", "NO_RECORD"),
        ("2026-10-20", "UNSYNCED"),
        ("", "UNSYNCED"),
    ],
)
async def test_ready_absent_day_returns_public_reason_and_all_null_details(
    client, score_api_db, requested, reason
):
    factory, (owner, _), headers = score_api_db
    await insert_ready_states(factory, owner)
    response = await client.get(
        URL + (f"?date={requested}" if requested else ""), headers=headers[0]
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["date"] == (requested or TODAY.isoformat())
    assert data["isInProgress"] is (not requested)
    assert data["reason"] == reason
    assert data["computedAt"] is None
    assert data["total"] == {
        "score": None,
        "missingAreas": ["activity", "sleep", "heart"],
    }
    assert data["areas"] == {
        "activity": {
            "score": None,
            "grade": None,
            "steps": {"score": None, "value": None},
            "exercise": {"score": None, "minutes": None},
        },
        "sleep": {"score": None, "grade": None, "sleepMinutes": None},
        "heart": {
            "score": None,
            "grade": None,
            "sleepHrAvg": None,
            "baselineHrAvg": None,
            "baselineNights": None,
            "checkWear": None,
        },
    }
    assert response.headers["cache-control"] == "no-store"
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LifestyleScore)
                .where(LifestyleScore.end_user_id == owner)
            )
            == 0
        )


@pytest.mark.parametrize("metric", INPUT_METRICS)
@pytest.mark.parametrize("kind", ["missing", "unready"])
async def test_each_input_is_required_and_other_member_cannot_supply_readiness(
    client, score_api_db, metric, kind
):
    factory, (owner, other), headers = score_api_db
    await insert_ready_states(factory, owner, **{kind: metric})
    await insert_ready_states(factory, other)
    response = await client.get(URL + "?date=2026-10-10", headers=headers[0])
    assert response.status_code == 503, response.text
    assert response.json()["error"]["context"] == {
        "reason": "DATA_NOT_READY",
        "metrics": [],
    }


async def test_ready_without_any_record_is_before_link(client, score_api_db):
    factory, (owner, _), headers = score_api_db
    await insert_ready_states(factory, owner)
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(first_data_at=None, last_data_at=None)
        )
    response = await client.get(URL, headers=headers[0])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["reason"] == "BEFORE_LINK"


async def test_all_404_still_precedes_ready_absent_day(client, score_api_db):
    factory, (owner, _), headers = score_api_db
    await insert_ready_states(factory, owner)
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(last_error="http_404", last_status="fail")
        )
    response = await client.get(URL + "?date=2026-10-10", headers=headers[0])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CANDIY_USER_NOT_LINKED"


@pytest.mark.parametrize("unready", [None, "sleeps"])
@pytest.mark.parametrize(
    "score_date,reason", [(TODAY, "UNSYNCED"), (date(2026, 10, 10), "NO_RECORD")]
)
async def test_late_score_and_readiness_commit_are_visible_only_next_request(
    client, score_api_db, monkeypatch, unready, score_date, reason
):
    factory, (owner, _), headers = score_api_db
    await insert_ready_states(factory, owner, unready=unready)
    original = repository.list_sync_states

    async def commit_after_state_read(session, user_id):
        states = await original(session, user_id)
        async with factory.begin() as writer:
            writer.add(
                LifestyleScore(
                    **stored_score(end_user_id=owner, date=score_date, computed_at=NOW)
                )
            )
            await writer.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner)
                .values(initial_sync_done=True, last_data_at=NOW, last_success_at=NOW)
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", commit_after_state_read)
    target = URL + f"?date={score_date}"
    before = await client.get(target, headers=headers[0])
    if unready:
        assert before.status_code == 503, before.text
        assert before.json()["error"]["context"]["reason"] == "DATA_NOT_READY"
    else:
        assert before.status_code == 200, before.text
        assert before.json()["data"]["reason"] == reason
        assert before.json()["data"]["computedAt"] is None
    monkeypatch.setattr(repository, "list_sync_states", original)
    after = await client.get(target, headers=headers[0])
    assert after.status_code == 200, after.text
    assert after.json()["data"]["reason"] is None
    assert after.json()["data"]["total"]["score"] == 67
    assert after.json()["data"]["computedAt"] == NOW.isoformat().replace("+00:00", "Z")
