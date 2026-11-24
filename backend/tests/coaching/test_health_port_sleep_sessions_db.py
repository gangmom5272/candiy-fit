"""합성 전용 DB 의 전체 수면 세션을 코칭 포트로 받아 sleep_sessions kind 근거가 대화 API 검증을 통과하는지 확인한다."""

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import update

from app.coaching.health_port import HealthQueryPort
from app.coaching.schemas import partition_evidence
from app.health import service
from app.health.models import SleepSession
from app.health.schemas import KST, SLEEP_SESSION_LIMIT
from coach_ai import ports
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.security.gateway import ToolGateway
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_service_db import seed
from tests.health.test_sleep_sessions_db import ready, row

pytestmark = pytest.mark.db

START = date(2026, 10, 16)
# 21일에 깬 주 수면(20일 23:00 에 잠듦)의 90% 미만 기록 두 번
LOW_TIMES = [
    datetime(2026, 10, 21, 2, 2, tzinfo=KST),
    datetime(2026, 10, 21, 3, 40, tzinfo=KST),
]


@pytest.fixture
def clock(monkeypatch):
    # 합성 데이터가 절대 날짜라서 건강 조회 서비스의 현재 시각을 2026-10-22 14:00(KST)로 고정한다
    monkeypatch.setattr(service, "_now", lambda: NOW)


async def test_stored_sessions_reach_conversation_validation_as_sleep_sessions_kind(
    health_db, clock
):
    factory, (owner, other) = health_db
    await seed(factory, owner)
    async with factory.begin() as db:
        await db.execute(
            update(SleepSession)
            .where(SleepSession.end_user_id == owner, SleepSession.data_id == "main-21")
            .values(spo2_count=25, spo2_low_count=2, spo2_low_times=LOW_TIMES)
        )
        db.add(row(other, "foreign"))
    adapter = HealthQueryPort(factory)

    # 백엔드 조회 → 포트 → snapshot
    direct = await service.get_sleep_sessions(
        owner, START, TODAY, session_factory=factory
    )
    result = await adapter.get_sleep_sessions(str(owner), START, TODAY)
    expected = direct.model_dump(mode="json", exclude={"user_id"})
    assert result.user_id == str(owner)
    assert json.dumps(result.snapshot, sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )
    sessions = result.snapshot["sessions"]
    assert [s["session_id"] for s in sessions] == [
        "main-16",
        "nap",
        "main-17",
        "main-18",
        "main-20",
        "main-21",
    ]
    assert sessions[-1]["spo2_low_times"] == [
        "2026-10-20T17:02:00Z",
        "2026-10-20T18:40:00Z",
    ]

    # 처리 함수 → 근거 → 대화 API 의 kind 검증
    gateway = ToolGateway(adapter, TODAY, evidence_enabled={"sleep_sessions"})
    ctx = CoachContext(user_id=str(owner), conversation_id="c-1", request_id="r-1")
    call = ToolCall(
        "c-1",
        "get_sleep_records",
        {"period": "recent_days:7", "include_non_main": True},
    )
    [reply] = await gateway.execute([call], ctx)
    message = json.loads(reply.content)
    assert message["evidence_ids"] == ["E1", "E2"]
    assert message["result"]["non_main_sleeps"] == ["10월 15일 밤 20분"]
    assert message["result"]["spo2_low"] == (
        "산소포화도를 기록한 1일 중 90% 미만으로 기록된 날 1일, 기록 2회 "
        "(깬 날 기준 10월 21일 수면 새벽 2시 2분·3시 40분)"
    )
    partition = partition_evidence([e.event_item() for e in reply.evidence])
    assert partition.skipped_metrics == ()
    base, detail = partition.valid
    assert (base.metric, detail.metric) == ("sleeps", "sleeps")
    assert detail.snapshot == {"kind": "sleep_sessions", **expected}
    # 주 수면 근거는 지표 원형이고, 실제 백엔드 원형처럼 nights 에 90% 미만 기록 시각이 있다
    assert "kind" not in base.snapshot
    assert base.snapshot["detail"]["nights"][-1]["spo2_low_times"] == [
        "2026-10-20T17:02:00Z",
        "2026-10-20T18:40:00Z",
    ]


@pytest.mark.parametrize(
    ("end", "can_narrow"), [(TODAY, True), (TODAY - timedelta(days=1), False)]
)
async def test_stored_sessions_over_limit_become_port_limit_error(
    health_db, clock, end, can_narrow
):
    # 저장 행은 모두 21일에 깬 세션이다. 연동 문서 2.4: 기간이 이틀 이상이면 줄여서 다시 조회할 수 있고, 하루면 할 수 없다
    factory, (owner, _) = health_db
    async with factory.begin() as db:
        db.add(ready(owner))
        db.add_all(
            [
                row(owner, f"session-{i:04}", is_main=i == 0)
                for i in range(SLEEP_SESSION_LIMIT + 1)
            ]
        )
    with pytest.raises(ports.HealthDataLimitError) as exc:
        await HealthQueryPort(factory).get_sleep_sessions(
            str(owner), TODAY - timedelta(days=1), end
        )
    error = exc.value
    assert (
        error.code,
        error.source,
        error.can_narrow,
        error.limit,
        error.limit_unit,
    ) == (
        "SLEEP_SESSION_LIMIT_EXCEEDED",
        "sleep_sessions",
        can_narrow,
        SLEEP_SESSION_LIMIT,
        "sessions",
    )
    assert error.__cause__ is None and error.__context__ is None
