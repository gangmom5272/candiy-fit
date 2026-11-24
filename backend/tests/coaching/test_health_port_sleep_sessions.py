"""전체 수면 세션 조회를 코칭 포트(get_sleep_sessions)로 잇는 변환과 sleep_sessions kind 근거 계약.

DB 조회 결과를 모의(mock) 응답으로 대체해 건강 조회 저장소 함수를 실행하고 합성 데이터만 쓴다.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.coaching.health_port import HealthQueryPort
from app.coaching.schemas import partition_evidence
from app.health import errors as health_errors
from app.health import repository
from app.health.calculations import sleep_record
from app.health.schemas import (
    KST,
    SLEEP_SESSION_LIMIT,
    Period,
    SleepDataStatus,
    SleepSessionsQuery,
    SleepSessionsResult,
)
from coach_ai import ports
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.security.gateway import ToolGateway
from tests.health.test_calculations import NOW, TODAY, calculate
from tests.health.test_schemas import USER
from tests.health.test_sleep_detail import detailed_night
from tests.health.test_sleep_sessions import query, stored

# 세션 팩토리는 조회 함수에 그대로 넘기기만 하므로 기본 테스트에서는 표식 객체로 대신한다
FACTORY = object()
DAY = TODAY - timedelta(days=1)
CTX = CoachContext(user_id=str(USER), conversation_id="c-1", request_id="r-1")
NAP = dict(
    session_id="nap",
    is_main=False,
    start_at=datetime(2026, 10, 21, 14, 0, tzinfo=KST),
    end_at=datetime(2026, 10, 21, 14, 40, tzinfo=KST),
    sleep_minutes=35,
    session_minutes=40,
    deep_minutes=None,
    rem_minutes=None,
    light_minutes=35,
    awake_minutes=5,
    avg_heart_rate=None,
    min_heart_rate=None,
    max_heart_rate=None,
    spo2_avg=None,
    spo2_min=None,
    spo2_max=None,
    spo2_count=None,
    spo2_low_count=None,
    spo2_low_times=None,
)


class FakeSessionsLookup:
    """전체 수면 세션 조회 함수 자리에 넣어 호출 인자를 기록하고 정해진 결과나 예외를 반환한다."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = []

    async def __call__(self, user_id, start, end, *, session_factory):
        self.calls.append((user_id, start, end, session_factory))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


async def stored_sessions(*rows):
    """저장소 함수(list_sleep_sessions)로 저장 행을 세션 모델로 읽는다."""
    result = Mock()
    result.mappings.return_value = list(rows)
    db = Mock(execute=AsyncMock(return_value=result))
    return await repository.list_sleep_sessions(db, query())


def sessions_result(sessions, start=DAY, end=DAY) -> SleepSessionsResult:
    return SleepSessionsResult(
        user_id=USER,
        period=Period(start=start, end=end),
        sessions=sessions,
        data_status=SleepDataStatus(
            last_sync_status="success",
            last_data_at=NOW - timedelta(minutes=7),
            last_success_at=NOW - timedelta(minutes=5),
            checked_at=NOW,
        ),
    )


def port(outcome) -> tuple[HealthQueryPort, FakeSessionsLookup]:
    lookup = FakeSessionsLookup(outcome)
    return HealthQueryPort(FACTORY, sleep_sessions_lookup=lookup), lookup


async def test_get_sleep_sessions_lookup_result_returns_owner_and_snapshot_without_user_id():
    found = sessions_result(await stored_sessions(stored(), stored(**NAP)))
    adapter, lookup = port(found)
    result = await adapter.get_sleep_sessions(str(USER), DAY, DAY)
    assert lookup.calls == [(USER, DAY, DAY, FACTORY)]
    assert isinstance(result, ports.SleepSessionsResult)
    assert result.user_id == "00000000-0000-4000-8000-000000000001"
    # JSON 으로 비교해 int·float 구분까지 같은지 확인한다
    expected = found.model_dump(mode="json", exclude={"user_id"})
    assert json.dumps(result.snapshot, sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )
    assert "user_id" not in result.snapshot and "kind" not in result.snapshot
    assert [(s.is_main, s.sleep_minutes) for s in result.sessions] == [
        (True, 400),
        (False, 35),
    ]


@pytest.mark.parametrize(
    ("count", "times", "expected"),
    [
        (None, None, None),
        (0, [], []),
        (3, None, None),
        (
            2,
            [datetime(2026, 10, 21, 2, 2, tzinfo=KST)] * 2,
            ["2026-10-20T17:02:00Z"] * 2,
        ),
    ],
)
async def test_get_sleep_sessions_keeps_null_empty_list_zero_and_utc_times(
    count, times, expected
):
    values = dict(spo2_low_count=count, spo2_low_times=times)
    if count is None:
        values.update(spo2_avg=None, spo2_min=None, spo2_max=None, spo2_count=None)
    adapter, _ = port(sessions_result(await stored_sessions(stored(**values))))
    result = await adapter.get_sleep_sessions(str(USER), DAY, DAY)
    [session] = result.snapshot["sessions"]
    assert (session["spo2_low_count"], session["spo2_low_times"]) == (count, expected)


@pytest.mark.parametrize(("start", "can_narrow"), [(DAY, True), (TODAY, False)])
async def test_get_sleep_sessions_limit_error_becomes_limit_error_from_backend_constant(
    start, can_narrow
):
    # 연동 문서 2.4: 이틀 이상이면 기간을 줄여 다시 조회할 수 있고, 하루면 할 수 없다
    adapter, _ = port(health_errors.SleepSessionLimitError())
    with pytest.raises(ports.HealthDataLimitError) as exc:
        await adapter.get_sleep_sessions(str(USER), start, TODAY)
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


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (health_errors.HealthDataNotReadyError, ports.HealthDataNotReadyError),
        (health_errors.HealthDataLookupError, ports.HealthDataUnavailableError),
        (health_errors.HealthDataForbiddenError, ports.HealthDataForbiddenError),
        (health_errors.HealthDataNotLinkedError, ports.HealthDataNotLinkedError),
    ],
)
async def test_get_sleep_sessions_health_error_raises_port_error_with_sleep_metric(
    raised, expected
):
    original = raised(("sleeps",))
    original.__cause__ = RuntimeError("SELECT spo2_low_times FROM sleep_session")
    adapter, _ = port(original)
    with pytest.raises(ports.HealthDataError) as exc:
        await adapter.get_sleep_sessions(str(USER), DAY, TODAY)
    assert type(exc.value) is expected and exc.value.metrics == ("sleep",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


async def test_get_sleep_sessions_backend_validation_error_becomes_lookup_failure():
    # 기간·인자는 도구 입력 단계에서 먼저 차단한다. 그래도 연동 쪽 입력 검증에 걸리면 조회 실패다
    with pytest.raises(ValidationError) as invalid:
        SleepSessionsQuery.model_validate(
            dict(user_id=USER, start=TODAY - timedelta(days=31), end=TODAY),
            context={"today": TODAY},
        )
    adapter, _ = port(invalid.value)
    with pytest.raises(ports.HealthDataUnavailableError) as exc:
        await adapter.get_sleep_sessions(str(USER), TODAY - timedelta(days=31), TODAY)
    assert exc.value.metrics == ("sleep",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


async def test_get_sleep_sessions_default_lookup_period_over_31_days_fails_before_database():
    # 기본 조회 함수는 DB 를 열기 전에 기간을 검증하므로 세션 팩토리를 쓰지 않는다
    with pytest.raises(ports.HealthDataUnavailableError):
        await HealthQueryPort(FACTORY).get_sleep_sessions(
            str(USER), TODAY - timedelta(days=31), TODAY
        )


@pytest.mark.parametrize(
    "user_id", ["", "not-a-uuid", "{00000000-0000-4000-8000-000000000001}"]
)
async def test_get_sleep_sessions_malformed_user_id_raises_internal_error(user_id):
    adapter, lookup = port(None)
    with pytest.raises(ValueError) as exc:
        await adapter.get_sleep_sessions(user_id, DAY, DAY)
    assert not isinstance(exc.value, ports.HealthDataError)
    assert lookup.calls == []


async def test_get_sleep_sessions_cancelled_lookup_propagates_cancellation():
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        HealthQueryPort(FACTORY, sleep_sessions_lookup=blocked).get_sleep_sessions(
            str(USER), DAY, DAY
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def sleep_metric_lookup(user_id, metric, start, end, *, session_factory):
    """주 수면 지표 조회. 같은 합성 주 수면으로 요청 기간의 공통 원형을 만든다."""
    return calculate(
        [sleep_record(detailed_night())], metric=metric, start=start, end=end
    )


async def test_backend_sessions_reach_conversation_validation_as_sleep_sessions_kind():
    # 백엔드 조회 → 포트 → snapshot → 대화 API 의 kind 검증
    found = sessions_result(await stored_sessions(stored(), stored(**NAP)))
    adapter = HealthQueryPort(
        FACTORY, sleep_metric_lookup, sleep_sessions_lookup=FakeSessionsLookup(found)
    )
    gateway = ToolGateway(adapter, TODAY, evidence_enabled={"sleep_sessions"})
    call = ToolCall(
        "c-1", "get_sleep_records", {"period": "days_ago:1", "include_non_main": True}
    )
    [reply] = await gateway.execute([call], CTX)
    message = json.loads(reply.content)
    assert message["evidence_ids"] == ["E1", "E2"]
    assert message["result"]["non_main_sleeps"] == ["10월 21일 낮 35분"]
    partition = partition_evidence([e.event_item() for e in reply.evidence])
    assert partition.skipped_metrics == ()
    base, detail = partition.valid
    assert (base.metric, detail.metric, detail.snapshot["kind"]) == (
        "sleeps",
        "sleeps",
        "sleep_sessions",
    )
    assert detail.snapshot == {
        "kind": "sleep_sessions",
        **found.model_dump(mode="json", exclude={"user_id"}),
    }
    # 주 수면 근거는 지금과 같은 지표 원형이다
    assert "kind" not in base.snapshot and base.snapshot["metric"] == "sleeps"


async def test_sample_sessions_of_local_environment_pass_conversation_validation():
    # local·test 환경의 합성 어댑터 결과도 같은 검증을 통과한다
    now = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)
    gateway = ToolGateway(
        sample_health_data(str(USER), now), TODAY, evidence_enabled={"sleep_sessions"}
    )
    call = ToolCall(
        "c-1",
        "get_sleep_records",
        {"period": "recent_days:14", "include_non_main": True},
    )
    [reply] = await gateway.execute([call], CTX)
    partition = partition_evidence([e.event_item() for e in reply.evidence])
    assert partition.skipped_metrics == () and len(partition.valid) == 2
    sessions = partition.valid[1].snapshot["sessions"]
    assert sum(not s["is_main"] for s in sessions) == 1
    assert any(s["spo2_low_times"] for s in sessions)
