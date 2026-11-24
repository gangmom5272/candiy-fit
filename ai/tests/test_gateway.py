import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from coach_ai.adapters.memory import SleepSession
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import EvidenceDraft, ToolCall, ToolOutcome
from coach_ai.ports import (
    HealthDataForbiddenError,
    HealthDataNotReadyError,
    HealthDataUnavailableError,
    LifestyleScoreResult,
    MetricResult,
)
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE, LOOKUP_TIMEOUT_S, MAX_TOOL_CALLS, ToolGateway
from coach_ai.tools.handlers import HANDLERS
from coach_ai.tools.results import PeriodSpan
from coach_ai.tools.schemas import PUBLIC_METRICS
from tests.fakes import SCORE_IN_PROGRESS, FakeHealthData

TODAY = date(2026, 10, 15)
KST = timezone(timedelta(hours=9))
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
SPAN = PeriodSpan(period="last_7_days", start=date(2026, 10, 9), end=TODAY, basis="날짜(KST)")
# 호출 횟수 한도 거부만 사유를 알린다
CALL_LIMIT_SENTENCE = "한 답에서 조회는 6번까지라 이 호출은 실행하지 않았다. 기록이 없다는 뜻은 아니다"


def summary(metric: str = "steps", period: str = "last_7_days") -> ToolCall:
    return ToolCall(f"call-{metric}-{period}", "get_metric_summary", {"metric": metric, "period": period})


def sleep_records(period: str = "last_7_days") -> ToolCall:
    return ToolCall(f"call-sleep-{period}", "get_sleep_records", {"period": period})


def outcome(metric: str) -> ToolOutcome:
    draft = EvidenceDraft(
        "get_metric_summary", PUBLIC_METRICS[metric], SPAN, metric, {"metric": PUBLIC_METRICS[metric]}
    )
    return ToolOutcome("ok", {"metric": metric}, (draft,))


async def drafting(inp, req) -> ToolOutcome:
    return outcome(getattr(inp, "metric", "sleep"))


def is_denied(replies) -> list[bool]:
    return [r.content == DENIED_MESSAGE for r in replies]


def test_limits_follow_api_rules():
    assert LOOKUP_TIMEOUT_S == 1.0
    assert MAX_TOOL_CALLS == 6


async def test_calls_in_one_response_run_concurrently_and_reply_in_call_order():
    second_started = asyncio.Event()

    async def waits_for_second(inp, req):
        await second_started.wait()
        return outcome("steps")

    async def second(inp, req):
        second_started.set()
        return outcome("sleep")

    handlers = {"get_metric_summary": waits_for_second, "get_sleep_records": second}
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers=handlers, timeout=0.5)
    replies = await gateway.execute([summary(), sleep_records()], CTX)

    assert [r.call_id for r in replies] == ["call-steps-last_7_days", "call-sleep-last_7_days"]
    assert [[e.position for e in r.evidence] for r in replies] == [[1], [2]]
    assert [json.loads(r.content)["evidence_ids"] for r in replies] == [["E1"], ["E2"]]


async def test_evidence_positions_continue_across_rounds_with_new_uuids():
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_metric_summary": drafting})
    first = await gateway.execute([summary("steps")], CTX)
    second = await gateway.execute([summary("sleep"), summary("heart_rate")], CTX)
    evidence = [e for r in [*first, *second] for e in r.evidence]
    assert [e.position for e in evidence] == [1, 2, 3]
    assert len({uuid.UUID(e.id) for e in evidence}) == 3


async def test_one_call_can_hold_several_evidence_and_numbers_continue():
    async def two_periods(inp, req):
        drafts = tuple(outcome(m).evidence[0] for m in ("steps", "sleep"))
        return ToolOutcome("ok", {"metric": "steps"}, drafts)

    audit = MemoryAuditSink()
    handlers = {"compare_periods": two_periods, "get_metric_summary": drafting}
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers=handlers, audit=audit)
    compare = ToolCall("c1", "compare_periods", {"metric": "steps", "current": "this_week", "baseline": "last_week"})
    replies = await gateway.execute([compare, summary("heart_rate")], CTX)

    assert [[e.ref for e in r.evidence] for r in replies] == [["E1", "E2"], ["E3"]]
    assert [json.loads(r.content)["evidence_ids"] for r in replies] == [["E1", "E2"], ["E3"]]
    assert [e.summary for e in replies[0].evidence] == ["steps", "sleep"]
    assert [r.evidence_ids for r in audit.records()] == [tuple(e.id for e in r.evidence) for r in replies]


async def test_event_only_evidence_takes_positions_but_no_tool_message_number():
    # 근거 이벤트로만 보내는 근거는 순서(position)를 차지하고 호출 로그에 들어가지만, 도구 메시지의 근거 번호는 받지
    # 않는다. 뒤 호출의 번호는 도구 메시지에 넣는 근거만 센다
    async def with_extra(inp, req):
        main, *extra = (outcome(m).evidence[0] for m in ("sleep", "steps", "heart_rate"))
        return ToolOutcome("ok", {"metric": "sleep"}, (main,), event_only_evidence=tuple(extra))

    audit = MemoryAuditSink()
    handlers = {"get_sleep_records": with_extra, "get_metric_summary": drafting}
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers=handlers, audit=audit)
    replies = await gateway.execute([sleep_records(), summary("steps")], CTX)

    assert [json.loads(r.content) for r in replies] == [
        {"evidence_ids": ["E1"], "result": {"metric": "sleep"}},
        {"evidence_ids": ["E2"], "result": {"metric": "steps"}},
    ]
    assert [[(e.position, e.ref, e.metric) for e in r.evidence] for r in replies] == [
        [(1, "E1", "sleeps"), (2, None, "steps"), (3, None, "heart_rates")],
        [(4, "E2", "steps")],
    ]
    assert [r.evidence_ids for r in audit.records()] == [tuple(e.id for e in r.evidence) for r in replies]


async def test_handler_gets_user_from_context_only():
    seen = []

    async def recording(inp, req):
        seen.append(req.user_id)
        return outcome("steps")

    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_metric_summary": recording})
    await gateway.execute([summary()], CTX)
    assert seen == ["u1"]


async def test_denied_calls_get_only_the_fixed_sentence():
    calls = [
        ToolCall("c1", "export_all_users", {"all": True}),
        ToolCall("c2", "get_metric_summary", {"metric": "steps"}),
        ToolCall("c3", "get_metric_summary", None),
    ]
    replies = await ToolGateway(FakeHealthData(), TODAY).execute(calls, CTX)
    assert [r.content for r in replies] == [DENIED_MESSAGE] * 3
    assert all(r.evidence == () for r in replies)


async def test_call_limit_counts_executed_calls_across_rounds():
    periods = ["today", "yesterday", "last_7_days", "last_14_days", "last_30_days", "this_week", "last_week"]
    gateway = ToolGateway(FakeHealthData(), TODAY)
    first = await gateway.execute([summary(period=p) for p in periods[:4]], CTX)
    second = await gateway.execute([summary(period=p) for p in periods[4:]], CTX)
    assert [r.content == CALL_LIMIT_SENTENCE for r in [*first, *second]] == [False] * 6 + [True]


async def test_call_limit_reply_states_the_limit_without_evidence():
    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, audit=audit)
    periods = ["today", "yesterday", "last_7_days", "last_14_days", "last_30_days", "this_week", "last_week"]
    replies = await gateway.execute([summary(period=p) for p in periods], CTX)
    assert replies[-1].content == CALL_LIMIT_SENTENCE and replies[-1].evidence == ()
    assert (audit.records()[-1].decision, audit.records()[-1].reason) == ("deny", "call_limit")


async def test_guidance_without_lookup_still_counts_toward_the_call_limit():
    """31일 한도 안내만 받은 호출도 한 번으로 센다. 6주 질문을 주별로 다시 부르면 마지막 주는 횟수 한도 문장이다."""
    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, audit=audit)
    [first] = await gateway.execute([sleep_records("recent_days:42")], CTX)
    weekly = await gateway.execute([sleep_records(f"weeks_ago:{n}") for n in range(6)], CTX)
    assert json.loads(first.content)["error"] == "unavailable_period"
    assert [r.content == CALL_LIMIT_SENTENCE for r in weekly] == [False] * 5 + [True]
    assert [r.reason for r in audit.records()] == ["unavailable_period", *[None] * 5, "call_limit"]


async def test_denials_other_than_call_limit_keep_the_fixed_sentence():
    """다른 회원을 지정한 호출 등 호출 횟수 한도가 아닌 거부는 지금처럼 사유 없는 문장만 받는다."""
    calls = [
        ToolCall("c1", "export_all_users", {"all": True}),
        ToolCall("c2", "get_metric_summary", {"metric": "steps", "period": "today", "user_id": "u2"}),
        ToolCall("c3", "get_metric_summary", {"metric": "steps", "period": "3주 전"}),
        ToolCall("c4", "get_metric_summary", {"metric": "steps", "period": "today"}),
        ToolCall("c5", "get_metric_summary", {"period": "today", "metric": "steps"}),
    ]
    audit = MemoryAuditSink()
    replies = await ToolGateway(FakeHealthData(), TODAY, audit=audit).execute(calls, CTX)
    assert [r.reason for r in audit.records()] == ["unknown_tool", "extra_field", "invalid_args", None, "duplicate"]
    assert [r.content == DENIED_MESSAGE for r in replies] == [True, True, True, False, True]


async def test_same_tool_and_args_in_any_key_order_is_duplicate():
    calls = [
        ToolCall("c1", "get_metric_summary", {"metric": "steps", "period": "today"}),
        ToolCall("c2", "get_metric_summary", {"period": "today", "metric": "steps"}),
        ToolCall("c3", "get_metric_summary", {"metric": "steps", "period": "yesterday"}),
    ]
    replies = await ToolGateway(FakeHealthData(), TODAY).execute(calls, CTX)
    assert is_denied(replies) == [False, True, False]


class OwnedByPort(FakeHealthData):
    """정해 둔 회원을 소유자로 적은 결과를 반환하는 잘못된 구현."""

    def __init__(self, owner, **kw) -> None:
        super().__init__(**kw)
        self.owner = owner

    async def get_metric_result(self, user_id, metric, start, end):
        result = await super().get_metric_result(user_id, metric, start, end)
        return MetricResult(self.owner, result.snapshot)


async def test_result_of_another_member_ends_with_forbidden():
    night = SleepSession(
        date(2026, 10, 14), datetime(2026, 10, 14, 0, 30, tzinfo=KST), datetime(2026, 10, 14, 7, 0, tzinfo=KST),
        True, 380, 60, 80, 220, 20,
    )  # fmt: skip
    gateway = ToolGateway(OwnedByPort("u2", sleep={"u1": [night]}), TODAY)
    with pytest.raises(HealthDataForbiddenError) as exc:
        await gateway.execute([sleep_records()], CTX)
    assert exc.value.metrics == ("sleep",)


@pytest.mark.parametrize("owner", ["", None])
async def test_result_without_owner_is_treated_as_another_member(owner):
    with pytest.raises(HealthDataForbiddenError):
        await ToolGateway(OwnedByPort(owner), TODAY).execute([summary()], CTX)


async def test_lookup_over_time_limit_ends_with_unavailable_and_its_metrics():
    class SlowPort(FakeHealthData):
        async def get_metric_result(self, user_id, metric, start, end):
            await asyncio.sleep(0.2)
            return await super().get_metric_result(user_id, metric, start, end)

    gateway = ToolGateway(SlowPort(), TODAY, timeout=0.05)
    with pytest.raises(HealthDataUnavailableError) as exc:
        await gateway.execute([summary("heart_rate")], CTX)
    assert exc.value.metrics == ("heart_rate",)


async def test_overview_over_time_limit_reports_its_four_metrics():
    async def slow(inp, req):
        await asyncio.sleep(0.2)

    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_health_overview": slow}, timeout=0.05)
    with pytest.raises(HealthDataUnavailableError) as exc:
        await gateway.execute([ToolCall("call-overview", "get_health_overview", {"period": "this_week"})], CTX)
    # 전체 요약은 산소포화도를 묶지 않아 오류 지표에도 산소포화도가 없다
    assert exc.value.metrics == ("steps", "exercise", "sleep", "heart_rate")


async def test_timeout_error_raised_inside_handler_is_not_a_lookup_timeout():
    async def broken(inp, req):
        raise TimeoutError("connection pool")

    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_metric_summary": broken})
    with pytest.raises(TimeoutError):
        await gateway.execute([summary()], CTX)


class ScorePort(FakeHealthData):
    """정해 둔 회원을 소유자로 적은 점수(API 명세 5.3 예시)를 반환한다."""

    def __init__(self, owner, **kw) -> None:
        super().__init__(**kw)
        self.owner = owner

    async def get_lifestyle_score(self, user_id, day):
        self.calls.append(("get_lifestyle_score", user_id, day))
        return LifestyleScoreResult(self.owner, SCORE_IN_PROGRESS)


async def reads_score(inp, req) -> ToolOutcome:
    score = await req.port.get_lifestyle_score(req.user_id, req.today)
    return ToolOutcome("ok", {"total": score.total.score})


async def test_score_lookup_passes_owner_check_and_counts_one_day():
    audit = MemoryAuditSink()
    port = ScorePort("u1")
    gateway = ToolGateway(port, TODAY, handlers={"get_metric_summary": reads_score}, audit=audit)
    [reply] = await gateway.execute([summary()], CTX)
    assert json.loads(reply.content) == {"evidence_ids": [], "result": {"total": 67}}
    assert port.calls == [("get_lifestyle_score", "u1", TODAY)]
    [record] = audit.records()
    assert (record.decision, record.reason, record.result_count) == ("allow", None, 1)


@pytest.mark.parametrize("owner", ["u2", "", None])
async def test_score_of_another_member_or_without_owner_ends_with_forbidden(owner):
    gateway = ToolGateway(ScorePort(owner), TODAY, handlers={"get_metric_summary": reads_score})
    with pytest.raises(HealthDataForbiddenError):
        await gateway.execute([summary()], CTX)


async def test_owner_mismatch_wins_over_earlier_failure_in_same_response():
    async def not_ready(inp, req):
        raise HealthDataNotReadyError(metrics=["steps"])

    handlers = {"get_metric_summary": not_ready, "get_sleep_records": HANDLERS["get_sleep_records"]}
    gateway = ToolGateway(OwnedByPort("u2"), TODAY, handlers=handlers)
    with pytest.raises(HealthDataForbiddenError):
        await gateway.execute([summary(), sleep_records()], CTX)


async def test_call_limit_reply_states_the_limit_of_this_gateway():
    gateway = ToolGateway(FakeHealthData(), TODAY, max_calls=1)
    replies = await gateway.execute([summary(period="today"), summary(period="yesterday")], CTX)
    assert replies[1].content == "한 답에서 조회는 1번까지라 이 호출은 실행하지 않았다. 기록이 없다는 뜻은 아니다"


# 에이전트가 더한 호출 (added)
def analysis(kind: str, period: str | None = None, call_id: str = "c1") -> ToolCall:
    args = {"analysis": kind} if period is None else {"analysis": kind, "period": period}
    return ToolCall(call_id, "get_sleep_records", args)


class AnalysisRecorder:
    """수면 분석 처리기 대신 쓰는 처리기. 실행한 분석과 기간을 기록한다."""

    def __init__(self) -> None:
        self.ran: list[tuple[str | None, str | None]] = []

    async def __call__(self, inp, req) -> ToolOutcome:
        self.ran.append((inp.analysis, inp.period))
        return outcome("sleep")


async def test_added_call_runs_in_the_same_round_after_the_model_calls():
    recorder = AnalysisRecorder()
    audit = MemoryAuditSink()
    handlers = {"get_sleep_records": recorder, "get_metric_summary": drafting}
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers=handlers, audit=audit)
    calls = [summary("oxygen_saturation"), analysis("factors", call_id="c2")]
    replies = await gateway.execute(calls, CTX, added=[None, analysis("signals", call_id="added-1")])

    assert [r.call_id for r in replies] == ["call-oxygen_saturation-last_7_days", "c2", "added-1"]
    assert sorted(recorder.ran) == [("factors", None), ("signals", None)]
    assert [json.loads(r.content)["evidence_ids"] for r in replies] == [["E1"], ["E2"], ["E3"]]
    assert [(r.sequence_no, r.round_no, r.decision, r.args) for r in audit.records()] == [
        (1, 1, "allow", {"metric": "oxygen_saturation", "period": "last_7_days"}),
        (2, 1, "allow", {"analysis": "factors"}),
        (3, 1, "allow", {"analysis": "signals"}),
    ]


async def test_added_call_over_the_call_limit_is_neither_run_nor_logged():
    recorder = AnalysisRecorder()
    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_sleep_records": recorder}, audit=audit, max_calls=1)
    replies = await gateway.execute([analysis("factors")], CTX, added=[analysis("signals", call_id="added-0")])

    assert [r.call_id for r in replies] == ["c1"] and recorder.ran == [("factors", None)]
    # 받은 순서도 쓰지 않아 다음 호출의 순서가 이어진다
    await gateway.execute([summary()], CTX)
    assert [(r.sequence_no, r.reason) for r in audit.records()] == [(1, None), (2, "call_limit")]


async def test_added_call_already_run_in_this_answer_is_not_run_again():
    recorder = AnalysisRecorder()
    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_sleep_records": recorder}, audit=audit)
    await gateway.execute([analysis("signals")], CTX)
    replies = await gateway.execute(
        [analysis("factors", call_id="c2")], CTX, added=[analysis("signals", call_id="added-0")]
    )

    assert [r.call_id for r in replies] == ["c2"]
    assert recorder.ran == [("signals", None), ("factors", None)]
    assert [r.decision for r in audit.records()] == ["allow", "allow"]


async def test_same_added_call_twice_in_one_response_runs_once():
    recorder = AnalysisRecorder()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_sleep_records": recorder})
    other = ToolCall(
        "c2", "get_sleep_records", {"analysis": "factors", "period": "recent_days:14", "include_non_main": True}
    )
    calls = [analysis("factors", "recent_days:14"), other]
    added = [analysis("signals", "recent_days:14", "added-0"), analysis("signals", "recent_days:14", "added-1")]
    replies = await gateway.execute(calls, CTX, added=added)

    assert [r.call_id for r in replies] == ["c1", "c2", "added-0"]
    assert sorted(recorder.ran).count(("signals", "recent_days:14")) == 1


async def test_added_call_is_not_run_when_its_model_call_is_denied():
    recorder = AnalysisRecorder()
    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={"get_sleep_records": recorder}, audit=audit)
    calls = [analysis("factors"), analysis("factors", call_id="c2"), analysis("factors", "3주 전", "c3")]
    added = [None, analysis("signals", call_id="added-1"), analysis("signals", "3주 전", "added-2")]
    replies = await gateway.execute(calls, CTX, added=added)

    assert [r.call_id for r in replies] == ["c1", "c2", "c3"]
    assert [r.content == DENIED_MESSAGE for r in replies] == [False, True, True]
    assert recorder.ran == [("factors", None)]
    assert [r.reason for r in audit.records()] == [None, "duplicate", "invalid_args"]
