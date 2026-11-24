from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from app.auth import tokens
from app.coaching.health_port import HealthQueryPort
from app.health import rest_schemas, service
from app.health.models import ActivityDaily, ExerciseSession, SyncState
from app.users.models import User
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_exercise import DAY, exercise
from tests.health.test_exercise_repository_db import stored_session
from tests.health.test_service_db import seed

pytestmark = pytest.mark.db
URL = "/api/v1/health/metrics/{metric}?start=2026-10-16&end=2026-10-22"


@pytest.fixture
async def metric_api_db(health_db, app, settings, monkeypatch):
    factory, owners = health_db
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    monkeypatch.setattr(service, "_now", lambda: NOW)

    class Clock(rest_schemas.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(rest_schemas, "datetime", Clock)
    app.state.sessionmaker = factory
    async with factory.begin() as db:
        db.add_all(
            [
                User(
                    id=owner,
                    social_type="GOOGLE",
                    social_user_id=str(owner),
                    display_name="합성회원",
                    birth_year=2000,
                    gender="FEMALE",
                    height_cm=Decimal("165.25"),
                    weight_kg=55,
                )
                for owner in owners
            ]
        )
    try:
        yield (
            factory,
            owners,
            [
                {
                    "Authorization": f"Bearer {tokens.issue_access_token(owner, settings, now=NOW).token}"
                }
                for owner in owners
            ],
        )
    finally:
        async with factory.begin() as db:
            await db.execute(delete(User).where(User.id.in_(owners)))


async def seed_metrics(factory, owner):
    await seed(factory, owner)
    excluded = exercise(
        start_at=exercise().start_at - timedelta(hours=1),
        end_at=exercise().start_at,
        duration_minutes=30,
        exercise_type=79,
        counts_as_exercise=False,
        avg_speed_mps=Decimal("0.50"),
        distance_m=Decimal("0.0"),
    )
    today = exercise(
        date=TODAY,
        start_at=NOW - timedelta(minutes=30),
        end_at=NOW,
        duration_minutes=30,
    )
    async with factory.begin() as db:
        db.add(
            SyncState(
                end_user_id=owner,
                metric="exercise_sessions",
                initial_sync_done=True,
                first_data_at=NOW - timedelta(days=3),
                last_data_at=NOW,
                last_success_at=NOW,
                last_status="fail",
            )
        )
        db.add_all(
            [
                stored_session(
                    owner, "counted", exercise(distance_m=Decimal("1234.5"))
                ),
                stored_session(owner, "excluded", excluded),
                stored_session(owner, "today", today),
            ]
        )
        for day, minutes, count in [(DAY, 60, 1), (TODAY, 30, 1)]:
            await db.execute(
                update(ActivityDaily)
                .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == day)
                .values(exercise_minutes=minutes, exercise_count=count)
            )


async def test_five_metrics_through_jwt_member_postgres_and_rest(client, metric_api_db):
    factory, (owner, _), (headers, _) = metric_api_db
    await seed_metrics(factory, owner)
    results = {}
    for metric in ("steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"):
        response = await client.get(URL.format(metric=metric), headers=headers)
        assert response.status_code == 200, response.text
        assert str(owner) not in response.text
        assert response.headers["cache-control"] == "no-store"
        data = response.json()["data"]
        assert data["metric"] == metric and len(data["days"]) == 7
        assert data["dataStatus"]["checkedAt"] == "2026-10-22T05:00:00Z"
        results[metric] = data
    assert results["steps"]["summary"]["avg"] == 7465
    assert results["steps"]["summary"]["total"] == 29860
    assert results["steps"]["inProgress"]["value"] == 4210
    assert results["heart_rates"]["summary"]["avg"] == 73.83
    assert results["oxygen_saturations"]["summary"]["avg"] == 95.73
    assert results["sleeps"]["summary"]["avg"] == 395.8
    assert len(results["sleeps"]["detail"]["nights"]) == 5
    exercise_data = results["exercises"]
    assert exercise_data["summary"]["total"] == 60
    assert exercise_data["inProgress"]["value"] == 30
    assert exercise_data["dataStatus"]["lastSyncStatus"] == "fail"
    sessions = exercise_data["detail"]["sessions"]
    assert [item["countsAsExercise"] for item in sessions] == [False, True, True]
    assert sessions[0]["avgSpeedMps"] == 0.5
    assert sessions[0]["typeName"] == "걷기"
    assert sessions[1]["avgSpeedMps"] is None
    assert [item["distanceM"] for item in sessions] == [0, 1234.5, None]


@pytest.mark.parametrize("distance", [None, Decimal("0.0"), Decimal("1234.5")])
async def test_daily_distance_including_slow_walk_preserves_session_distances(
    client, metric_api_db, distance
):
    factory, (owner, _), (headers, _) = metric_api_db
    await seed_metrics(factory, owner)
    slow_distance = Decimal("516.2")
    daily_distance = (
        distance if distance is not None else Decimal("0")
    ) + slow_distance
    async with factory.begin() as db:
        for data_id, stored_distance in (
            ("counted", distance),
            ("excluded", slow_distance),
        ):
            await db.execute(
                update(ExerciseSession)
                .where(
                    ExerciseSession.end_user_id == owner,
                    ExerciseSession.data_id == data_id,
                )
                .values(distance_m=stored_distance)
            )
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY)
            .values(exercise_distance_m=daily_distance)
        )

    # 일별 거리는 느린 걷기를 포함한다. 인정 운동 거리와의 차이는 조회 실패가 아니다.
    assert daily_distance != distance
    response = await client.get(
        f"/api/v1/health/metrics/exercises?start={DAY}&end={TODAY}", headers=headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    sessions = data["detail"]["sessions"]
    expected = [
        float(slow_distance),
        None if distance is None else float(distance),
        None,
    ]
    assert [item["distanceM"] for item in sessions] == expected
    assert [item["countsAsExercise"] for item in sessions] == [False, True, True]
    assert sessions[1]["date"] == DAY.isoformat()
    assert sessions[1]["endAt"] == "2026-10-21T15:30:00Z"
    assert data["summary"]["total"] == 60
    assert data["inProgress"]["value"] == 30

    port_result = await HealthQueryPort(factory).get_metric_result(
        str(owner), "exercise", DAY, TODAY
    )
    assert [item["distance_m"] for item in port_result.detail.sessions] == expected
    assert [
        item["distance_m"] for item in port_result.snapshot["detail"]["sessions"]
    ] == expected
    assert [item["counts_as_exercise"] for item in port_result.detail.sessions] == [
        False,
        True,
        True,
    ]
    assert port_result.summary.total == 60
    async with factory() as db:
        stored = (
            await db.execute(
                select(
                    ActivityDaily.exercise_distance_m,
                    ActivityDaily.exercise_minutes,
                    ActivityDaily.exercise_count,
                ).where(ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY)
            )
        ).one()
    assert stored == (daily_distance, 60, 1)


@pytest.mark.parametrize("counted", [True, False])
async def test_counted_aggregate_preserves_all_sessions_in_rest(
    client, metric_api_db, counted
):
    factory, (owner, _), (headers, _) = metric_api_db
    await seed_metrics(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            delete(ExerciseSession).where(ExerciseSession.end_user_id == owner)
        )
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner)
            .values(exercise_minutes=0, exercise_count=0)
        )
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY)
            .values(
                exercise_minutes=50 if counted else 0,
                exercise_count=2 if counted else 0,
            )
        )
        for index, minutes in enumerate((20, 30, 25, 20)):
            start = exercise().start_at - timedelta(hours=3 - index)
            db.add(
                stored_session(
                    owner,
                    f"session-{index}",
                    exercise(
                        start_at=start,
                        end_at=start + timedelta(minutes=minutes),
                        duration_minutes=minutes,
                        counts_as_exercise=counted and index < 2,
                    ),
                )
            )
    response = await client.get(URL.format(metric="exercises"), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    day = next(item for item in data["days"] if item["date"] == DAY.isoformat())
    assert day["value"] == data["summary"]["total"] == (50 if counted else 0)
    assert day["missing"] is False and day["reason"] is None
    sessions = data["detail"]["sessions"]
    assert [item["durationMinutes"] for item in sessions] == [20, 30, 25, 20]
    assert [item["countsAsExercise"] for item in sessions] == [
        counted,
        counted,
        False,
        False,
    ]


async def test_authenticated_users_cannot_read_each_others_metrics(
    client, metric_api_db
):
    factory, (owner, other), (headers, other_headers) = metric_api_db
    await seed_metrics(factory, owner)
    await seed_metrics(factory, other)
    async with factory.begin() as db:
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == other)
            .values(steps=123, exercise_minutes=0, exercise_count=0)
        )
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == other)
            .values(title="synthetic-other-only", counts_as_exercise=False)
        )
    for metric in ("steps", "exercises"):
        first = await client.get(URL.format(metric=metric), headers=headers)
        second = await client.get(URL.format(metric=metric), headers=other_headers)
        assert first.status_code == second.status_code == 200
        assert "synthetic-other-only" not in first.text
        totals = [
            response.json()["data"]["summary"]["total"] for response in (first, second)
        ]
        assert totals == ([29860, 615] if metric == "steps" else [60, 0])
    assert len(second.json()["data"]["detail"]["sessions"]) == 3
    assert second.json()["data"]["days"][-2]["missing"] is False


@pytest.mark.parametrize(
    "state,expected,reason",
    [
        ("missing", 503, "DATA_NOT_READY"),
        ("unready", 503, "DATA_NOT_READY"),
        ("ready_empty", 200, None),
        ("recent_failure", 200, None),
    ],
)
async def test_state_failures_and_successful_empty_response_remain_distinct(
    client, metric_api_db, state, expected, reason
):
    factory, (owner, _), (headers, _) = metric_api_db
    if state != "missing":
        async with factory.begin() as db:
            db.add(
                SyncState(
                    end_user_id=owner,
                    metric="steps",
                    initial_sync_done=state != "unready",
                    first_data_at=NOW - timedelta(days=13),
                    last_data_at=NOW,
                    last_success_at=NOW if state != "unready" else None,
                    last_status="fail" if state == "recent_failure" else "success",
                    last_error="synthetic-private",
                )
            )
    response = await client.get(URL.format(metric="steps"), headers=headers)
    assert response.status_code == expected
    assert "synthetic-private" not in response.text
    if reason:
        assert response.json()["error"]["context"] == {
            "reason": reason,
            "metrics": ["steps"],
        }
    else:
        data = response.json()["data"]
        assert data["summary"]["avg"] is data["summary"]["total"] is None
        assert data["summary"]["daysWithData"] == 0
        assert data["days"][0]["reason"] == "NO_RECORD"


async def test_deleted_member_is_unauthorized_even_with_remaining_health_data(
    client, metric_api_db
):
    factory, (owner, _), (headers, _) = metric_api_db
    await seed_metrics(factory, owner)
    async with factory.begin() as db:
        await db.execute(delete(User).where(User.id == owner))
    response = await client.get(URL.format(metric="steps"), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("legacy_field", ["exercise_minutes", "exercise_count"])
async def test_mismatched_exercise_aggregate_is_safe_lookup_error(
    client, metric_api_db, legacy_field
):
    factory, (owner, _), (headers, _) = metric_api_db
    await seed_metrics(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(ActivityDaily)
            .where(ActivityDaily.end_user_id == owner, ActivityDaily.date == DAY)
            .values(**{legacy_field: 90 if legacy_field == "exercise_minutes" else 2})
        )
    response = await client.get(URL.format(metric="exercises"), headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": ["exercises"],
    }


async def test_valid_token_for_unknown_member_does_not_imply_unlinked(
    client, metric_api_db, settings
):
    token = tokens.issue_access_token(uuid4(), settings, now=NOW).token
    response = await client.get(
        URL.format(metric="steps"), headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("ready", [False, True])
async def test_all_404_rejects_five_metrics_but_preserves_status_and_other_member(
    client, metric_api_db, ready
):
    factory, (owner, other), (headers, other_headers) = metric_api_db
    await seed_metrics(factory, owner)
    await seed_metrics(factory, other)
    async with factory.begin() as db:
        await db.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(
                initial_sync_done=ready,
                last_status="fail",
                last_error="http_404",
                last_success_at=NOW if ready else None,
            )
        )
    for metric in service.STATUS_METRICS:
        response = await client.get(URL.format(metric=metric), headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CANDIY_USER_NOT_LINKED"
        assert "http_404" not in response.text and "last_error" not in response.text
        assert str(owner) not in response.text
        assert response.headers["cache-control"] == "no-store"
        other_response = await client.get(
            URL.format(metric=metric), headers=other_headers
        )
        assert other_response.status_code == 200, other_response.text
    status = await client.get("/api/v1/health/data-status", headers=headers)
    assert status.status_code == 200, status.text
    assert "http_404" not in status.text
    assert all(item["ready"] is ready for item in status.json()["data"]["metrics"])


@pytest.mark.parametrize(
    "remaining_error", [None, "http_503", "aggregate:RuntimeError"]
)
@pytest.mark.parametrize("ready", [False, True])
async def test_one_non_404_state_keeps_existing_readiness_rules(
    client, metric_api_db, remaining_error, ready
):
    factory, (owner, _), (headers, _) = metric_api_db
    await seed_metrics(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(last_status="fail", last_error="http_404", initial_sync_done=ready)
        )
        # 요청 지표 밖의 상태도 전체 판정에 포함한다.
        db.add(
            SyncState(
                end_user_id=owner,
                metric="weights",
                initial_sync_done=False,
                last_status="fail" if remaining_error else "success",
                last_error=remaining_error,
            )
        )
    for metric in service.STATUS_METRICS:
        response = await client.get(URL.format(metric=metric), headers=headers)
        assert response.status_code == (200 if ready else 503), response.text
        assert "http_404" not in response.text and "aggregate:" not in response.text
        if ready:
            assert response.json()["data"]["dataStatus"]["lastSyncStatus"] == "fail"
        else:
            assert response.json()["error"]["context"] == {
                "reason": "DATA_NOT_READY",
                "metrics": [metric],
            }
