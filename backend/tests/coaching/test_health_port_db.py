"""합성 전용 DB 에서 어댑터 결과를 건강 조회 함수의 직접 호출 결과와 비교한다."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.coaching.health_port import HealthQueryPort
from app.health import service
from app.health.models import ExerciseSession, SyncState
from coach_ai import ports
from tests.coaching.test_health_port import SPEC_IN_PROGRESS, SPEC_NO_ROW, set_clock
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_exercise import DAY
from tests.health.test_exercise_service_db import seed_exercises
from tests.health.test_score_router_db import insert_ready_states, insert_score
from tests.health.test_service_db import seed

pytestmark = pytest.mark.db

START = date(2026, 10, 16)
# health-query-service.md 의 기대 평균. 두 조회가 함께 비어 있어도 같다고 판정하지 않도록 따로 확인한다
AVERAGES = {
    "steps": 7465,
    "heart_rate": 73.83,
    "oxygen_saturation": 95.73,
    "sleep": 395.8,
}


@pytest.fixture
def clock(monkeypatch):
    # 합성 데이터가 절대 날짜라서 건강 조회 서비스의 현재 시각을 2026-10-22 14:00(KST)로 고정한다
    monkeypatch.setattr(service, "_now", lambda: NOW)


@pytest.mark.parametrize("distance", [None, Decimal("0.0"), Decimal("1234.5")])
async def test_stored_exercise_distance_reaches_port_with_owner_isolation(
    health_db, clock, distance
):
    factory, (owner, other) = health_db
    await seed_exercises(factory, owner, other)
    async with factory.begin() as db:
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == owner, ExerciseSession.date == DAY)
            .values(distance_m=distance)
        )
        await db.execute(
            update(ExerciseSession)
            .where(ExerciseSession.end_user_id == other)
            .values(distance_m=Decimal("9999.9"))
        )
    result = await HealthQueryPort(factory).get_metric_result(
        str(owner), "exercise", DAY, TODAY
    )
    assert result.user_id == str(owner)
    assert [item["distance_m"] for item in result.detail.sessions] == [
        None if distance is None else float(distance),
        None,
    ]
    assert result.summary.total == 60


@pytest.mark.parametrize(
    ("metric", "public"),
    [
        ("steps", "steps"),
        ("heart_rate", "heart_rates"),
        ("oxygen_saturation", "oxygen_saturations"),
        ("sleep", "sleeps"),
    ],
)
async def test_get_metric_result_synthetic_member_matches_direct_call(
    health_db, clock, metric, public
):
    factory, (owner, _) = health_db
    await seed(factory, owner)

    result = await HealthQueryPort(factory).get_metric_result(
        str(owner), metric, START, TODAY
    )
    direct = await service.get_metric_result(
        owner, public, START, TODAY, session_factory=factory
    )

    assert result.user_id == str(owner) == str(direct.user_id)
    assert result.snapshot == direct.to_public().model_dump(mode="json")
    assert result.summary.avg == AVERAGES[metric]


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rate", "oxygen_saturation", "sleep"]
)
async def test_stored_404_is_converted_to_ai_unlinked_error(health_db, clock, metric):
    factory, (owner, _) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(last_status="fail", last_error="http_404")
        )
    with pytest.raises(ports.HealthDataNotLinkedError) as caught:
        await HealthQueryPort(factory).get_metric_result(
            str(owner), metric, START, TODAY
        )
    assert caught.value.metrics == (metric,)
    assert "http_404" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.fixture
def score_clock(monkeypatch):
    # 합성 점수가 절대 날짜라서 어댑터의 현재 시각을 2026-10-22 14:00(KST)로 고정한다
    set_clock(monkeypatch, NOW)


async def test_get_lifestyle_score_stored_row_returns_owner_row(health_db, score_clock):
    factory, (owner, other) = health_db
    await insert_score(factory, owner)
    await insert_score(factory, other, total_score=99)

    result = await HealthQueryPort(factory).get_lifestyle_score(str(owner), TODAY)

    assert result.user_id == str(owner)
    assert result.snapshot == SPEC_IN_PROGRESS | {
        "date": "2026-10-22",
        "computed_at": "2026-10-22T05:00:00Z",
    }


# insert_ready_states 의 기록 시작일은 10-10(KST), 동기화 기준일은 10-20(KST)이다
@pytest.mark.parametrize(
    ("day", "reason"),
    [
        (date(2026, 10, 9), "BEFORE_LINK"),
        (date(2026, 10, 15), "NO_RECORD"),
        (TODAY, "UNSYNCED"),
    ],
)
async def test_get_lifestyle_score_absent_row_returns_reason_without_other_member_row(
    health_db, score_clock, day, reason
):
    factory, (owner, other) = health_db
    await insert_ready_states(factory, owner)
    await insert_score(factory, other, date=day, total_score=99)

    result = await HealthQueryPort(factory).get_lifestyle_score(str(owner), day)

    assert result.user_id == str(owner)
    assert result.snapshot == SPEC_NO_ROW | {
        "date": day.isoformat(),
        "is_in_progress": day == TODAY,
        "reason": reason,
    }


@pytest.mark.parametrize("kind", ["missing", "unready"])
async def test_get_lifestyle_score_input_not_ready_raises_data_not_ready(
    health_db, score_clock, kind
):
    factory, (owner, other) = health_db
    await insert_ready_states(factory, owner, **{kind: "sleeps"})
    # 다른 회원의 준비 상태는 이 회원의 준비 여부에 쓰지 않는다
    await insert_ready_states(factory, other)

    with pytest.raises(ports.HealthDataNotReadyError) as caught:
        await HealthQueryPort(factory).get_lifestyle_score(
            str(owner), date(2026, 10, 15)
        )

    assert caught.value.metrics == ()
    assert caught.value.__context__ is None


async def test_get_lifestyle_score_stored_404_raises_unlinked_error(
    health_db, score_clock
):
    factory, (owner, _) = health_db
    await insert_ready_states(factory, owner)
    await insert_score(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner)
            .values(last_status="fail", last_error="http_404")
        )

    # 저장 점수가 있어도 전체 http_404 판정이 먼저다
    with pytest.raises(ports.HealthDataNotLinkedError) as caught:
        await HealthQueryPort(factory).get_lifestyle_score(str(owner), TODAY)

    assert caught.value.metrics == ()
    assert "http_404" not in str(caught.value)
    assert caught.value.__context__ is None
