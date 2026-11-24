"""주 수면이 아닌 잠까지 조회하는 수면 기록 (include_non_main)과 전체 수면 세션 포트."""

import json
from datetime import UTC, date, datetime

import pytest

from coach_ai.adapters.memory import SyncState
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.evidence import KindEvidenceDraft
from coach_ai.agent.executor import EvidenceDraft, ToolCall
from coach_ai.ports import (
    HealthDataForbiddenError,
    HealthDataLimitError,
    HealthDataNotLinkedError,
    HealthDataPort,
    HealthDataUnavailableError,
    ResultContractError,
    SleepSessionsResult,
)
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.handlers import get_sleep_records
from coach_ai.tools.schemas import GetSleepRecords
from tests.fakes import KST, NOW, TODAY, D, FakeHealthData, at, night, request, sample_port

CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
# 백엔드 전체 수면 세션 조회 결과의 세션 칸 (app.health.schemas.SleepSessionDetail, sleep-sessions-237.md)
SESSION_KEYS = [
    "date", "start_at", "end_at", "sleep_minutes", "deep_minutes", "rem_minutes", "light_minutes", "awake_minutes",
    "session_minutes", "unspecified_minutes", "unknown_minutes", "avg_heart_rate", "min_heart_rate", "max_heart_rate",
    "spo2_avg", "spo2_min", "spo2_max", "spo2_count", "spo2_low_count", "spo2_low_times", "session_id", "is_main",
    "computed_at",
]  # fmt: skip
# 주 수면이 아닌 잠은 잠든 시각 구분과 실제 수면 시간만 한 줄로 쓴다 (13일 14:00~14:40, 40분 잔 낮잠)
NAP = "10월 13일 낮 40분"
NON_MAIN_NOTE = "주 수면이 아닌 잠이고 summary 에 들어가지 않는다."
NO_NON_MAIN = "기간 안에 주 수면이 아닌 잠 기록이 없다."


def call(period="last_7_days", **args) -> ToolCall:
    return ToolCall("c1", "get_sleep_records", {"period": period, **args})


class SessionsPort(FakeHealthData):
    """전체 수면 세션 조회만 바꾼다. error 가 있으면 그 예외를, owner 가 있으면 그 소유자의 결과를 반환한다."""

    def __init__(self, base: FakeHealthData, *, error: Exception | None = None, owner: str | None = None) -> None:
        super().__init__(**vars(base))
        self.error = error
        self.owner = owner

    async def get_sleep_sessions(self, user_id, start, end):
        if self.error is not None:
            raise self.error
        result = await super().get_sleep_sessions(user_id, start, end)
        return result if self.owner is None else SleepSessionsResult(self.owner, result.snapshot)


def test_memory_adapter_satisfies_port_with_sleep_sessions():
    assert isinstance(FakeHealthData(), HealthDataPort)
    assert hasattr(HealthDataPort, "get_sleep_sessions")


async def test_memory_sessions_hold_main_and_non_main_sleeps_in_backend_shape():
    port = sample_port()
    result = await port.get_sleep_sessions("u1", D(2026, 10, 12), D(2026, 10, 15))
    assert isinstance(result, SleepSessionsResult) and result.user_id == "u1"
    assert result.snapshot["period"] == {"start": "2026-10-12", "end": "2026-10-15"}
    sessions = result.snapshot["sessions"]
    # 깬 날, 잠든 시각 순이다. 13일은 주 수면(00:35)과 낮잠(14:00)이 함께 있다
    assert [(s["date"], s["start_at"], s["is_main"]) for s in sessions] == [
        ("2026-10-12", "2026-10-11T14:50:00Z", True),
        ("2026-10-13", "2026-10-12T15:35:00Z", True),
        ("2026-10-13", "2026-10-13T05:00:00Z", False),
        ("2026-10-14", "2026-10-13T16:10:00Z", True),
    ]
    assert [list(s) for s in sessions] == [SESSION_KEYS] * 4
    assert list(result.snapshot) == ["period", "sessions", "data_status"]
    assert list(result.snapshot["data_status"]) == ["last_sync_status", "last_data_at", "last_success_at", "checked_at"]
    assert "user_id" not in json.dumps(result.snapshot)
    assert [(s.date, s.is_main, s.sleep_minutes) for s in result.sessions] == [
        (D(2026, 10, 12), True, 450), (D(2026, 10, 13), True, 368), (D(2026, 10, 13), False, 40),
        (D(2026, 10, 14), True, None),
    ]  # fmt: skip


async def test_memory_sessions_keep_null_empty_list_zero_and_utc_times():
    port = sample_health_data("u1", datetime(2026, 10, 22, 14, 0, tzinfo=KST))
    sessions = (await port.get_sleep_sessions("u1", D(2026, 10, 9), D(2026, 10, 22))).snapshot["sessions"]
    by_day = {(s["date"], s["is_main"]): s for s in sessions}
    # 90% 미만 기록이 있는 수면은 시각 수가 횟수와 같고, 0회인 수면은 빈 목록, 산소포화도 기록이 없는 수면은 null 이다
    low = by_day[("2026-10-20", True)]
    assert low["spo2_low_count"] == 3 and len(low["spo2_low_times"]) == 3
    assert all(t.endswith("Z") and len(t) == 20 for t in low["spo2_low_times"])
    assert (by_day[("2026-10-21", True)]["spo2_low_count"], by_day[("2026-10-21", True)]["spo2_low_times"]) == (0, [])
    assert (by_day[("2026-10-11", True)]["spo2_low_count"], by_day[("2026-10-11", True)]["spo2_low_times"]) == (
        None,
        None,
    )
    assert by_day[("2026-10-16", False)]["is_main"] is False


@pytest.mark.parametrize(
    ("start", "end"),
    [(D(2026, 9, 14), D(2026, 10, 15)), (D(2026, 10, 14), D(2026, 10, 16)), (D(2026, 10, 15), D(2026, 10, 14))],
)
async def test_memory_sessions_period_outside_contract_is_lookup_failure(start, end):
    # 백엔드 조회 함수의 입력 검증(1~31일, 끝은 KST 오늘까지)에 걸리면 포트가 조회 실패로 바꾼다
    with pytest.raises(HealthDataUnavailableError) as exc:
        await sample_port().get_sleep_sessions("u1", start, end)
    assert exc.value.metrics == ("sleep",)


async def test_memory_sessions_not_linked_member_fails():
    port = sample_port()
    port.not_linked = {"u1"}
    with pytest.raises(HealthDataNotLinkedError):
        await port.get_sleep_sessions("u1", D(2026, 10, 14), D(2026, 10, 14))


async def test_sessions_result_rejects_internal_owner_in_snapshot():
    result = await sample_port().get_sleep_sessions("u1", D(2026, 10, 14), D(2026, 10, 14))
    with pytest.raises(ResultContractError):
        SleepSessionsResult("u1", {**result.snapshot, "user_id": "u1"})
    broken = {**result.snapshot, "sessions": [{"date": "2026-10-14"}]}
    with pytest.raises(ResultContractError):
        SleepSessionsResult("u1", broken)


async def test_default_does_not_query_sessions_or_add_non_main_fields():
    port = sample_port()
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    # 두 번째 조회는 기본 조회에 붙는 두 줄(오늘까지 42일 주 수면)이다
    assert [c[0] for c in port.calls] == ["get_metric_result", "get_metric_result"]
    assert "non_main_sleeps" not in out.payload and "non_main_note" not in out.payload
    assert [type(e) for e in out.evidence] == [EvidenceDraft]


async def test_include_non_main_adds_non_main_sleeps_and_keeps_main_nights():
    port = sample_port()
    plain = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(sample_port()))
    out = await get_sleep_records(GetSleepRecords(period="last_7_days", include_non_main=True), request(port))
    assert sorted(c[0] for c in port.calls) == ["get_metric_result", "get_metric_result", "get_sleep_sessions"]
    assert ("get_sleep_sessions", "u1", D(2026, 10, 9), TODAY) in port.calls
    assert out.payload["non_main_sleeps"] == [NAP]
    assert out.payload["non_main_note"] == NON_MAIN_NOTE
    # 주 수면 기록과 기간 요약은 include_non_main 과 관계없이 같다
    assert out.payload["nights"] == plain.payload["nights"] and out.payload["summary"] == plain.payload["summary"]
    assert list(out.payload)[-4:] == ["non_main_sleeps", "non_main_note", "sleep_vs_baseline", "last_night_vs_week"]


async def test_non_main_sleep_without_sleep_minutes_does_not_use_session_length():
    port = sample_port()
    port.sleep["u1"] = [s for s in port.sleep["u1"] if s.is_main] + [night(13, (13, 14, 0), (14, 40), None, main=False)]
    out = await get_sleep_records(GetSleepRecords(period="last_7_days", include_non_main=True), request(port))
    assert out.payload["non_main_sleeps"] == ["10월 13일 낮 수면 시간 기록 없음"]


async def test_include_non_main_without_non_main_sleep_states_no_record():
    port = sample_port()
    port.sleep["u1"] = [s for s in port.sleep["u1"] if s.is_main]
    out = await get_sleep_records(GetSleepRecords(period="last_7_days", include_non_main=True), request(port))
    assert out.payload["non_main_sleeps"] == [] and out.payload["non_main_note"] == NO_NON_MAIN


async def test_include_non_main_period_over_31_days_is_refused_before_lookup():
    port = sample_port()
    out = await get_sleep_records(GetSleepRecords(period="recent_days:32", include_non_main=True), request(port))
    assert out.status == "unavailable_period"
    assert out.payload["message"].startswith("날짜별 수면 기록은 한 번에 31일까지 조회된다.")
    # 하루만 남는 조각은 하루 표현으로 쓴다
    assert "나누면 date:09-14, dates:09-15..10-15 로 조회된다." in out.payload["message"]
    assert port.calls == []


def test_include_non_main_takes_only_true_or_false():
    assert GetSleepRecords(period="days_ago:1").include_non_main is False
    with pytest.raises(ValueError):
        GetSleepRecords.model_validate({"period": "days_ago:1", "include_non_main": "true"})


async def test_include_non_main_evidence_is_main_record_then_sleep_sessions_kind():
    port = sample_port()
    out = await get_sleep_records(GetSleepRecords(period="last_7_days", include_non_main=True), request(port))
    base, detail = out.evidence
    assert isinstance(base, EvidenceDraft) and isinstance(detail, KindEvidenceDraft)
    assert (base.metric, detail.metric, detail.kind) == ("sleeps", "sleeps", "sleep_sessions")
    assert detail.period == base.period and detail.tool == "get_sleep_records"
    sessions = await port.get_sleep_sessions("u1", D(2026, 10, 9), TODAY)
    assert detail.event_snapshot() == {"kind": "sleep_sessions", **sessions.snapshot}
    assert detail.summary == "지난 7일 수면 세션 4개 (주 수면이 아닌 잠 1개)"


async def test_sessions_lookup_failure_keeps_main_result_as_detail_failed():
    port = SessionsPort(sample_port(), error=HealthDataUnavailableError(metrics=("sleep",)))
    plain = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(sample_port()))
    out = await get_sleep_records(GetSleepRecords(period="last_7_days", include_non_main=True), request(port))
    assert out.status == "detail_failed"
    assert out.payload == plain.payload and [type(e) for e in out.evidence] == [EvidenceDraft]
    assert out.detail == {"error": "detail_failed", "message": "상세를 불러오지 못했다"}


async def test_gateway_sends_kind_evidence_only_when_allowed():
    for enabled, metrics in ((frozenset(), ["sleeps"]), ({"sleep_sessions"}, ["sleeps", "sleeps"])):
        gateway = ToolGateway(sample_port(), TODAY, evidence_enabled=enabled)
        [reply] = await gateway.execute([call(include_non_main=True)], CTX)
        assert [e.metric for e in reply.evidence] == metrics
        message = json.loads(reply.content)
        assert message["evidence_ids"] == [f"E{i}" for i in range(1, len(metrics) + 1)]
        assert message["result"]["non_main_sleeps"] == [NAP]
    assert reply.evidence[1].snapshot["kind"] == "sleep_sessions"


async def test_gateway_limit_error_from_sessions_becomes_limit_result():
    error = HealthDataLimitError(
        "SLEEP_SESSION_LIMIT_EXCEEDED", source="sleep_sessions", can_narrow=True, limit=1000, limit_unit="sessions"
    )
    audit = MemoryAuditSink()
    gateway = ToolGateway(SessionsPort(sample_port(), error=error), TODAY, audit=audit)
    [reply] = await gateway.execute([call(include_non_main=True)], CTX)
    assert json.loads(reply.content) == {
        "error": "limit_exceeded",
        "tool": "get_sleep_records",
        "message": "조회 범위의 기록이 한도(수면 기록 1,000개)를 넘었다. 기간을 줄이면 조회할 수 있다",
    }
    assert reply.evidence == ()
    [record] = audit.records()
    assert (record.result_status, record.evidence_ids, record.result_count) == ("limit_exceeded", (), 0)


async def test_gateway_sessions_of_other_member_end_as_forbidden():
    gateway = ToolGateway(SessionsPort(sample_port(), owner="u2"), TODAY)
    with pytest.raises(HealthDataForbiddenError) as exc:
        await gateway.execute([call(include_non_main=True)], CTX)
    assert exc.value.metrics == ("sleep",)


async def test_gateway_counts_only_main_result_days_and_sessions_make_status_ok():
    # 실제 수면 시간을 모르는 주 수면 하나만 있으면 지표 원형에는 값이 있는 날이 없다
    port = FakeHealthData(
        sleep={"u1": [night(14, (14, 1, 10), (7, 15), None), night(14, (14, 14, 0), (14, 40), 40, main=False)]},
        sync={"u1": {"sleep": SyncState(at(15, 7, 20), "success", at(15, 7, 25))}},
        linked_from={"u1": D(2026, 9, 20)},
        now=NOW,
    )
    audit = MemoryAuditSink()
    gateway = ToolGateway(port, TODAY, audit=audit, evidence_enabled=frozenset())
    await gateway.execute([call("days_ago:1"), call("days_ago:1", include_non_main=True)], CTX)
    plain, with_sessions = audit.records()
    assert (plain.result_status, plain.result_count) == ("no_record", 1)
    # 수면 세션 결과는 세션이 하나라도 있으면 값이 있는 결과로 센다. 조회한 날짜 수는 기본 결과만 센다
    assert (with_sessions.result_status, with_sessions.result_count) == ("ok", 1)


async def test_sessions_result_period_must_match_request():
    class ShiftedPort(FakeHealthData):
        async def get_sleep_sessions(self, user_id, start, end):
            result = await super().get_sleep_sessions(user_id, start, end)
            snapshot = {**result.snapshot, "period": {"start": "2026-10-01", "end": end.isoformat()}}
            return SleepSessionsResult(user_id, snapshot)

    port = ShiftedPort(**vars(sample_port()))
    with pytest.raises(ResultContractError):
        await get_sleep_records(GetSleepRecords(period="last_7_days", include_non_main=True), request(port))


def test_low_times_in_sessions_keep_offsets():
    snapshot = {
        "period": {"start": "2026-10-14", "end": "2026-10-14"},
        "sessions": [
            {
                "date": "2026-10-14",
                "start_at": "2026-10-13T16:10:00Z",
                "end_at": "2026-10-13T22:15:00Z",
                "is_main": True,
                "sleep_minutes": 340,
            },
        ],  # fmt: skip
        "data_status": {"checked_at": "2026-10-15T03:00:00Z"},
    }
    [session] = SleepSessionsResult("u1", snapshot).sessions
    assert session.start_at == datetime(2026, 10, 13, 16, 10, tzinfo=UTC)
    assert session.start_at.astimezone(KST).date() == date(2026, 10, 14)


async def test_audit_args_leave_out_default_include_non_main():
    audit = MemoryAuditSink()
    gateway = ToolGateway(sample_port(), TODAY, audit=audit, evidence_enabled=frozenset())
    await gateway.execute([call(), call("days_ago:1", include_non_main=True)], CTX)
    assert [r.args for r in audit.records()] == [
        {"period": "last_7_days"},
        {"period": "days_ago:1", "include_non_main": True},
    ]


async def test_explicit_default_is_the_same_call_as_no_argument():
    audit = MemoryAuditSink()
    gateway = ToolGateway(sample_port(), TODAY, audit=audit)
    await gateway.execute([call(), call(include_non_main=False)], CTX)
    assert [(r.decision, r.reason) for r in audit.records()] == [("allow", None), ("deny", "duplicate")]
