import asyncio
import json
import uuid
from datetime import UTC, date, datetime

import pytest
import respx

from coach_ai.adapters.memory import DailyMessageDay, StepDay, SyncState
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import ToolCall
from coach_ai.agent.graph import run_coach
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.ports import HealthDataNotReadyError, HealthDataUnavailableError, MetricResult
from coach_ai.security import gateway as gateway_module
from coach_ai.security.audit import JsonlAuditSink, MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE, ToolGateway
from coach_ai.tools.handlers import HANDLERS
from coach_ai.tools.periods import DateRange
from coach_ai.tools.schemas import TOOL_INPUTS
from tests.fakes import FakeHealthData
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
# 한국 시간 10-15 11:00. 그 전 날짜의 빈 날은 기록 없음, 10-15 는 동기화 전이다
SYNCED = datetime(2026, 10, 15, 2, 0, tzinfo=UTC)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
STEPS = ToolCall("c1", "get_metric_summary", {"metric": "steps", "period": "last_7_days"})
RECORD_FIELDS = {
    "request_id", "user_id", "sequence_no", "round_no", "tool_name", "args", "period_dates", "decision", "reason",
    "result_status", "latency_ms", "result_count", "evidence_ids", "created_at",
}  # fmt: skip


@pytest.fixture
def model(settings):
    return create_chat_model(settings, recorder=MetricsRecorder(), retry_base_delay=0.0)


def steps_port() -> FakeHealthData:
    return FakeHealthData(now=NOW, steps={"u1": [StepDay(date(2026, 10, d), 7412) for d in (9, 10)]})


def synced_port(**changes) -> FakeHealthData:
    """10-09·10-10 걸음이 있는 회원. 10-08 까지는 연동 전, 10-11~14 는 기록 없음, 10-15 는 동기화 전이다."""
    states = {metric: SyncState(last_data_at=SYNCED) for metric in ("steps", "exercise", "sleep", "heart_rate")}
    return FakeHealthData(
        now=NOW, steps={"u1": [StepDay(date(2026, 10, d), 7412) for d in (9, 10)]}, sync={"u1": states}, **changes
    )


def steps_on(period: str, call_id: str = "c1") -> ToolCall:
    return ToolCall(call_id, "get_metric_summary", {"metric": "steps", "period": period})


def decisions(sink: MemoryAuditSink) -> list[tuple]:
    return [(r.tool_name, r.decision, r.reason) for r in sink.records()]


async def test_each_call_is_recorded_in_call_order():
    sink = MemoryAuditSink()
    calls = [
        STEPS,
        ToolCall("c2", "export_all_users", {}),
        ToolCall("c3", "get_metric_summary", {"metric": "steps", "period": "last_7_days", "user_id": "u2"}),
        ToolCall("c4", "get_metric_summary", {"period": "last_7_days", "metric": "steps"}),
    ]
    replies = await ToolGateway(steps_port(), TODAY, audit=sink).execute(calls, CTX)

    assert decisions(sink) == [
        ("get_metric_summary", "allow", None),
        ("export_all_users", "deny", "unknown_tool"),
        ("get_metric_summary", "deny", "extra_field"),
        ("get_metric_summary", "deny", "duplicate"),
    ]
    first, unknown, extra, duplicate = sink.records()
    assert (first.request_id, first.user_id) == ("r-1", "u1")
    assert first.args == duplicate.args == {"metric": "steps", "period": "last_7_days"}
    # result_count 는 조회한 날짜 수(공통 원형 days 길이)다. 지난 7일이라 7
    assert (first.result_count, first.evidence_ids) == (7, (replies[0].evidence[0].id,))
    assert first.created_at.tzinfo is not None
    assert (unknown.args, extra.args, duplicate.evidence_ids) == (None, None, ())
    assert unknown.latency_ms == unknown.result_count == 0


async def test_lookup_timeout_is_recorded_as_allowed_with_timeout_reason():
    class SlowPort(FakeHealthData):
        async def get_metric_result(self, user_id, metric, start, end):
            await asyncio.sleep(0.2)
            return await super().get_metric_result(user_id, metric, start, end)

    sink = MemoryAuditSink()
    with pytest.raises(HealthDataUnavailableError):
        await ToolGateway(SlowPort(), TODAY, audit=sink, timeout=0.05).execute([STEPS], CTX)
    assert decisions(sink) == [("get_metric_summary", "allow", "timeout")]
    # Windows 에서는 타이머가 시계 해상도(약 15ms)만큼 일찍 울릴 수 있다
    assert sink.records()[0].latency_ms >= 30


@pytest.mark.parametrize(
    ("error", "reason"),
    [(HealthDataNotReadyError(metrics=["steps"]), "data_not_ready"), (RuntimeError("bug"), "internal")],
)
async def test_failed_call_is_recorded_with_its_reason(error, reason):
    async def failing(inp, req):
        raise error

    sink = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_metric_summary": failing}, audit=sink)
    with pytest.raises(type(error)):
        await gateway.execute([STEPS], CTX)
    assert decisions(sink) == [("get_metric_summary", "allow", reason)]


async def test_call_cut_by_cancellation_is_recorded_as_cancelled():
    async def slow(inp, req):
        await asyncio.sleep(10)

    sink = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_metric_summary": slow}, audit=sink)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await gateway.execute([STEPS], CTX)
    assert decisions(sink) == [("get_metric_summary", "allow", "cancelled")]


async def test_long_unknown_tool_name_is_cut_to_column_size():
    sink = MemoryAuditSink()
    await ToolGateway(FakeHealthData(), TODAY, audit=sink).execute([ToolCall("c1", "x" * 500, {})], CTX)
    assert sink.records()[0].tool_name == "x" * 64


async def test_jsonl_sink_writes_one_line_per_call_with_table_columns(tmp_path):
    path = tmp_path / "logs" / "tool_calls.jsonl"
    calls = [STEPS, ToolCall("c2", "export_all_users", {})]
    await ToolGateway(steps_port(), TODAY, audit=JsonlAuditSink(path)).execute(calls, CTX)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(r["decision"], r["reason"]) for r in lines] == [("allow", None), ("deny", "unknown_tool")]
    [evidence_id] = lines[0]["evidence_ids"]
    assert uuid.UUID(evidence_id).version == 4 and lines[1]["evidence_ids"] == []
    assert all(set(r) == RECORD_FIELDS for r in lines)
    assert datetime.fromisoformat(lines[0]["created_at"]).tzinfo is not None


@respx.mock
async def test_log_holds_no_health_values_question_or_exception_text(model, tmp_path):
    async def broken(inp, req):
        raise RuntimeError("u1 수면 412분 조회 중 db-01 연결 끊김")

    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_metric_summary", STEPS.args), ("get_sleep_records", {"period": "today"}))]
    )
    path = tmp_path / "tool_calls.jsonl"
    handlers = {"get_metric_summary": HANDLERS["get_metric_summary"], "get_sleep_records": broken}
    result = await run_coach(
        CTX, "혈압 135/88인데 지난 7일 걸음 수 알려줘", port=steps_port(), model=model, sink=ListSink(), now=NOW,
        handlers=handlers, audit=JsonlAuditSink(path),
    )  # fmt: skip

    assert result.error_code == "internal"
    saved = path.read_text(encoding="utf-8")
    assert [json.loads(line)["reason"] for line in saved.splitlines()] == [None, "internal"]
    for secret in ("7412", "7,412", "412분", "db-01", "135/88", "혈압"):
        assert secret not in saved


@respx.mock
async def test_audit_failure_does_not_change_the_answer(model):
    class BrokenAudit:
        async def write(self, record):
            raise OSError("disk full")

    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_metric_summary", STEPS.args)), text_response("기록을 확인했어요.")]
    )
    result = await run_coach(
        CTX, "지난 7일 걸음 수 알려줘", port=steps_port(), model=model, sink=ListSink(), now=NOW, audit=BrokenAudit()
    )
    assert (result.finish_reason, result.answer) == ("stop", "기록을 확인했어요.")
    assert [e.ref for e in result.evidence] == ["E1"]


@respx.mock
async def test_evidence_uuid_is_the_same_in_event_log_and_result(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(
                ("get_metric_summary", STEPS.args), ("export_all_users", {}), ("get_sleep_records", {"period": "today"})
            ),
            text_response("기록을 확인했어요."),
        ]
    )
    audit, sink = MemoryAuditSink(), ListSink()
    result = await run_coach(
        CTX, "지난 7일 걸음 수랑 오늘 수면 알려줘", port=steps_port(), model=model, sink=sink, now=NOW, audit=audit
    )

    ids = [e.id for e in result.evidence]
    assert [uuid.UUID(i).version for i in ids] == [4, 4] and ids[0] != ids[1]
    [event] = [e for e in sink.events if e["type"] == "evidence"]
    assert [(item["id"], item["position"]) for item in event["items"]] == [(ids[0], 1), (ids[1], 2)]
    assert [r.evidence_ids for r in audit.records()] == [(ids[0],), (), (ids[1],)]
    # 지난 7일 걸음은 7일, 거부된 호출은 0, 오늘 수면은 1일을 조회한다
    assert [r.result_count for r in audit.records()] == [7, 0, 1]
    replies = [m["content"] for m in json.loads(route.calls[1].request.content)["messages"] if m["role"] == "tool"]
    assert [None if c == DENIED_MESSAGE else json.loads(c)["evidence_ids"] for c in replies] == [["E1"], None, ["E2"]]


async def test_result_status_other_than_ok_is_recorded_as_reason():
    audit = MemoryAuditSink()
    calls = [
        STEPS,
        ToolCall("c2", "compare_periods", {"metric": "steps", "current": "last_week", "baseline": "this_week"}),
        ToolCall("c3", "get_health_overview", {"period": "last_7_days"}),
    ]
    handlers = {k: v for k, v in HANDLERS.items() if k != "get_health_overview"}
    await ToolGateway(FakeHealthData(), TODAY, handlers=handlers, audit=audit).execute(calls, CTX)
    rows = [(r.tool_name, r.decision, r.reason, r.result_count, r.evidence_ids) for r in audit.records()]
    assert rows[1:] == [
        ("compare_periods", "allow", "invalid_periods", 0, ()),
        ("get_health_overview", "allow", "not_supported", 0, ()),
    ]
    # 정상 결과는 지금처럼 사유가 없다
    assert rows[0][1:3] == ("allow", None)


async def test_calls_are_numbered_in_order_across_rounds_of_one_request():
    sink = MemoryAuditSink()
    gateway = ToolGateway(steps_port(), TODAY, audit=sink)
    await gateway.execute([STEPS, ToolCall("c2", "export_all_users", {})], CTX)
    await gateway.execute([ToolCall("c3", "get_sleep_records", {"period": "today"})], CTX)

    assert [(r.sequence_no, r.round_no, r.tool_name) for r in sink.records()] == [
        (1, 1, "get_metric_summary"),
        (2, 1, "export_all_users"),
        (3, 2, "get_sleep_records"),
    ]


async def test_period_dates_hold_the_dates_each_period_expression_points_to():
    sink = MemoryAuditSink()
    calls = [
        steps_on("weekday:sat"),
        ToolCall("c2", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"}),
        ToolCall("c3", "get_daily_message", {}),
        steps_on("dates:10-14..10-20", "c4"),
        steps_on("date:02-30", "c5"),
        ToolCall("c6", "export_all_users", {"period": "last_7_days"}),
    ]
    await ToolGateway(synced_port(), TODAY, audit=sink).execute(calls, CTX)

    records = sink.records()
    # 10-15 는 목요일이라 지난 토요일은 10-10 이고, 이번 주는 월요일부터 오늘까지다
    assert [r.period_dates for r in records] == [
        {"period": DateRange(date(2026, 10, 10), date(2026, 10, 10))},
        {
            "current": DateRange(date(2026, 10, 12), TODAY),
            "baseline": DateRange(date(2026, 10, 5), date(2026, 10, 11)),
        },
        {},
        # 아직 오지 않은 날이 있어 조회하지 않은 기간도 표현이 가리키는 날짜를 기록한다
        {"period": DateRange(date(2026, 10, 14), date(2026, 10, 20))},
        # 없는 날짜와 검증 전에 거부된 호출은 날짜가 없다
        {},
        {},
    ]
    assert [r.result_status for r in records[3:]] == ["unavailable_period", "unavailable_period", None]


async def test_every_period_field_of_every_tool_gets_its_dates():
    calls = []
    for i, (name, schema) in enumerate(TOOL_INPUTS.items()):
        # 기간 칸은 선택지가 없는 문자열 칸이다. 지표·수면 분석·조건(선택지)과 수면 기록의 include_non_main(bool)은
        # 기간 칸이 아니다. 수면 기록의 기간은 선택 칸이라 도구 정의의 타입으로 찾는다
        properties = schema.model_json_schema()["properties"]
        periods = [field for field, prop in properties.items() if prop.get("type") == "string" and "enum" not in prop]
        args: dict[str, str] = {field: f"days_ago:{n}" for n, field in enumerate(periods, start=1)}
        if "metric" in schema.model_fields:
            args["metric"] = "steps"
        calls.append(ToolCall(f"c{i}", name, args))
    sink = MemoryAuditSink()
    await ToolGateway(synced_port(), TODAY, audit=sink, max_calls=len(calls)).execute(calls, CTX)

    for call, record in zip(calls, sink.records(), strict=True):
        assert record.decision == "allow"
        assert set(record.period_dates) == set(call.args) - {"metric"}


async def test_result_status_tells_records_from_no_record_unsynced_and_before_link():
    sink = MemoryAuditSink()
    calls = [
        steps_on("last_7_days"),
        steps_on("date:10-12", "c2"),
        steps_on("days_ago:0", "c3"),
        steps_on("date:10-01", "c4"),
        ToolCall("c5", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"}),
        steps_on("last_7_days", "c6"),
    ]
    await ToolGateway(synced_port(), TODAY, audit=sink).execute(calls, CTX)

    assert [(r.decision, r.reason, r.result_status) for r in sink.records()] == [
        ("allow", None, "ok"),
        ("allow", None, "no_record"),
        ("allow", None, "unsynced"),
        ("allow", None, "before_link"),
        # 이번 주는 기록이 없지만 지난주에 기록이 있다
        ("allow", None, "ok"),
        ("deny", "duplicate", None),
    ]


@pytest.mark.parametrize(
    ("call", "status"),
    [
        (ToolCall("c1", "get_lifestyle_score", {"period": "date:10-12"}), "no_record"),
        (ToolCall("c1", "get_lifestyle_score", {"period": "days_ago:0"}), "unsynced"),
        (ToolCall("c1", "get_lifestyle_score", {"period": "date:10-01"}), "before_link"),
        (ToolCall("c1", "get_daily_message", {}), "no_record"),
    ],
)
async def test_score_and_daily_message_status_follow_their_missing_reason(call, status):
    sink = MemoryAuditSink()
    await ToolGateway(synced_port(), TODAY, audit=sink).execute([call], CTX)
    assert [r.result_status for r in sink.records()] == [status]


async def test_daily_message_waiting_for_sync_is_unsynced():
    waiting = DailyMessageDay(TODAY, "WAITING", "WAITING_SYNC", None, "합성 제목", "합성 설명")
    sink = MemoryAuditSink()
    port = synced_port(daily_messages={"u1": [waiting]})
    await ToolGateway(port, TODAY, audit=sink).execute([ToolCall("c1", "get_daily_message", {})], CTX)
    assert [r.result_status for r in sink.records()] == ["unsynced"]


async def test_failure_and_guidance_reasons_are_also_the_result_status():
    async def not_ready(inp, req):
        raise HealthDataNotReadyError(metrics=["sleep"])

    handlers = {k: v for k, v in HANDLERS.items() if k != "get_health_overview"}
    handlers["get_sleep_records"] = not_ready
    calls = [
        ToolCall("c1", "compare_periods", {"metric": "steps", "current": "last_week", "baseline": "this_week"}),
        ToolCall("c2", "get_health_overview", {"period": "last_7_days"}),
        ToolCall("c3", "get_sleep_records", {"period": "today"}),
    ]
    sink = MemoryAuditSink()
    with pytest.raises(HealthDataNotReadyError):
        await ToolGateway(synced_port(), TODAY, handlers=handlers, audit=sink).execute(calls, CTX)
    assert [(r.reason, r.result_status) for r in sink.records()] == [
        ("invalid_periods", "invalid_periods"),
        ("not_supported", "not_supported"),
        ("data_not_ready", "data_not_ready"),
    ]


async def test_call_cut_by_cancellation_keeps_its_number_dates_and_status():
    async def slow(inp, req):
        await asyncio.sleep(10)

    sink = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_metric_summary": slow}, audit=sink)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await gateway.execute([STEPS], CTX)
    [record] = sink.records()
    assert (record.sequence_no, record.round_no, record.result_status) == (1, 1, "cancelled")
    assert record.period_dates == {"period": DateRange(date(2026, 10, 9), TODAY)}


async def test_jsonl_sink_writes_period_dates_as_iso_dates(tmp_path):
    path = tmp_path / "tool_calls.jsonl"
    await ToolGateway(steps_port(), TODAY, audit=JsonlAuditSink(path)).execute([STEPS], CTX)

    [line] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert line["period_dates"] == {"period": {"start": "2026-10-09", "end": "2026-10-15"}}
    assert (line["sequence_no"], line["round_no"], line["result_status"]) == (1, 1, "ok")


async def test_record_that_cannot_be_built_does_not_change_the_replies(monkeypatch):
    def broken(period, today):
        raise RuntimeError("기간 계산 실패")

    expected = await ToolGateway(steps_port(), TODAY, audit=MemoryAuditSink()).execute([STEPS], CTX)
    monkeypatch.setattr(gateway_module, "period_range", broken)
    sink = MemoryAuditSink()
    replies = await ToolGateway(steps_port(), TODAY, audit=sink).execute([STEPS], CTX)

    assert [(r.name, r.content) for r in replies] == [(r.name, r.content) for r in expected]
    assert sink.records() == []


async def test_result_without_values_or_missing_reasons_is_no_record():
    class ReasonlessPort(FakeHealthData):
        async def get_metric_result(self, user_id, metric, start, end):
            result = await super().get_metric_result(user_id, metric, start, end)
            snapshot = {**result.snapshot, "days": [{**day, "reason": None} for day in result.snapshot["days"]]}
            return MetricResult(user_id, snapshot)

    sink = MemoryAuditSink()
    port = ReasonlessPort(now=NOW, steps={"u1": [StepDay(date(2026, 10, 9), 7412)]})
    await ToolGateway(port, TODAY, audit=sink).execute([steps_on("date:10-12")], CTX)
    assert [r.result_status for r in sink.records()] == ["no_record"]
