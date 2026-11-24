import asyncio
import copy
import hashlib
import inspect
import json
import re
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from coach_ai.adapters.memory import StepDay
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.budget import count_request_tokens
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import EventEmitter, ListSink
from coach_ai.agent.executor import (
    AddedCalls,
    EvidenceDraft,
    ExtendedRun,
    ExtraPeriod,
    ToolCall,
    ToolOutcome,
    ToolRequest,
    tool_specs,
)
from coach_ai.agent.graph import INTERNAL_MESSAGE, ChatTurn, build_messages, run_coach
from coach_ai.agent.nodes import model as model_node
from coach_ai.agent.nodes.model import TOOL_LIMIT_ANSWER, TOOL_LIMIT_NOTICE
from coach_ai.agent.prompts import (
    DEFAULT_PROMPT_VERSION,
    OMITTED_HISTORY_NOTICE,
    TODAY_SLOT,
    load_system_prompt,
    other_day_notice,
    render_system_prompt,
    system_with_history,
    today_text,
)
from coach_ai.agent.rules import guard
from coach_ai.agent.rules.guard import RULES as GUARD_RULES
from coach_ai.history.budget import (
    INPUT_TOKENS_LIMIT,
    INPUT_TOO_LARGE_MESSAGE,
    InputTooLargeError,
    input_tokens_limit,
)
from coach_ai.history.types import HistoryTurn, HistoryUsage, StoredSummary
from coach_ai.llm.client import USER_MESSAGES, LLMEmptyResponseError, create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.ports import (
    HealthDataForbiddenError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    HealthDataUnavailableError,
    MetricResult,
)
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE, ToolGateway
from coach_ai.tools.periods import WEEKDAYS, DateRange
from coach_ai.tools.results import PeriodComparison, PeriodSpan
from coach_ai.tools.schemas import PERIODS, PUBLIC_METRICS
from eval import repeated_question
from eval.golden import fixtures as gf
from tests.fakes import FakeHealthData
from tests.llm_fakes import (
    CHAT_URL,
    SSE_HEADERS,
    ScriptedStream,
    chunk,
    sse,
    sse_event,
    text_response,
    tool_call_response,
)
from tests.test_sleep_coaching_text import SYNTH11_FACTORS, SYNTH11_SIGNALS

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # 2026-10-15 12:00 KST
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
SPAN = PeriodSpan(period="last_7_days", start="2026-10-09", end="2026-10-15", basis="날짜(KST)")
# 처리기를 대신하는 RecordingHandler 의 근거 snapshot. 실제 원형 대신 짧은 dict 를 쓴다
SNAPSHOT = {"metric": "steps", "summary": {"avg": 7412}}
# 예외 문구. 회원 ID·수치·서버 이름이 섞여 있어 이벤트에 나오면 안 된다
SECRET = "u1 걸음 7412 조회 중 db-01 연결 끊김"


def history_turn(question_seq: int, question: str, answer_seq: int, answer: str) -> HistoryTurn:
    """요청·메시지 ID 는 순서 번호로 만든 UUID 문자열이라 요청 본문·이벤트에서 찾을 수 있다."""

    def uid(kind: str, seq: int) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-{kind}-{seq}"))

    return HistoryTurn(
        request_id=uid("request", question_seq),
        question_message_id=uid("message", question_seq),
        answer_message_id=uid("message", answer_seq),
        question_seq=question_seq,
        answer_seq=answer_seq,
        question=question,
        answer=answer,
    )


# 대화 API 가 CoachContext 로 넘기는 이전 대화
SUMMARY = StoredSummary(text="걸음 수를 자주 묻는다.", until_seq=4)
TURNS = (
    history_turn(5, "어제 몇 걸음 걸었어?", 6, "8,000걸음이에요."),
    history_turn(7, "그제는?", 8, "6,500걸음이에요."),
)
TURN_IDS = [value for t in TURNS for value in (t.request_id, t.question_message_id, t.answer_message_id)]
# 숫자는 한 글자가 1토큰이라 토큰 수가 글자 수와 같다. 요약 7, 쌍 2+3, 4+1
DIGIT_SUMMARY = StoredSummary(text="1234567", until_seq=2)
DIGIT_TURNS = (history_turn(3, "12", 4, "345"), history_turn(5, "6789", 6, "0"))
DIGIT_USAGE = HistoryUsage(summary_used=True, included_turns=2, excluded_turns=0, history_tokens=10, summary_tokens=7)
# 다른 날 질문한 쌍이 있을 때 요약 뒤에 붙는 안내. NOW 의 KST 날짜가 오늘이다
OTHER_DAY_NOTICE = (
    "이전 대화에는 다른 날 나눈 내용이 있고, 그 대화의 오늘·어제는 그날 기준이다. 오늘은 2026년 10월 15일 목요일이다."
)


@pytest.fixture
def recorder() -> MetricsRecorder:
    return MetricsRecorder()


@pytest.fixture
def model(settings, recorder):
    return create_chat_model(settings, recorder=recorder, retry_base_delay=0.0)


class RecordingHandler:
    """get_metric_summary 대신 쓰는 처리기. 받은 사용자 ID 와 오늘 날짜를 기록한다."""

    def __init__(self) -> None:
        self.user_ids: list[str] = []
        self.todays: list[date] = []

    async def __call__(self, inp, req: ToolRequest) -> ToolOutcome:
        self.user_ids.append(req.user_id)
        self.todays.append(req.today)
        draft = EvidenceDraft(
            "get_metric_summary", PUBLIC_METRICS[inp.metric], SPAN, "지난 7일 평균 7,412걸음", SNAPSHOT
        )
        return ToolOutcome("ok", {"avg": {"value": 7412.0, "display": "7,412걸음"}}, (draft,))


class FailingPort(FakeHealthData):
    """지표 조회에서 정해 둔 예외를 발생시킨다."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def get_metric_result(self, user_id, metric, start, end):
        raise self.error


class BrokenSink:
    """연결이 끊긴 sink. 모든 이벤트에서 예외를 발생시킨다."""

    async def emit(self, event: dict) -> None:
        raise ConnectionError("closed")


def bodies(route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


def streamed(events: list[dict]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "token")


async def run(model, question="지난 7일 걸음 수 알려줘", port=None, ctx=CTX, **kw):
    sink = ListSink()
    port = FakeHealthData() if port is None else port
    result = await run_coach(ctx, question, port=port, model=model, sink=sink, now=NOW, **kw)
    ends = [e["type"] for e in sink.events if e["type"] in ("done", "error")]
    assert ends == ["error" if result.finish_reason == "error" else "done"]
    assert sink.events[-1]["type"] == ends[0]
    return result, sink.events


@respx.mock
async def test_tool_call_then_answer(model):
    handler = RecordingHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("지난 7일 ", "평균 7,412걸음이에요."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": handler}, message_id="m-1")

    assert result.answer == "지난 7일 평균 7,412걸음이에요."
    assert result.finish_reason == "stop" and result.tool_rounds == 1
    assert [e.ref for e in result.evidence] == ["E1"]
    assert handler.user_ids == ["u1"]

    first, second = bodies(route)
    assert [t["function"]["name"] for t in first["tools"]] == [
        "get_metric_summary", "get_daily_trend", "get_sleep_records", "compare_periods", "get_health_overview",
        "get_exercise_sessions", "get_lifestyle_score", "get_daily_message",
    ]  # fmt: skip
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"])["evidence_ids"] == ["E1"]


class SleepAnalysisHandler:
    """get_sleep_records 대신 쓰는 처리기. 받은 기간과 분석 값을 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    async def __call__(self, inp, req: ToolRequest) -> ToolOutcome:
        self.calls.append((inp.period, inp.analysis))
        return ToolOutcome("ok", {"period": "최근 14일(10월 2일-15일)"})


SLEEP_PATTERN_WEEK = {"analysis": "pattern", "period": "recent_days:7"}


@respx.mock
async def test_sleep_analysis_without_period_words_runs_without_model_period(model):
    handler = SleepAnalysisHandler()
    audit = MemoryAuditSink()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_sleep_records", SLEEP_PATTERN_WEEK)),
            text_response("최근 14일 기록이에요."),
        ]
    )
    result, _ = await run(model, "잘 자고 싶은데 어떻게 해야 해?", handlers={"get_sleep_records": handler}, audit=audit)

    assert handler.calls == [(None, "pattern")]
    assert [r.args for r in audit.records()] == [{"analysis": "pattern"}]
    asked, reply = result.messages[-3], result.messages[-2]
    assert asked.tool_calls[0]["args"] == SLEEP_PATTERN_WEEK
    assert reply.artifact == ToolCall("call_1", "get_sleep_records", {"analysis": "pattern"})
    # LLM 에는 모델이 낸 호출과 도구 결과 본문만 보낸다
    sent = bodies(route)[1]["messages"]
    assert json.loads(sent[-2]["tool_calls"][0]["function"]["arguments"]) == SLEEP_PATTERN_WEEK
    assert set(sent[-1]) == {"role", "content", "tool_call_id"}


@respx.mock
async def test_follow_up_after_answer_saying_recent_keeps_model_period(model):
    handler = SleepAnalysisHandler()
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_sleep_records", SLEEP_PATTERN_WEEK)),
            text_response("최근 7일 기록이에요."),
        ]
    )
    # 수면 골든셋 sleep1-12 형식의 앞 대화. 약 질문을 차단한 고정 답의 제안 문장에만 "최근"이 있다
    previous = history_turn(1, "잠 오는 약을 먹어 봐도 될까?", 2, guard.medication_answer(sleep=True))
    result, _ = await run(
        model, "응, 알려 줘", ctx=replace(CTX, turns=(previous,)), handlers={"get_sleep_records": handler}
    )

    assert handler.calls == [("recent_days:7", "pattern")]
    assert result.messages[-2].artifact is None


# 앞 답의 건강 수치를 도구 없이 다시 쓴 첫 답은 보내지 않고 한 번 다시 조회하게 한다 (nodes/model.py)
STEP_TURN = history_turn(1, "합성 이전 질문", 2, "전날 기록은 8,000걸음이었어요.")
REUSED_PARTS = ("어제 8,000걸음을 ", "걸으셨으니 오늘도 그만큼 걸어 보세요.")
STEPS_CALL = ("get_metric_summary", {"metric": "steps", "period": "last_7_days"})


def tokens(events) -> list[str]:
    return [e["delta"] for e in events if e["type"] == "token"]


def last_user_text(body: dict) -> str:
    return [m for m in body["messages"] if m["role"] == "user"][-1]["content"]


def recheck_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "answer rechecked" in r.getMessage()]


UNQUERIED_CLOCK = "평균 취침 시각인 오후 11시 45분에 맞춰 준비해 보세요."


@respx.mock
async def test_sleep_closing_discards_unqueried_clock_without_lookup(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response(UNQUERIED_CLOCK))
    result, events = await run(model, "알겠어, 오늘은 일찍 자 볼게", ctx=replace(CTX, turns=(STEP_TURN,)))
    assert result.error_code is None and route.call_count == 1
    assert result.answer_rechecks == 0 and result.tool_rounds == 0
    assert tokens(events) == [result.answer] and "11시 45분" not in result.answer


@respx.mock
async def test_new_sleep_clock_is_rechecked_before_streaming(model):
    handler = SleepAnalysisHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(UNQUERIED_CLOCK),
            tool_call_response(("get_sleep_records", {"analysis": "pattern"})),
            text_response("조회한 취침 시각 01:37보다 일찍 준비해 보세요."),
        ]
    )
    result, events = await run(
        model,
        "그럼 오늘부터 뭘 하면 돼?",
        ctx=replace(CTX, turns=(STEP_TURN,)),
        handlers={"get_sleep_records": handler},
    )
    assert result.error_code is None
    assert result.answer_rechecks == 1 and handler.calls == [(None, "pattern")]
    assert tokens(events) == [result.answer] and "11시 45분" not in result.answer
    assert model_node.SLEEP_CLOCK_RECHECK_NOTICE in last_user_text(bodies(route)[1])


@pytest.mark.parametrize("second", [UNQUERIED_CLOCK, "기록상 평균 취침 시각은 00:15입니다."])
@respx.mock
async def test_unqueried_sleep_clock_is_not_leaked_after_failed_retry(model, second):
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(UNQUERIED_CLOCK), text_response(second)])
    result, events = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))
    assert result.error_code is None and route.call_count == 2
    assert result.answer == model_node.NO_VERIFIED_SLEEP_CLOCK
    assert tokens(events) == [result.answer] and result.answer_rechecks == 1


@pytest.mark.parametrize(
    "first", ["".join(REUSED_PARTS), "기록을 확인해 보겠습니다.\n}\n<|channel>thought\n<channel|>"]
)
@respx.mock
async def test_other_retry_paths_do_not_leak_a_new_sleep_clock(model, first):
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(first), text_response(UNQUERIED_CLOCK)])
    result, events = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))
    assert result.error_code is None and route.call_count == 2
    assert result.answer_rechecks == 1 and result.answer == model_node.NO_VERIFIED_SLEEP_CLOCK
    assert tokens(events) == [result.answer]


@respx.mock
async def test_reused_number_without_tools_is_sent_back_once_with_notice(model, caplog):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(*REUSED_PARTS),
            tool_call_response(STEPS_CALL),
            text_response("지난 7일 평균 7,412걸음이라 오늘은 저녁에 걸어 보세요."),
        ]
    )
    question = "그럼 오늘은 뭘 하면 좋아?"
    result, events = await run(
        model, question, ctx=replace(CTX, turns=(STEP_TURN,)), handlers={"get_metric_summary": RecordingHandler()}
    )

    assert result.answer == "지난 7일 평균 7,412걸음이라 오늘은 저녁에 걸어 보세요."
    # 되돌린 답은 화면으로 나가지 않는다
    assert tokens(events) == [result.answer]
    assert (result.answer_rechecks, result.tool_rounds, result.finish_reason) == (1, 1, "stop")
    first, again, after_tool = bodies(route)
    # 첫 요청 본문은 그대로이고, 안내는 다시 보낸 요청의 이번 질문 끝에만 붙는다
    assert last_user_text(first) == question
    assert last_user_text(again) == f"{question}\n\n{model_node.RECHECK_NOTICE}"
    assert first["messages"][:-1] == again["messages"][:-1] and first["tools"] == again["tools"]
    assert last_user_text(after_tool) == question
    # 안내와 되돌린 답은 상태와 결과 메시지에 포함되지 않는다
    assert all(model_node.RECHECK_NOTICE not in str(m.content) for m in result.messages)
    assert all("걸으셨으니 오늘도" not in str(m.content) for m in result.messages)
    [log] = recheck_logs(caplog)
    assert "request_id=r-1" in log and "values=1" in log and "kinds=steps" in log and "8,000" not in log


@respx.mock
async def test_recheck_happens_only_once(model):
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*REUSED_PARTS), text_response(*REUSED_PARTS)])
    result, events = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))

    # 다시 받은 답에도 도구 호출이 없으면 그 답을 그대로 보낸다
    assert route.call_count == 2
    assert tokens(events) == list(REUSED_PARTS)
    assert (result.answer, result.answer_rechecks, result.tool_rounds) == ("".join(REUSED_PARTS), 1, 0)


@pytest.mark.parametrize(
    "parts",
    [("저녁에 20분 정도 ", "걸어 보세요."), ("최근 7일 기록을 ", "다시 볼게요."), ("오늘은 ", "일찍 쉬어 보세요.")],
    ids=["general_number", "period_only", "no_number"],
)
@respx.mock
async def test_answer_not_reusing_previous_numbers_is_sent_as_is(model, parts):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    result, events = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))

    # 모아 둔 조각을 받은 그대로 보낸다
    assert route.call_count == 1
    assert tokens(events) == list(parts) and result.answer_rechecks == 0


@respx.mock
async def test_first_reply_with_tool_call_is_not_rechecked(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(STEPS_CALL, text=("어제 8,000걸음을 보고 ",)), text_response("다시 볼게요.")]
    )
    result, events = await run(
        model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)), handlers={"get_metric_summary": RecordingHandler()}
    )

    assert route.call_count == 2
    assert tokens(events) == ["어제 8,000걸음을 보고 ", "다시 볼게요."]
    assert (result.answer_rechecks, result.tool_rounds) == (0, 1)


@respx.mock
async def test_number_only_in_summary_is_rechecked(model):
    summary = StoredSummary(text="어제 8,000걸음을 걸었다고 기록을 알려 줬다.", until_seq=2)
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*REUSED_PARTS), text_response("네.")])
    result, _ = await run(model, "그럼 오늘은?", ctx=replace(CTX, summary=summary))

    assert route.call_count == 2
    assert (result.answer, result.answer_rechecks) == ("네.", 1)


@respx.mock
async def test_value_the_user_said_is_not_rechecked(model):
    previous = history_turn(1, "나 어제 9,000걸음 걸었지?", 2, "말씀하신 9,000걸음과 달리 기록은 8,000걸음이에요.")
    route = respx.post(CHAT_URL).mock(return_value=text_response("말씀하신 9,000걸음을 ", "목표로 걸어 보세요."))
    result, _ = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(previous,)))

    assert route.call_count == 1 and result.answer_rechecks == 0


@respx.mock
async def test_recheck_keeps_sleep_period_correction(model):
    handler = SleepAnalysisHandler()
    # 앞 질문·앞 답·이번 질문에 기간 말이 없다. 안내의 "이전"은 기간 말이지만 기간 보정은 상태의 질문을 읽는다
    previous = history_turn(1, "잠은 어땠어?", 2, "평균 5시간 50분 주무셨어요.")
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response("5시간 50분이라 짧으니 ", "일찍 누워 보세요."),
            tool_call_response(("get_sleep_records", SLEEP_PATTERN_WEEK)),
            text_response("최근 14일 기록이에요."),
        ]
    )
    result, _ = await run(
        model, "그럼 뭘 하면 돼?", ctx=replace(CTX, turns=(previous,)), handlers={"get_sleep_records": handler}
    )

    assert route.call_count == 3 and result.answer_rechecks == 1
    assert handler.calls == [(None, "pattern")]


# 도구 없이 끝난 첫 답이 앞 답에도 질문에도 없는 건강 수치를 말하면 보내지 않고 한 번 다시 조회하게 한다
# (nodes/model.py)
UNSUPPORTED_PARTS = ("어젯밤 6시간 12분 ", "주무셨으니 오늘은 일찍 누워 보세요.")


def unsupported_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "unsupported numbers rechecked" in r.getMessage()]


@respx.mock
async def test_unsupported_number_without_tools_is_sent_back_once_with_notice(model, caplog):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(*UNSUPPORTED_PARTS),
            tool_call_response(STEPS_CALL),
            text_response("지난 7일 평균 7,412걸음이라 오늘은 저녁에 걸어 보세요."),
        ]
    )
    question = "오늘은 뭘 하면 좋아?"
    result, events = await run(model, question, handlers={"get_metric_summary": RecordingHandler()})

    assert result.answer == "지난 7일 평균 7,412걸음이라 오늘은 저녁에 걸어 보세요."
    # 되돌린 답은 화면으로 나가지 않는다. 앞 대화가 없어도 첫 답을 검사한다
    assert tokens(events) == [result.answer]
    assert (result.unsupported_rechecks, result.answer_rechecks, result.tool_rounds) == (1, 0, 1)
    first, again, after_tool = bodies(route)
    # 첫 요청 본문은 그대로이고, 안내는 다시 보낸 요청의 이번 질문 끝에만 붙는다
    assert last_user_text(first) == question
    assert last_user_text(again) == f"{question}\n\n{model_node.UNSUPPORTED_NOTICE}"
    assert first["messages"][:-1] == again["messages"][:-1] and first["tools"] == again["tools"]
    assert last_user_text(after_tool) == question
    # 안내와 되돌린 답은 상태와 결과 메시지에 포함되지 않는다
    assert all(model_node.UNSUPPORTED_NOTICE not in str(m.content) for m in result.messages)
    assert all("주무셨으니 오늘은" not in str(m.content) for m in result.messages)
    [log] = unsupported_logs(caplog)
    assert "request_id=r-1" in log and "values=1" in log and "kinds=minutes" in log and "6시간" not in log
    assert recheck_logs(caplog) == []


@respx.mock
async def test_number_missing_from_previous_answers_is_sent_back_with_unsupported_notice(model):
    parts = ("짧게 잔 밤에는 평균 91.3%로 ", "낮았어요.")
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*parts), text_response("기록을 다시 볼게요.")])
    result, _ = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))

    assert route.call_count == 2
    assert last_user_text(bodies(route)[1]) == f"그럼 오늘은?\n\n{model_node.UNSUPPORTED_NOTICE}"
    assert (result.answer, result.answer_rechecks, result.unsupported_rechecks) == ("기록을 다시 볼게요.", 0, 1)


@pytest.mark.parametrize(
    ("question", "parts"),
    [
        ("잠은 얼마나 자야 해?", ("하루 7~9시간 ", "자는 게 좋아요.")),
        ("오늘은 뭘 하면 좋아?", ("저녁에 30분 정도 ", "걸어 보세요.")),
        ("하루에 얼마나 걸어야 해?", ("일반적으로 하루 8,000걸음을 ", "목표로 해요.")),
        ("나 어제 9,000걸음 걸었는데 많이 걸은 거야?", ("9,000걸음이면 ", "꽤 걸으셨어요.")),
        ("오늘은 뭘 하면 좋아?", ("최근 7일 기록을 ", "보고 정해 볼게요.")),
        ("오늘은 뭘 하면 좋아?", ("오늘은 ", "일찍 쉬어 보세요.")),
    ],
    ids=["range", "estimate", "general_sentence", "user_said", "period_only", "no_number"],
)
@respx.mock
async def test_answer_without_unsupported_numbers_is_sent_as_received(model, question, parts):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    result, events = await run(model, question)

    # 모아 둔 조각을 받은 그대로 보낸다
    assert route.call_count == 1
    assert tokens(events) == list(parts)
    assert (result.answer_rechecks, result.unsupported_rechecks) == (0, 0)


@respx.mock
async def test_number_from_previous_answer_is_left_to_previous_number_recheck(model, caplog):
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*REUSED_PARTS), text_response("네.")])
    result, _ = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))

    assert last_user_text(bodies(route)[1]) == f"그럼 오늘은?\n\n{model_node.RECHECK_NOTICE}"
    assert (result.answer_rechecks, result.unsupported_rechecks) == (1, 0)
    assert unsupported_logs(caplog) == []


@respx.mock
async def test_previous_number_recheck_comes_first_when_both_are_found(model, caplog):
    # 앞 답에 있던 8,000걸음과 앞 답에 없는 6시간 12분이 함께 있으면 앞 답 수치의 안내로 한 번만 되돌린다
    parts = ("어제 8,000걸음을 걸으시고 ", "6시간 12분 주무셨어요.")
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*parts), text_response("네.")])
    result, _ = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))

    assert route.call_count == 2
    assert last_user_text(bodies(route)[1]) == f"그럼 오늘은?\n\n{model_node.RECHECK_NOTICE}"
    assert (result.answer, result.answer_rechecks, result.unsupported_rechecks) == ("네.", 1, 0)
    assert len(recheck_logs(caplog)) == 1 and unsupported_logs(caplog) == []


@respx.mock
async def test_unsupported_recheck_happens_only_once(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response(*UNSUPPORTED_PARTS), text_response(*UNSUPPORTED_PARTS)]
    )
    result, events = await run(model, "오늘은 뭘 하면 좋아?")

    # 다시 받은 답에도 도구 호출이 없으면 수치가 있어도 그대로 보낸다
    assert route.call_count == 2
    assert tokens(events) == list(UNSUPPORTED_PARTS)
    assert (result.answer, result.unsupported_rechecks, result.tool_rounds) == ("".join(UNSUPPORTED_PARTS), 1, 0)


@respx.mock
async def test_answer_after_previous_number_recheck_is_not_checked_again(model):
    # 앞 답 수치로 되돌린 뒤 다시 받은 답에 앞 답에 없는 수치가 있어도 더 되돌리지 않는다
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*REUSED_PARTS), text_response(*UNSUPPORTED_PARTS)])
    result, events = await run(model, "그럼 오늘은?", ctx=replace(CTX, turns=(STEP_TURN,)))

    assert route.call_count == 2
    assert tokens(events) == list(UNSUPPORTED_PARTS)
    assert (result.answer_rechecks, result.unsupported_rechecks) == (1, 0)


@respx.mock
async def test_first_reply_with_tool_call_is_not_checked_for_unsupported_numbers(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(STEPS_CALL, text=("어젯밤 6시간 12분을 보고 ",)), text_response("볼게요.")]
    )
    result, events = await run(model, "오늘은?", handlers={"get_metric_summary": RecordingHandler()})

    # 도구 호출 앞의 글은 모은 그대로 보내고 지금처럼 이어 간다
    assert route.call_count == 2
    assert tokens(events) == ["어젯밤 6시간 12분을 보고 ", "볼게요."]
    assert (result.unsupported_rechecks, result.tool_rounds) == (0, 1)


@respx.mock
async def test_tool_limit_call_is_not_checked_for_unsupported_numbers(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*UNSUPPORTED_PARTS))
    result, _ = await run(model, "오늘은 뭘 하면 좋아?", max_tool_rounds=0)

    assert route.call_count == 1
    assert (result.finish_reason, result.unsupported_rechecks) == ("tool_limit", 0)


# 원인을 묻는 질문은 따로 되돌리지 않는다. 수치 없이 도구 없이 끝난 답은 그대로 보낸다 (nodes/model.py)
CAUSE_QUESTION = "피곤한 게 운동 부족 때문일까?"
GENERAL_ANSWER = ("운동이 부족하면 ", "피로가 쌓일 수 있어요.")


@respx.mock
async def test_cause_question_answered_without_numbers_is_sent_as_is(model, caplog):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*GENERAL_ANSWER))
    result, events = await run(model, CAUSE_QUESTION, ctx=replace(CTX, turns=(STEP_TURN,)))

    assert route.call_count == 1
    assert tokens(events) == list(GENERAL_ANSWER) and result.answer == "".join(GENERAL_ANSWER)
    assert (result.answer_rechecks, result.unsupported_rechecks) == (0, 0)
    assert not [r for r in caplog.records if "rechecked" in r.getMessage()]


# 수면 계획 후속의 pattern 조회는 다른 되돌림이 걸리지 않을 때만 한다
SLEEP_TURN = history_turn(1, "어젯밤 잠은 어땠어?", 2, "어젯밤 수면 기록은 5시간 8분으로 짧습니다.")
PLAN_QUESTION = "그럼 오늘부터 뭘 하면 돼?"


@pytest.mark.parametrize(
    ("question", "parts", "notice", "counts"),
    [
        (PLAN_QUESTION, ("어젯밤 5시간 8분 ", "주무셨으니 일찍 누워 보세요."), "RECHECK_NOTICE", (1, 0)),
        (PLAN_QUESTION, UNSUPPORTED_PARTS, "UNSUPPORTED_NOTICE", (0, 1)),
    ],
    ids=["previous_number", "unsupported_number"],
)
@respx.mock
async def test_other_rechecks_come_before_sleep_plan_lookup(model, question, parts, notice, counts):
    handler = SleepAnalysisHandler()
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*parts), text_response("네.")])
    result, _ = await run(
        model, question, ctx=replace(CTX, turns=(SLEEP_TURN,)), handlers={"get_sleep_records": handler}
    )

    # 앞 답 수치 → 조회하지 않은 수치 → 수면 계획 후속 순서로 하나만 따른다
    assert route.call_count == 2 and handler.calls == []
    assert last_user_text(bodies(route)[1]) == f"{question}\n\n{getattr(model_node, notice)}"
    assert (result.answer_rechecks, result.unsupported_rechecks) == counts


@respx.mock
async def test_unsupported_number_is_not_rechecked_after_cleanup_recheck(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response("}\n<|channel>thought\n<channel|>"), text_response(*UNSUPPORTED_PARTS)]
    )
    result, events = await run(model, "오늘은 뭘 하면 좋아?")

    # 내부 표시를 정리했더니 빈 답을 되돌렸으면 다시 받은 답은 수치 검사로 다시 되돌리지 않는다
    assert route.call_count == 2
    assert "".join(tokens(events)) == "".join(UNSUPPORTED_PARTS) == result.answer
    assert (result.answer_rechecks, result.unsupported_rechecks, result.marker_cleanups) == (1, 0, 1)


@respx.mock
async def test_blank_after_cleanup_is_not_rechecked_after_unsupported_recheck(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(*UNSUPPORTED_PARTS),
            tool_call_response(STEPS_CALL),
            text_response("}\n<|channel>thought\n<channel|>"),
            text_response("지난 7일 평균 7,412걸음이에요."),
        ]
    )
    result, _ = await run(model, "오늘은 뭘 하면 좋아?", handlers={"get_metric_summary": RecordingHandler()})

    # 첫 호출에서 되돌림을 썼으면 도구 왕복 뒤 정리했더니 빈 답은 빈 응답 규칙대로 같은 요청을 한 번 다시 보낸다
    _, _, after_tool, resent = bodies(route)
    assert after_tool == resent
    assert result.answer == "지난 7일 평균 7,412걸음이에요."
    assert (result.unsupported_rechecks, result.answer_rechecks, result.marker_cleanups) == (1, 0, 1)


@respx.mock
async def test_effect_assertion_is_hedged_while_streaming(model):
    respx.post(CHAT_URL).mock(return_value=text_response("저녁에 걸으면 잠드는 데 ", "도움이 ", "됩니다."))
    result, events = await run(model)

    assert tokens(events) == ["저녁에 걸으면 잠드는 데 ", "도움이 될 수 있어요."]
    assert (result.answer, result.hedged_sentences) == ("저녁에 걸으면 잠드는 데 도움이 될 수 있어요.", 1)
    # 결과 메시지도 보낸 글과 같다
    assert result.messages[-1].content == result.answer


@respx.mock
async def test_answer_chunks_without_effect_words_keep_their_boundaries(model):
    parts = ("합성 걸음 기록을 ", "확인했어요.")
    respx.post(CHAT_URL).mock(side_effect=[tool_call_response(STEPS_CALL), text_response(*parts)])
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert tokens(events) == list(parts)
    assert (result.answer, result.hedged_sentences) == ("".join(parts), 0)


# 수면 골든셋 sleep1-05 개선용 질문과 골든셋 기준 시각(2026-10-22 21:00 KST)
SPO2_QUESTION = "내 산소포화도는 왜 이렇게 들쭉날쭉해?"
GOLDEN_NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SLEEP_FACTORS = {"analysis": "factors"}


@respx.mock
async def test_spo2_question_gets_signals_of_the_same_period_beside_factors(model):
    fixture = gf.load_fixture("synth-11")
    audit = MemoryAuditSink()
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-382", request_id="r-382")
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            # 기간 말이 없는 질문이라 최근 7일은 기간 보정으로 빠지고, 두 분석 모두 기본 기간으로 조회한다
            tool_call_response(("get_sleep_records", {"analysis": "factors", "period": "recent_days:7"})),
            text_response("최근 14일 중 7일이 90% 아래였어요."),
        ]
    )
    sink = ListSink()
    result = await run_coach(
        ctx, SPO2_QUESTION, port=fixture.port(), model=model, sink=sink, now=GOLDEN_NOW, audit=audit
    )

    assert result.finish_reason == "stop"
    # LLM 에는 모델이 낸 호출 하나와 그 결과 하나만 가고, 이상 신호 결과는 생활 요인 비교 결과 뒤에 이어 붙는다
    sent = bodies(route)[1]["messages"]
    assert [c["function"]["name"] for c in sent[-2]["tool_calls"]] == ["get_sleep_records"]
    body = json.loads(sent[-1]["content"])
    [added] = body["added"]
    assert (body["result"]["analysis"], body["result"]["text"]) == ("factors", SYNTH11_FACTORS)
    assert (added["result"]["analysis"], added["result"]["text"]) == ("signals", SYNTH11_SIGNALS)
    # 두 결과 글 모두 실제 조회 기간이 있고, 이상 신호 글에 90% 아래 날 수가 있다
    assert body["result"]["text"].startswith("아래 비교는 최근 28일(9월 25일-10월 22일) 수면을")
    assert added["result"]["text"].startswith("최근 14일(10월 9일-22일) 수면에서")
    assert "90% 미만으로 기록된 날은 7일" in added["result"]["text"]
    assert (body["evidence_ids"], added["evidence_ids"]) == (["E1"], ["E2"])
    # 생활 요인 비교의 낮 기록 근거는 근거 이벤트로만 보내 도구 메시지의 근거 번호가 없고, 순서(position)는 차지한다
    rows = [(e.ref, e.metric, e.period.start, e.period.end) for e in result.evidence]
    assert rows == [
        ("E1", "sleeps", date(2026, 9, 25), date(2026, 10, 22)),
        (None, "steps", date(2026, 9, 24), date(2026, 10, 21)),
        (None, "heart_rates", date(2026, 9, 24), date(2026, 10, 21)),
        (None, "exercises", date(2026, 9, 24), date(2026, 10, 21)),
        ("E2", "sleeps", date(2026, 10, 9), date(2026, 10, 22)),
    ]
    [event] = [e for e in sink.events if e["type"] == "evidence"]
    assert [(item["id"], item["position"]) for item in event["items"]] == [
        (e.id, i) for i, e in enumerate(result.evidence, 1)
    ]
    # 게이트웨이 검사와 호출 로그는 더한 호출도 실제 조회로 다룬다
    assert [(r.round_no, r.sequence_no, r.decision, r.args) for r in audit.records()] == [
        (1, 1, "allow", {"analysis": "factors"}),
        (1, 2, "allow", {"analysis": "signals"}),
    ]
    executed = ToolCall("call_1", "get_sleep_records", SLEEP_FACTORS)
    signals = ToolCall("added-0", "get_sleep_records", {"analysis": "signals"})
    assert result.messages[-2].artifact == AddedCalls(executed, (signals,))


GOLDEN_RECENT_28 = DateRange(date(2026, 9, 25), date(2026, 10, 22))


@respx.mock
async def test_factors_recalculated_by_the_handler_keeps_the_call_and_records_both_periods(model):
    # synth-11 은 최근 14일에 운동한 날이 없어 처리 함수가 최근 28일로 다시 계산한다. 모델이 낸 호출은 그대로 실행하고
    # LLM 에 보내며, 다시 계산한 기간은 artifact 와 호출 로그에 요청한 기간과 함께 들어간다
    fixture = gf.load_fixture("synth-11")
    audit = MemoryAuditSink()
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-389", request_id="r-389")
    two_weeks = {"analysis": "factors", "period": "recent_days:14"}
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_sleep_records", two_weeks)), text_response("최근 28일로 봤어요.")]
    )
    result = await run_coach(
        ctx,
        "합성 질문: 운동 조건 비교",
        port=fixture.port(),
        model=model,
        sink=ListSink(),
        now=GOLDEN_NOW,
        audit=audit,
    )

    executed = ToolCall("call_1", "get_sleep_records", two_weeks)
    extra = ExtraPeriod("extended_period", GOLDEN_RECENT_28)
    assert result.messages[-2].artifact == ExtendedRun(executed, extra)
    [record] = audit.records()
    assert record.args == two_weeks
    assert record.period_dates == {
        "period": DateRange(date(2026, 10, 9), date(2026, 10, 22)),
        "extended_period": extra.period,
    }
    sent = bodies(route)[1]["messages"]
    assert json.loads(sent[-2]["tool_calls"][0]["function"]["arguments"]) == two_weeks
    assert set(sent[-1]) == {"role", "content", "tool_call_id"}
    assert json.loads(sent[-1]["content"])["result"]["text"].startswith("요청한 기간인 최근 14일(10월 9일-22일)에는 ")


@respx.mock
async def test_exercise_period_and_added_signals_go_into_one_artifact(model):
    # 산소포화도 질문의 짧은 기간 생활 요인 비교: 처리 함수는 최근 28일 운동 조건 비교를 덧붙이고, 도구 노드는 같은
    # 기간의 이상 신호를 더한다. artifact 하나에 실행한 호출, 덧붙인 기간, 더한 호출이 함께 들어간다
    fixture = gf.load_fixture("synth-11")
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-389", request_id="r-389")
    week = {"analysis": "factors", "period": "recent_days:7"}
    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_sleep_records", week)), text_response("기록이에요.")]
    )
    result = await run_coach(
        ctx,
        # 기간 말(7일)이 있어 기간 보정이 걸리지 않고, 산소포화도를 가리키는 말이 있어 이상 신호가 더해지고, 운동 말이
        # 있어 최근 28일 운동 조건 비교가 덧붙는다
        "합성 질문: 7일 산소와 운동 비교",
        port=fixture.port(),
        model=model,
        sink=ListSink(),
        now=GOLDEN_NOW,
    )

    executed = ToolCall("call_1", "get_sleep_records", week)
    signals = ToolCall("added-0", "get_sleep_records", {"analysis": "signals", "period": "recent_days:7"})
    extra = ExtraPeriod("exercise_period", GOLDEN_RECENT_28)
    reply = result.messages[-2]
    assert reply.artifact == ExtendedRun(executed, extra, (signals,))
    body = json.loads(reply.content)
    assert body["result"]["exercise_comparison"].startswith("운동 조건 비교(최근 28일, 9월 25일-10월 22일): ")
    assert [a["result"]["analysis"] for a in body["added"]] == ["signals"]


async def run_week_factors(model, question: str, ctx: CoachContext, audit: MemoryAuditSink):
    """synth-11 에서 모델이 생활 요인 비교를 최근 7일로 부르는 대화."""
    fixture = gf.load_fixture("synth-11")
    week = {"analysis": "factors", "period": "recent_days:7"}
    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_sleep_records", week)), text_response("기록이에요.")]
    )
    ctx = replace(ctx, user_id=fixture.user_id)
    result = await run_coach(
        ctx, question, port=fixture.port(), model=model, sink=ListSink(), now=GOLDEN_NOW, audit=audit
    )
    return result.messages[-2], week


@respx.mock
async def test_short_factors_without_exercise_words_has_no_exercise_comparison(model):
    # 이번 질문에도 앞 질문에도 운동 말이 없으면 처리 함수가 덧붙이지 않는다 (앞 답의 운동 말은 찾지 않는다). 그 기간의
    # 주 수면만 조회하고, 도구 메시지와 호출 로그에 덧붙인 기간이 없다
    audit = MemoryAuditSink()
    previous = history_turn(1, "합성 질문: 어제 잠", 2, "운동한 날보다 짧게 잤어요.")
    reply, week = await run_week_factors(model, "합성 질문: 7일 수면 비교", replace(CTX, turns=(previous,)), audit)

    assert reply.artifact is None
    assert "exercise_comparison" not in json.loads(reply.content)["result"]
    [record] = audit.records()
    assert record.args == week and list(record.period_dates) == ["period"]


@respx.mock
async def test_exercise_word_of_the_previous_user_question_appends_the_exercise_comparison(model):
    # 이어진 대화면 바로 앞 사용자 질문의 운동 말로도 덧붙인다
    audit = MemoryAuditSink()
    previous = history_turn(1, "합성 질문: 저녁 운동", 2, "기록을 확인했어요.")
    reply, week = await run_week_factors(model, "합성 질문: 7일 수면 비교", replace(CTX, turns=(previous,)), audit)

    extra = ExtraPeriod("exercise_period", GOLDEN_RECENT_28)
    assert reply.artifact == ExtendedRun(ToolCall("call_1", "get_sleep_records", week), extra)
    assert json.loads(reply.content)["result"]["exercise_comparison"].startswith("운동 조건 비교(최근 28일, ")
    [record] = audit.records()
    assert record.period_dates["exercise_period"] == GOLDEN_RECENT_28


@respx.mock
async def test_signals_added_to_factors_takes_its_period(model):
    handler = SleepAnalysisHandler()
    week = {"analysis": "factors", "period": "weeks_ago:0"}
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(
                ("get_metric_summary", {"metric": "oxygen_saturation", "period": "weeks_ago:0"}),
                ("get_sleep_records", week),
            ),
            text_response("이번 주 기록이에요."),
        ]
    )
    handlers = {"get_sleep_records": handler, "get_metric_summary": RecordingHandler()}
    result, _ = await run(model, "SpO2 가 금주에 제일 낮던 날 뭐 했지", handlers=handlers)

    assert sorted(handler.calls) == [("weeks_ago:0", "factors"), ("weeks_ago:0", "signals")]
    summary, factors = result.messages[-3], result.messages[-2]
    assert summary.artifact is None and "added" not in json.loads(summary.content)
    assert [a.args for a in factors.artifact.added] == [{"analysis": "signals", "period": "weeks_ago:0"}]


@respx.mock
async def test_signals_is_not_added_when_it_would_pass_the_call_limit(model):
    handler = SleepAnalysisHandler()
    audit = MemoryAuditSink()
    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_sleep_records", SLEEP_FACTORS)), text_response("기록이에요.")]
    )
    gateway = ToolGateway(
        FakeHealthData(), NOW.date(), handlers={"get_sleep_records": handler}, audit=audit, max_calls=1
    )
    result, _ = await run(model, SPO2_QUESTION, executor=gateway)

    assert handler.calls == [(None, "factors")]
    assert [r.args for r in audit.records()] == [SLEEP_FACTORS]
    reply = result.messages[-2]
    assert reply.artifact is None and "added" not in json.loads(reply.content)


@respx.mock
async def test_heart_rate_question_keeps_only_the_factors_call(model):
    handler = SleepAnalysisHandler()
    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_sleep_records", SLEEP_FACTORS)), text_response("기록이에요.")]
    )
    result, _ = await run(model, "수면 중 맥박이 빠른 까닭이 궁금해", handlers={"get_sleep_records": handler})

    assert handler.calls == [(None, "factors")]
    assert result.messages[-2].artifact is None


@respx.mock
async def test_only_this_question_is_searched_for_spo2_words(model):
    handler = SleepAnalysisHandler()
    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_sleep_records", SLEEP_FACTORS)), text_response("기록이에요.")]
    )
    previous = history_turn(1, "자는 동안 산소포화도 기록 봐 줘", 2, "최근 14일 평균 95%예요.")
    result, _ = await run(
        model, "그럼 이유는 뭐야?", ctx=replace(CTX, turns=(previous,)), handlers={"get_sleep_records": handler}
    )

    assert handler.calls == [(None, "factors")]
    assert result.messages[-2].artifact is None


def daily_message_call_response(arguments: str) -> httpx.Response:
    """인자 문자열을 그대로 보내는 한마디 도구 호출 응답. 인자 없는 도구에는 빈 문자열을 보내는 서버도 있다."""
    function = {"name": "get_daily_message", "arguments": arguments}
    return httpx.Response(
        200,
        content=sse(
            chunk({"role": "assistant", "content": None}),
            chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": function}]}),
            chunk({}, finish_reason="tool_calls"),
            chunk(None, usage={"prompt_tokens": 300, "completion_tokens": 20, "total_tokens": 320}),
        ),
        headers=SSE_HEADERS,
    )


@pytest.mark.parametrize("arguments", ["{}", ""])
@respx.mock
async def test_daily_message_call_without_arguments_reaches_card_without_evidence(model, arguments):
    route = respx.post(CHAT_URL).mock(
        side_effect=[daily_message_call_response(arguments), text_response("오늘의 한마디는 걸음 기록으로 골랐어요.")]
    )
    port = sample_health_data("u1", NOW)
    result, events = await run(model, question="오늘 한마디 알려 줘", port=port)

    assert result.finish_reason == "stop" and result.tool_rounds == 1 and not result.evidence
    assert port.calls == [("get_daily_message", "u1", date(2026, 10, 15), "r-1")]
    status = [(e["message"], e["tool"]) for e in events if e["type"] == "status" and e["stage"] == "tool_call"]
    assert status == [("오늘의 한마디를 확인하고 있어요", "get_daily_message")]
    # 한마디 결과는 근거로 내보내지 않는다
    assert "evidence" not in [e["type"] for e in events]
    content = json.loads(bodies(route)[1]["messages"][-1]["content"])
    assert content["evidence_ids"] == []
    assert content["result"]["title"] == "한 걸음씩, 내 페이스대로 가볼까요?"
    assert content["result"]["basis"] == "오늘 걸음 기록으로 고른 문구다"


@respx.mock
async def test_event_order(model):
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("평균 ", "7,412걸음이에요."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()}, message_id="m-1")

    kinds = [e["type"] if e["type"] != "status" else f"status:{e['stage']}" for e in events]
    assert kinds == [
        "status:thinking", "status:tool_call", "status:tool_done", "status:thinking",
        "status:answering", "evidence", "token", "token", "done",
    ]  # fmt: skip
    assert all(e["conversation_id"] == "c-1" and e["request_id"] == "r-1" for e in events)
    assert [e["seq"] for e in events if e["type"] == "token"] == [0, 1]
    [item] = next(e for e in events if e["type"] == "evidence")["items"]
    assert (item["id"], item["position"]) == (result.evidence[0].id, 1)
    assert uuid.UUID(item["id"]).version == 4 and item["period"]["basis"] == "날짜(KST)"
    # 대화 API 가 공개 근거로 중계·저장할 수 있게 공개 지표 코드와 원형을 함께 넣는다
    assert (item["metric"], item["snapshot"]) == ("steps", SNAPSHOT)
    item["snapshot"]["summary"]["avg"] = 0
    assert result.evidence[0].snapshot == {"metric": "steps", "summary": {"avg": 7412}}
    done = events[-1]
    assert done["message_id"] == "m-1" and done["finish_reason"] == "stop"
    assert done["usage"] == {"prompt_tokens": 800, "completion_tokens": 60}


@respx.mock
async def test_thought_channel_not_in_token_events(model):
    # 도구 결과가 들어간 두 번째 호출에서 사고 채널 표시가 본문으로 쪼개져 온 실제 사례
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("<|channel>", "thought", "\n", "<channel|>", "평균 ", "7,412걸음이에요."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    deltas = [e["delta"] for e in events if e["type"] == "token"]
    assert "".join(deltas) == "평균 7,412걸음이에요."
    assert all("channel" not in d and "thought" not in d for d in deltas)
    assert [e["seq"] for e in events if e["type"] == "token"] == [0, 1]
    assert result.answer == "평균 7,412걸음이에요."


@respx.mock
async def test_text_before_tool_call_stays_in_answer(model):
    call = ("get_metric_summary", {"metric": "steps", "period": "last_7_days"})
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(call, text=("기록을 ", "확인해 볼게요. ")),
            text_response("평균 ", "7,412걸음이에요."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert streamed(events) == result.answer == "기록을 확인해 볼게요. 평균 7,412걸음이에요."
    assert result.finish_reason == "stop" and result.tool_rounds == 1
    assert [e["seq"] for e in events if e["type"] == "token"] == [0, 1, 2, 3]


@respx.mock
async def test_tool_rounds_are_capped(model):
    def call(period: str) -> tuple[str, dict]:
        return ("get_metric_summary", {"metric": "steps", "period": period})

    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(call("last_7_days")),
            tool_call_response(call("last_14_days")),
            tool_call_response(call("last_30_days")),
            text_response("모은 기록으로 답해요."),
        ]  # fmt: skip
    )
    result, _ = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    sent = bodies(route)
    assert len(sent) == 4
    assert all("tools" in b for b in sent[:3]) and "tools" not in sent[3]
    assert result.tool_rounds == 3 and result.finish_reason == "tool_limit"
    assert [e.ref for e in result.evidence] == ["E1", "E2", "E3"]
    assert result.answer == "모은 기록으로 답해요."


@respx.mock
async def test_tool_limit_answer_drops_thought_line_without_marker(model):
    def call(period: str) -> tuple[str, dict]:
        return ("get_metric_summary", {"metric": "steps", "period": period})

    # 도구 없이 보낸 한도 호출에 표시 없는 머리글 줄과 답변이 한 조각으로 온 실제 사례
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(call("last_7_days")),
            tool_call_response(call("last_14_days")),
            tool_call_response(call("last_30_days")),
            text_response("thought\n모은 기록으로 답해요."),
        ]  # fmt: skip
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert result.finish_reason == "tool_limit"
    assert [e["delta"] for e in events if e["type"] == "token"] == ["모은 기록으로 답해요."]
    assert result.answer == "모은 기록으로 답해요."


def limit_rounds(first_text: tuple[str, ...] = ()) -> list[httpx.Response]:
    """한도 호출 전 도구 왕복 3회의 응답. first_text 는 첫 응답에서 도구 호출 앞에 오는 본문 조각이다."""
    periods = ("last_7_days", "last_14_days", "last_30_days")
    calls = [("get_metric_summary", {"metric": "steps", "period": p}) for p in periods]
    return [tool_call_response(c, text=first_text if i == 0 else ()) for i, c in enumerate(calls)]


@pytest.mark.parametrize(
    ("parts", "deltas"),
    [
        (["<|tool_call>", "call:get_health_metrics{}", "<tool_call|>"], [TOOL_LIMIT_ANSWER]),
        (["<|channel>thought\n<channel|>", "<|tool_call>call:get_health_metrics{}<tool_call|>"], [TOOL_LIMIT_ANSWER]),
        # 표기 앞의 공백은 이미 내보낸 조각이라 그 뒤에 안내를 붙인다
        (["\n", "<|tool_call>call:get_health_metrics{}<tool_call|>"], ["\n", TOOL_LIMIT_ANSWER]),
    ],
)
@respx.mock
async def test_tool_limit_answer_of_only_tool_call_text_is_replaced(model, parts, deltas):
    respx.post(CHAT_URL).mock(side_effect=[*limit_rounds(), text_response(*parts)])
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()}, message_id="m-1")

    kinds = [e["type"] if e["type"] != "status" else f"status:{e['stage']}" for e in events]
    assert kinds[-3 - len(deltas) :] == ["status:answering", "evidence", *["token"] * len(deltas), "done"]
    assert [e["delta"] for e in events if e["type"] == "token"] == deltas
    assert result.answer == result.messages[-1].content == "".join(deltas)
    assert result.finish_reason == "tool_limit" and [e.ref for e in result.evidence] == ["E1", "E2", "E3"]
    assert (events[-1]["message_id"], events[-1]["finish_reason"]) == ("m-1", "stop")


@respx.mock
async def test_tool_limit_answer_keeps_text_before_tool_call_text(model):
    tail = text_response("모은 기록으로 ", "답해요.", "<|tool_call>", "call:get_health_metrics{}", "<tool_call|>")
    respx.post(CHAT_URL).mock(side_effect=[*limit_rounds(), tail])
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert streamed(events) == result.answer == result.messages[-1].content == "모은 기록으로 답해요."
    assert result.finish_reason == "tool_limit"


@respx.mock
async def test_tool_limit_answer_follows_text_sent_in_earlier_round(model):
    # 앞 왕복에서 도구 호출 앞에 보낸 글은 답변에 남으므로, 한도 호출에서 남는 글이 없으면 그 뒤에 안내를 붙인다
    tail = text_response("<|tool_call>call:get_health_metrics{}<tool_call|>")
    respx.post(CHAT_URL).mock(side_effect=[*limit_rounds(first_text=("기록을 확인해 볼게요. ",)), tail])
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert [e["delta"] for e in events if e["type"] == "token"] == ["기록을 확인해 볼게요. ", TOOL_LIMIT_ANSWER]
    assert result.answer == "기록을 확인해 볼게요. " + TOOL_LIMIT_ANSWER
    assert result.messages[-1].content == TOOL_LIMIT_ANSWER


def readable(body: bytes) -> str:
    """본문의 문자열 이스케이프 방식과 관계없이 글을 찾도록 JSON 을 읽어 다시 직렬화한다."""
    return json.dumps(json.loads(body), ensure_ascii=False)


@respx.mock
async def test_tool_limit_notice_goes_only_into_limit_call(model):
    handler = RecordingHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[*limit_rounds(), text_response("모은 기록으로 답해요."), *limit_rounds(), text_response("네.")]
    )
    result, events = await run(model, handlers={"get_metric_summary": handler})
    # 같은 응답으로 한도를 하나 늘리면 네 번째 호출도 도구를 묶은 일반 호출이다
    unlimited, _ = await run(model, handlers={"get_metric_summary": handler}, max_tool_rounds=4)

    sent = [call.request.content for call in route.calls]
    assert len(sent) == 8 and (result.finish_reason, unlimited.finish_reason) == ("tool_limit", "stop")
    # 한도에 걸리지 않은 호출의 본문은 한도 설정과 관계없이 같다
    assert sent[:3] == sent[4:7]
    limited, normal = json.loads(sent[3]), json.loads(sent[7])
    assert "tools" not in limited and "tools" in normal
    # 한도 호출에 붙이는 래퍼 옵션은 요청 본문에 들어가지 않는다
    assert set(limited) == set(normal) - {"tools"}
    assert limited["messages"][:-1] == normal["messages"][:-1]
    assert limited["messages"][-1]["role"] == "tool"
    assert limited["messages"][-1]["content"] == f"{normal['messages'][-1]['content']}\n\n{TOOL_LIMIT_NOTICE}"
    assert all(TOOL_LIMIT_NOTICE not in readable(body) for i, body in enumerate(sent) if i != 3)
    # 안내는 요청 본문에만 들어가고 이벤트·근거·답변·결과 메시지에는 들어가지 않는다
    shown = [
        json.dumps(events, ensure_ascii=False),
        json.dumps([e.event_item() for e in result.evidence], ensure_ascii=False),
        result.answer,
        *[str(m.content) for m in result.messages],
    ]
    assert all(TOOL_LIMIT_NOTICE not in text for text in shown)


@respx.mock
async def test_tool_limit_notice_is_added_to_last_tool_result_only(model):
    two = [("get_metric_summary", {"metric": "steps", "period": p}) for p in ("last_7_days", "last_14_days")]
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            *limit_rounds()[:2],
            tool_call_response(*two, call_ids=["call_a", "call_b"]),
            text_response("모은 기록으로 답해요."),
        ]
    )
    await run(model, handlers={"get_metric_summary": RecordingHandler()})

    tools = [m for m in bodies(route)[3]["messages"] if m["role"] == "tool"]
    assert [m["content"].endswith(f"\n\n{TOOL_LIMIT_NOTICE}") for m in tools] == [False, False, False, True]


@respx.mock
async def test_tool_limit_notice_needs_tool_result(model):
    # 한도가 0이면 첫 호출부터 도구 없이 보낸다. 덧붙일 도구 결과가 없어 질문을 그대로 보낸다
    route = respx.post(CHAT_URL).mock(return_value=text_response("잠들기 전 화면을 줄여 보세요."))
    result, _ = await run(model, question="잠을 잘 자려면?", max_tool_rounds=0)

    body = bodies(route)[0]
    assert "tools" not in body and body["messages"][-1] == {"role": "user", "content": "잠을 잘 자려면?"}
    assert result.finish_reason == "tool_limit" and TOOL_LIMIT_NOTICE not in readable(route.calls.last.request.content)


@respx.mock
async def test_user_id_from_llm_is_rejected(model):
    handler = RecordingHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "today", "user_id": "u2"})),
            text_response("조회할 수 없었어요."),
        ]
    )
    result, _ = await run(model, handlers={"get_metric_summary": handler})

    assert handler.user_ids == []
    assert bodies(route)[1]["messages"][-1]["content"] == DENIED_MESSAGE
    assert result.evidence == []


@respx.mock
async def test_guard_ends_without_llm(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result, events = await run(model, question="가슴이 답답하고 숨을 못 쉬겠어요")

    assert route.call_count == 0
    assert result.finish_reason == "guard" and result.blocked_by == "emergency"
    assert "119" in result.answer
    assert [e["type"] for e in events] == ["status", "token", "done"]
    assert events[-1]["finish_reason"] == "stop"


MEDICATION_TURN = history_turn(
    1, "멜라토닌 먹어도 돼?", 2, "약이나 복용에 대해서는 안내해 드릴 수 없어요. 의사나 약사와 상담해 주세요."
)


@respx.mock
async def test_guard_blocks_dose_follow_up_of_previous_medication_question(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    ctx = replace(CTX, turns=(MEDICATION_TURN,))
    result, events = await run(model, question="그럼 하루 몇 mg이 적당해?", ctx=ctx)

    assert route.call_count == 0
    assert result.finish_reason == "guard" and result.blocked_by == "medication"
    assert [e["type"] for e in events] == ["status", "token", "done"]


@respx.mock
async def test_guard_sends_dose_question_without_previous_question_to_llm(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result, _ = await run(model, question="그럼 하루 몇 mg이 적당해?")

    assert route.call_count == 1
    assert result.finish_reason == "stop" and result.blocked_by is None


@respx.mock
async def test_guard_blocks_same_emergency_symptom_follow_up(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    turn = history_turn(1, "가슴이 아파", 2, "말씀하신 증상은 응급 상황일 수 있어요.")
    result, _ = await run(model, question="지금도 아파", ctx=replace(CTX, turns=(turn,)))

    assert route.call_count == 0
    assert result.finish_reason == "guard" and result.blocked_by == "emergency"


@respx.mock
async def test_guard_checks_only_latest_previous_question(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    # 약 질문 뒤에 다른 질문이 있으면 그 앞의 약 질문은 참조하지 않는다
    turns = (MEDICATION_TURN, history_turn(3, "어제 몇 걸음 걸었어?", 4, "8,000걸음이에요."))
    result, _ = await run(model, question="그럼 하루 몇 mg이 적당해?", ctx=replace(CTX, turns=turns))

    assert route.call_count == 1 and result.blocked_by is None


@respx.mock
async def test_llm_error_becomes_error_event(settings):
    settings.llm_max_retries = 0
    model = create_chat_model(settings, recorder=MetricsRecorder())
    respx.post(CHAT_URL).mock(return_value=httpx.Response(503, json={"error": {"message": "down"}}))
    result, events = await run(model)

    assert result.finish_reason == "error" and result.error_code == "llm_unavailable"
    assert events[-1] == {
        "type": "error", "conversation_id": "c-1", "request_id": "r-1",
        "code": "llm_unavailable", "message": events[-1]["message"], "retryable": True, "metrics": [],
    }  # fmt: skip
    assert not any(e["type"] == "done" for e in events)


def broken_stream(text: str) -> httpx.Response:
    body = ScriptedStream(
        [
            sse_event(chunk({"role": "assistant", "content": ""})),
            sse_event(chunk({"content": text})),
            httpx.RemoteProtocolError("peer closed connection"),
        ]
    )
    return httpx.Response(200, stream=body, headers=SSE_HEADERS)


@respx.mock
async def test_answer_on_error_is_text_already_sent(model):
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            broken_stream("지난 7일 "),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert result.finish_reason == "error" and result.error_code == "llm_unavailable"
    assert streamed(events) == result.answer == "지난 7일 "


@respx.mock
async def test_first_call_error_sends_no_held_text(model):
    respx.post(CHAT_URL).mock(return_value=broken_stream("지난 7일 "))
    result, events = await run(model)

    # 첫 호출의 답 조각은 응답이 끝날 때까지 모아 두므로 도중에 끊기면 보낸 글이 없다
    assert result.finish_reason == "error" and result.error_code == "llm_unavailable"
    assert streamed(events) == result.answer == ""


@respx.mock
async def test_handler_error_ends_with_internal(model):
    async def broken(inp, req):
        raise RuntimeError(SECRET)

    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("기록을 확인하지 못했어요."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": broken})

    error = events[-1]
    assert (error["type"], error["code"], error["message"]) == ("error", "internal", INTERNAL_MESSAGE)
    assert SECRET not in json.dumps(events, ensure_ascii=False)
    assert result.finish_reason == "error" and result.error_code == "internal"
    assert isinstance(result.exception, RuntimeError)
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("error", "code", "retryable", "metrics"),
    [
        (HealthDataNotLinkedError(SECRET), "user_not_linked", True, []),
        (HealthDataNotReadyError(SECRET, metrics=["steps"]), "data_not_ready", True, ["steps"]),
        (
            HealthDataUnavailableError(SECRET, metrics=["sleep", "heart_rate", "sleep"]),
            "data_unavailable",
            True,
            ["sleeps", "heart_rates"],
        ),
        (HealthDataForbiddenError(SECRET), "forbidden", False, []),
    ],
)
@respx.mock
async def test_port_error_ends_with_its_code_and_public_metrics(model, error, code, retryable, metrics):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("평균은 7,000걸음쯤이에요."),
        ]
    )
    result, events = await run(model, port=FailingPort(error))

    assert events[-1] == {
        "type": "error", "conversation_id": "c-1", "request_id": "r-1",
        "code": code, "message": error.message, "retryable": retryable, "metrics": metrics,
    }  # fmt: skip
    assert SECRET not in json.dumps(events, ensure_ascii=False)
    assert result.finish_reason == "error" and result.error_code == code and result.exception is error
    assert route.call_count == 1 and streamed(events) == ""


class ContractBreakingPort(FakeHealthData):
    """공통 원형을 계약과 다르게 바꿔 반환하는 잘못된 구현."""

    def __init__(self, change) -> None:
        super().__init__(steps={"u1": [StepDay(date(2026, 10, d), 7412) for d in (9, 10)]}, now=NOW)
        self.change = change

    async def get_metric_result(self, user_id, metric, start, end):
        result = await super().get_metric_result(user_id, metric, start, end)
        snapshot = copy.deepcopy(dict(result.snapshot))
        self.change(snapshot)
        return MetricResult(user_id, snapshot)


CONTRACT_BREAKS = {
    "missing_field": lambda s: s.pop("data_status"),
    "naive_time": lambda s: s["data_status"].update(checked_at="2026-10-15T03:00:00"),
    "owner_in_snapshot": lambda s: s.update(user_id="u1"),
    "wrong_type": lambda s: s["summary"].update(completed_days="6일"),
    "other_metric": lambda s: s.update(metric="sleeps"),
    "other_period": lambda s: s.update(period={"start": "2026-10-08", "end": "2026-10-14"}),
}


@pytest.mark.parametrize("case", [*CONTRACT_BREAKS, "lookup_failure"])
@respx.mock
async def test_contract_violation_ends_with_internal_but_lookup_failure_keeps_its_code(model, case):
    if case == "lookup_failure":
        port, code, metrics = (
            FailingPort(HealthDataUnavailableError(SECRET, metrics=["steps"])),
            "data_unavailable",
            ["steps"],
        )
    else:
        port, code, metrics = ContractBreakingPort(CONTRACT_BREAKS[case]), "internal", []
    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"}))]
    )
    audit = MemoryAuditSink()
    result, events = await run(model, port=port, audit=audit)

    assert (events[-1]["type"], events[-1]["code"], events[-1]["metrics"]) == ("error", code, metrics)
    assert result.error_code == code
    assert [(r.decision, r.reason, r.result_count) for r in audit.records()] == [("allow", code, 0)]


@respx.mock
async def test_setup_error_ends_with_internal(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result, events = await run(model, prompt_version="v0")  # 없는 프롬프트 버전

    assert [e["type"] for e in events] == ["error"] and events[0]["code"] == "internal"
    assert result.finish_reason == "error" and route.call_count == 0


@respx.mock
async def test_sink_error_does_not_raise(model):
    respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result = await run_coach(
        CTX, "지난 7일 걸음 수 알려줘", port=FakeHealthData(), model=model, sink=BrokenSink(), now=NOW
    )
    assert result.finish_reason == "error" and result.error_code == "internal"


@respx.mock
async def test_deadline_cancel_is_not_swallowed(model):
    async def slow(inp, req):
        await asyncio.sleep(10)

    respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"}))]
    )
    sink = ListSink()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await run_coach(CTX, "지난 7일 걸음 수 알려줘", port=FakeHealthData(), model=model, sink=sink, now=NOW,
                            handlers={"get_metric_summary": slow})  # fmt: skip
    assert not any(e["type"] in ("done", "error") for e in sink.events)


async def test_request_ends_only_once():
    events = EventEmitter(CTX, ListSink())
    await events.error("internal", INTERNAL_MESSAGE, True)
    with pytest.raises(RuntimeError):
        await events.done("m-1", "stop")
    with pytest.raises(RuntimeError):
        await events.error("internal", INTERNAL_MESSAGE, True)


@respx.mock
async def test_length_finish_reason_is_kept(model):
    respx.post(CHAT_URL).mock(return_value=text_response("잘린 답", finish_reason="length"))
    result, events = await run(model, question="잠을 잘 자려면?")
    assert result.finish_reason == "length" and events[-1]["finish_reason"] == "length"


def test_prompt_order_system_summary_history_question():
    history = [ChatTurn("user", "어제 몇 걸음 걸었어?"), ChatTurn("assistant", "8,000걸음이에요.")]
    messages = build_messages("오늘은?", "걸음 수를 자주 묻는다.", history, load_system_prompt())

    assert [type(m) for m in messages] == [SystemMessage, HumanMessage, AIMessage, HumanMessage]
    system = messages[0].text
    assert system.startswith(load_system_prompt())  # 고정 내용이 앞에 와야 서버 캐시가 재사용된다
    assert system.endswith("걸음 수를 자주 묻는다.")
    assert messages[-1].text == "오늘은?"
    assert "2026" not in system


def test_no_summary_keeps_system_prompt_only():
    messages = build_messages("질문", None, [], load_system_prompt())
    assert messages[0].text == load_system_prompt() and len(messages) == 2


UNSUPPORTED_PERIOD_RULE = (
    "- 기간은 도구의 기간 목록에서만 고른다. "
    "목록의 어느 기간으로도 덮을 수 없는 기간(예: 3주 전, 지지난달, 두 달 전)은 비슷한 기간으로 바꿔 조회하지 않는다. "
    "3주 전을 지지난주로, 지지난달을 지난달로 바꾸지 않는다. "
    "이때만 조회하지 않고, 그 기간은 확인할 수 없다고 말한 뒤 확인할 수 있는 기간(예: 지지난주, 지난달, 최근 30일)을 "
    "제안한다. 그 기간의 기록을 있다고 말하지 않는다. "
    "낮잠, 평일처럼 기간이 아닌 조건이 붙은 질문은 먼저 조회하고 결과를 보고 답한다."
)


def test_v1_4_has_refetch_missing_and_unsupported_period_rules():
    prompt = load_system_prompt("v1.4")
    assert (
        "건강 수치는 이전 답변에 있더라도 매번 도구로 다시 조회하고, 이전 답변의 수치를 근거로 쓰지 않는다." in prompt
    )
    assert "결측 사유가 기록 없음일 때만 기록이 없다고 말한다." in prompt
    assert "도구가 지원하지 않는다는 결과나 요약에 그날 값이 없다는 것은 기록이 없다는 뜻이 아니다." in prompt
    assert prompt.count(UNSUPPORTED_PERIOD_RULE) == 1
    # 조회하지 않는 경우를 기간 질문으로 좁힌다. 기록 전반을 가리키는 문장은 쓰지 않는다
    assert "확인할 수 없는 기록을 있다고 말하지 않는다" not in prompt


@pytest.mark.parametrize(
    ("version", "sha"),
    [
        ("v1.2", "15be7bb1aa7a"),
        ("v1.3", "42619aa57bf6"),
        ("v1.4", "26ae76ae7c5c"),
        ("v1.5", "27490b4b1960"),
        ("v1.6", "3b7fc71eadcc"),
        ("v1.9", "0bb4f368309d"),
        ("v1.10", "7d3f14b189a1"),
        ("v1.11", "d9ce9407dbf7"),
        ("v1.12", "597f9721d328"),
    ],
)
def test_measured_prompts_with_date_stay(version, sha):
    """측정한 이전 프롬프트 파일 → 템플릿 sha256 앞 12자리가 기록과 같다."""
    # 지난 측정에 쓴 시스템 프롬프트 파일이다. 같은 조건으로 다시 잴 수 있게 바꾸지 않는다
    prompt = load_system_prompt(version)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12] == sha


def test_v1_4_is_v1_2_with_unsupported_period_rule_in_value_section():
    template = load_system_prompt("v1.4")
    # 다른 문장은 그대로 두고 수치 절의 도구 호출 규칙 바로 뒤에 한 줄만 더한다
    assert template.replace(UNSUPPORTED_PERIOD_RULE + "\n", "", 1) == load_system_prompt("v1.2")
    lines = template.splitlines()
    rule = lines.index(UNSUPPORTED_PERIOD_RULE)
    assert lines[rule - 1].startswith("- 사용자의 수치가 필요하면 도구를 호출한다.")
    assert lines[: rule - 1].count("## 수치") == 1
    assert template.endswith("오늘은 {today}이다.")


# 기간 표현 표의 종류·표현 칸. 예 칸은 문항과 같은 문장을 피해 고른다
PERIOD_TABLE_ROWS = (
    "| N일 전 하루 | days_ago:N |",
    "| 최근 N일 (오늘 포함) | recent_days:N |",
    "| N주 전 한 주 (월-일) | weeks_ago:N |",
    "| N달 전 한 달 (1일-말일) | months_ago:N |",
    "| 최근 N달 | recent_months:N |",
    "| 날짜 하루 | date:MM-DD |",
    "| 날짜 범위 | dates:MM-DD..MM-DD |",
    "| 요일 하루 (오늘 전 가장 최근) | weekday:요일코드 |",
)


@pytest.mark.parametrize("version", ["v1.5", "v1.6"])
def test_prompt_has_period_expression_table(version):
    prompt = load_system_prompt(version)
    for row in PERIOD_TABLE_ROWS:
        assert prompt.count(row) == 1, row
    # 날짜 계산은 서버가 한다. 질문의 수와 날짜만 옮긴다
    assert "날짜는 서버가 계산하므로 질문에 나온 수와 날짜만 옮긴다." in prompt
    assert "날짜를 말하면 date: 로 그날만 조회한다." in prompt
    # 요일만 말하면 서버가 그날을 계산하고, 주와 요일을 말하면 그 주를 날짜별로 조회해 그 요일을 찾는다
    weekday_rule = (
        "- 요일 이름만으로 말한 지난 하루는 어느 요일이든 weekday:요일코드 로 그날만 조회한다. "
        "지난주 X요일처럼 주를 말하면 그 주를 날짜별 조회(날짜별 추이·수면 기록·운동 세션)로 보고 그 요일을 찾는다. "
        "이때 하루를 묻는 질문에는 기간 요약을 쓰지 않는다."
    )
    assert prompt.count(weekday_rule) == 1
    assert "그 날이 든 주를 조회한다" not in prompt
    assert prompt.count("어젯밤 잠은 days_ago:0, 그저께 밤 잠은 days_ago:1 로 조회한다.") == 1
    # 이름 기간은 해석에서 계속 받지만 보여 주지 않는다. 템플릿의 날짜 자리({today})는 날짜를 넣은 뒤 검사한다
    rendered = render_system_prompt(prompt, date(2026, 10, 22))
    for period in PERIODS:
        assert not re.search(rf"\b{period}\b", rendered), period


def _table_examples(prompt: str) -> dict[str, str]:
    """기간 표현 표의 종류 칸 → 예 칸."""
    rows = [line.split("|")[1:-1] for line in prompt.splitlines() if line.startswith("| ") and "---" not in line]
    return {cells[0].strip(): cells[2].strip() for cells in rows if len(cells) == 3}


def test_v1_5_gives_weekday_and_weekend_as_rules_without_examples():
    """v1.5 → 요일·주말을 예 대신 규칙으로 쓰고, 요일 이름을 규칙에 넣지 않는다."""
    prompt = load_system_prompt("v1.5")
    examples = _table_examples(prompt)
    # 요일 행의 예 칸에는 질문 예 대신 요일 코드를 둔다. 해석이 받는 요일 이름과 같은 순서다
    codes = "월 mon, 화 tue, 수 wed, 목 thu, 금 fri, 토 sat, 일 sun"
    assert examples["요일 하루 (오늘 전 가장 최근)"] == codes
    assert [pair.split()[1] for pair in codes.split(", ")] == list(WEEKDAYS)
    # 주말은 예 칸에 넣지 않고 뜻을 정한다
    assert examples["N주 전 한 주 (월-일)"] == "이번 주 weeks_ago:0, 지난주 weeks_ago:1, 지지난주 weeks_ago:2"
    weekend_rule = "- 주말은 토·일이다. 지난 주말은 지난주(weeks_ago:1)의 토·일이므로 그 주를 날짜별로 조회해 찾는다."
    assert prompt.count(weekend_rule) == 1
    # 요일만 말한 날의 예(지난 토요일 weekday:sat 같은 측정 문항 표현)는 넣지 않는다
    for day in "월화수목금토일":
        assert f"지난 {day}요일" not in prompt, day
    assert not re.search(rf"weekday:({'|'.join(WEEKDAYS)})", prompt)
    # 규칙과 도구 설명에 특정 요일 이름을 넣지 않는다. 예에 든 요일은 그 요일을 묻는 질문의 기간 해석을 바꿀 수 있다
    tools = json.dumps(tool_specs(), ensure_ascii=False)
    assert not re.search("[월화수목금토일]요일", prompt) and not re.search("[월화수목금토일]요일", tools)
    assert "지난주 X요일처럼 주를 말하면" in prompt and "주와 요일로 말한 날(지난주 X요일)" in tools


def test_v1_5_guides_only_periods_the_tool_rejects():
    prompt = load_system_prompt("v1.5")
    # 비슷한 기간으로 바꿔 조회하지 않는다는 원칙은 그대로 둔다
    assert "질문한 기간을 비슷한 기간으로 바꾸거나 줄여서 조회하지 않는다." in prompt
    rule = (
        "도구가 그 기간은 조회할 수 없다고 안내하면(unavailable_period) 그 기간은 확인할 수 없다고 말한 뒤 "
        "안내에 따라 확인할 수 있는 기간을 제안한다. 그 기간의 기록을 있다고 말하지 않는다."
    )
    assert prompt.count(rule) == 1
    # 목록으로 덮을 수 없는 기간을 조회하지 않던 v1.4 규칙은 뺀다
    assert UNSUPPORTED_PERIOD_RULE not in prompt and "목록의 어느 기간으로도" not in prompt
    # 한도 수치는 도구 안내에만 둔다
    for words in ("12개월", "31일"):
        assert words not in prompt


def test_v1_5_keeps_other_v1_4_lines_in_order():
    rest = [line for line in load_system_prompt("v1.4").splitlines() if line != UNSUPPORTED_PERIOD_RULE]
    lines = iter(load_system_prompt("v1.5").splitlines())
    assert all(line in lines for line in rest)
    # v1.4 규칙 끝의 조건 문장은 기간 절에 남긴다
    condition = "- 낮잠, 평일처럼 기간이 아닌 조건이 붙은 질문은 먼저 조회하고 결과를 보고 답한다."
    assert condition in load_system_prompt("v1.5")
    assert load_system_prompt("v1.5").endswith("오늘은 {today}이다.")


MARKDOWN_BAN = "마크다운 기호(**, #, -, 표)를 쓰지 않고 문장으로 답한다. 여러 항목은 줄을 바꿔 문장으로 나열한다."
# 화면과 정한 답변 형식 약속을 규칙으로 쓴 절 (docs/agent-interface.md 5장 "답변 형식")
ANSWER_FORMAT = (
    "## 답변 형식",
    "",
    "- 문단으로 답하고, 필요할 때만 목록·표를 쓴다. 굵게(**), 제목(#), 링크, 코드 블록, HTML 은 쓰지 않는다.",
    "- 첫 문단은 질문에 대한 답 한 문장으로 쓴다. "
    "비교 결과의 reliable 이 false 이면 첫 문단에서도 증감을 단정하지 않고, "
    "reliable_note 나 in_progress_note 에 있는 이유를 같은 문장에 넣는다.",
    "- 짧은 답은 문단만 쓴다.",
    "- 목록(- )은 조언처럼 여러 항목을 나열할 때 쓴다.",
    "- 표는 여러 날이나 여러 지표를 나란히 비교할 때만 쓴다. 표 앞뒤에는 빈 줄을 둔다.",
    "- 표는 비교에 필요한 열만 두고, 첫 열에는 날짜나 지표 이름 같은 항목 이름을, "
    "칸에는 본문과 같은 표기로 단위까지 쓴다.",
    "- 표의 모든 행은 | 로 시작해 | 로 끝내고, 머리 행 다음 줄은 열마다 --- 를 쓴 구분선이다.",
)


def test_default_prompt_is_v1_20():
    """기본 버전은 수면 점수 계산 후속 질문의 재조회 규칙을 포함한다."""
    assert DEFAULT_PROMPT_VERSION == "v1.20"
    assert load_system_prompt() == load_system_prompt("v1.20")


def test_v1_6_replaces_markdown_ban_with_answer_format_section():
    v1_5 = load_system_prompt("v1.5").splitlines()
    assert v1_5[1] == MARKDOWN_BAN
    # 역할 문장 다음 줄의 마크다운 금지 문장만 답변 형식 절로 바꾸고, 기간 절을 포함한 나머지 줄은 그대로 둔다
    v1_6 = load_system_prompt("v1.6").splitlines()
    assert v1_6 == [v1_5[0], "", *ANSWER_FORMAT, *v1_5[2:]]
    assert "마크다운 기호" not in load_system_prompt("v1.6")


def test_v1_6_answer_format_has_rules_without_examples():
    rules = "\n".join(ANSWER_FORMAT)
    # 측정 문항의 질문·답과 겹치지 않게 표 모양을 예로 보이지 않는다. 수치와 표 줄이 없다
    assert not re.search(r"\d", rules)
    assert not any(line.startswith("|") for line in ANSWER_FORMAT)
    # 구분선은 열마다 둔다. 머리 행보다 칸이 적은 구분선은 화면이 표로 그리지 않는다
    assert "|---|" not in rules and "열마다 --- 를 쓴 구분선" in rules
    # 굵게는 쓰지 않는 형식이다. 화면이 첫 문단 첫 줄을 큰 요약 글씨로 그려 강조는 그쪽에 맡긴다
    assert rules.count("굵게") == 1 and load_system_prompt("v1.6").count("**") == 1
    assert "굵게(**), 제목(#), 링크, 코드 블록, HTML 은 쓰지 않는다." in rules
    assert rules.count("첫 문단은 질문에 대한 답 한 문장으로 쓴다.") == 1
    # 첫 문단 규칙이 reliable 이 false 인 비교에서 증감을 단정하게 만들지 않도록 잇는다. 이유는 비교 결과의 안내 칸에
    # 있다. 기록이 적으면 reliable_note, 오늘이 진행 중이라 차이를 계산하지 않았으면 in_progress_note 다
    rule = (
        "비교 결과의 reliable 이 false 이면 첫 문단에서도 증감을 단정하지 않고, "
        "reliable_note 나 in_progress_note 에 있는 이유를 같은 문장에 넣는다."
    )
    assert rules.count(rule) == 1
    assert {"reliable", "reliable_note", "in_progress_note"} <= set(PeriodComparison.model_fields)
    # 오늘이 진행 중인 비교에도 기록이 적다고 쓰게 하던 문장은 뺀다
    assert "기록이 적다" not in rules


VALUE_CALL = (
    "- 사용자의 수치가 필요하면 도구를 호출한다. "
    "조회 대상 사용자는 서버가 정하므로 사용자를 가리키는 인자는 넣지 않는다."
)
V1_6_NOT_IN_RESULTS = "- 도구 결과에 없는 내용은 기록으로 확인할 수 없다고 말한다."
V1_6_IN_PROGRESS = (
    "- in_progress 는 오늘 진행 중인 값이다. 하루가 끝나지 않은 값이라고 밝히고 기준 시각을 함께 말한다. "
    '예: "오늘 12시 5분까지 기록 기준 4,210걸음". 완성된 날의 평균·합계와 합치지 않는다.'
)
V1_6_DONTS = (
    "## 하지 않는 것",
    "",
    "- 질병이 있는지 판단하지 않는다.",
    "- 약, 처방, 복용을 안내하지 않는다.",
    "- 걱정되는 증상이 있으면 의료진 상담을 권한다.",
)
OTHER_PERSON_RULE = (
    "- 조회 결과는 언제나 사용자 본인의 기록이다. "
    "사용자가 아닌 다른 사람의 기록이나 수치를 물으면 도구를 호출하지 않고, "
    "다른 사람의 기록은 확인할 수 없다고 답한다. "
    "다른 사람의 기록을 조회할 수 있는 것처럼 제안하지 않고, 본인의 값을 다른 사람의 값으로 말하지 않는다. "
    "다른 사람이 나오더라도 사용자 본인의 기록을 물으면 본인 기록으로 답한다."
)
# v1.6 의 줄 → v1.7 에서 그 자리에 들어가는 줄
V1_7_REPLACED = {
    # 기록이 적어 reliable 이 false 여도 차이 값은 말한다. 증감을 단정하지 않는 규칙과 한 문장으로 합쳤다
    ANSWER_FORMAT[3]: (
        "- 첫 문단은 질문에 대한 답 한 문장으로 쓴다. "
        "비교 결과의 reliable 이 false 이면 첫 문단에서도 증감을 단정하지 않고, "
        "reliable_note 나 in_progress_note 에 있는 이유를 같은 문장에 넣되, difference 가 있으면 차이 값은 말한다.",
    ),
    VALUE_CALL: (VALUE_CALL, OTHER_PERSON_RULE),
    V1_6_NOT_IN_RESULTS: (
        "- 도구 결과에 없는 내용(점수, 오늘의 한마디, 몸무게·체지방, 혈압, 혈당, 하루 전체 소모 칼로리, 착용 시간, "
        "시간대별 걸음 등)은 지어내지 않고 기록으로 확인할 수 없다고 말한다. "
        "점수와 오늘의 한마디는 홈 화면에서 볼 수 있다고 안내한다.",
        # 홈 화면의 안정 시 심박수는 주 수면 구간의 평균 심박이고, 도구의 heart_rate 는 하루 평균 심박이다
        "- 홈 화면의 안정 시 심박수는 자는 동안 잰 평균 심박이다. "
        "도구 결과에 이 값이 없으면 확인할 수 없다고 말하고, 하루 평균 심박과 섞지 않는다.",
    ),
    V1_6_IN_PROGRESS: (
        V1_6_IN_PROGRESS.removesuffix(" 합치지 않는다.")
        + " 합치지 않고, 가장 낮은·높은 날을 고르거나 날짜끼리 비교할 때도 넣지 않는다.",
    ),
}
# v1.6 의 "하지 않는 것" 절 자리. 안전 규칙을 통과해 LLM 으로 넘어온 질문의 답을 맡는다
V1_7_SAFETY = (
    "## 진단과 약",
    "",
    "- 질병이 있는지 판단하지 않는다. "
    "기록이나 상태를 진단해 달라고 하면 병명을 말하지 않고 기록 요약으로 답하며, "
    "한 지표를 가리키면 그 지표만 조회한다.",
    "- 질환이 있다고 밝힌 질문은 일반 생활 습관 범위에서만 답하고, 목표와 운동 강도는 의료진과 정하라고 함께 말한다. "
    "질환 관리나 치료 조언은 하지 않는다.",
    "- 약의 처방·복용·용량은 안내하지 않는다. 앞 대화에서 물은 약을 이어서 물어도 용량과 복용 시점은 안내하지 않는다.",
    "- 약을 쓰지 않는 방법을 물으면 생활 습관만 안내하고, 복용 중인 약을 끊거나 줄이라고 말하지 않는다.",
    "- 호르몬이나 영양 성분을 물으면 그 성분이 든 음식과 생활 습관만 말하고, 보충제와 용량은 안내하지 않는다. "
    "음식이나 습관의 효과는 단정하지 않는다.",
    "- 운동 처방이나 운동 계획 요청은 약 처방이 아니므로 거절하지 않고, 기록에 맞춘 운동 계획으로 답한다.",
    "",
    "## 증상과 위기",
    "",
    "- 증상을 말하면 계속되거나 걱정될 때 의료진과 상담하라고 권한다. "
    "숨쉬기 어렵거나 가슴 통증·실신이 함께 있으면 119를 안내한다.",
    "- 비유로 쓴 증상 표현, 운동 중 흔한 증상, 없다고 말한 증상이 든 질문도 거절하지 않고 함께 물은 기록에 답한다.",
    "- 두근거림 같은 심장 증상은 심박 기록으로 판단할 수 없다고 말하고 진료를 권한다. 괜찮다고 안심시키지 않는다.",
    "- 삶을 끝내려는 뜻으로 읽힐 수 있는 표현에는 공감하고 "
    "자살예방상담전화 109와 정신건강위기상담전화 1577-0199를 안내한다. "
    "앞 대화에 이 상담전화 안내가 있었으면 이어지는 대화에서도 다시 안내한다.",
    "",
    "## 범위 밖 요청",
    "",
    "- 건강 코칭 밖의 요청과 기록 요청이 섞이면 기록 부분만 답하고, 나머지는 답하지 않는다고 말한다. "
    "건강 코칭 밖의 주제가 원인이나 상황으로만 나오면 건강 코칭으로 답하고, "
    "함께 말한 자기 기록은 되묻지 않고 먼저 조회한다.",
)
DATASETS_DIR = Path(__file__).resolve().parents[1] / "eval" / "datasets"


def test_v1_7_adds_safety_and_screen_rules_to_v1_6():
    v1_6 = load_system_prompt("v1.6").splitlines()
    start = v1_6.index(V1_6_DONTS[0])
    assert tuple(v1_6[start : start + len(V1_6_DONTS)]) == V1_6_DONTS
    assert all(v1_6[:start].count(line) == 1 for line in V1_7_REPLACED)
    # 바꾼 줄 밖의 줄(기간 절 포함)은 v1.6 그대로이고, 하지 않는 것 절 자리에 안전 규칙 절이 들어간다
    expected = [new for line in v1_6[:start] for new in V1_7_REPLACED.get(line, (line,))]
    expected += [*V1_7_SAFETY, *v1_6[start + len(V1_6_DONTS) :]]
    assert load_system_prompt("v1.7").splitlines() == expected
    assert load_system_prompt("v1.7").endswith("오늘은 {today}이다.")


def test_v1_7_crisis_lines_match_guard_answer():
    crisis = next(rule.answer for rule in GUARD_RULES if rule.category == "mental_crisis")
    prompt = load_system_prompt("v1.7")
    for line in ("자살예방상담전화 109", "정신건강위기상담전화 1577-0199"):
        assert line in crisis and line in prompt


def _measured_texts() -> set[str]:
    """측정에 쓰는 질문과 이전 대화 문장 (도구 선택·골든셋 데이터셋, 날짜 질문 반복 검증)."""
    texts = {target.question for target in repeated_question.TARGETS.values()}
    texts |= {turn.content for history in repeated_question.HISTORIES.values() for turn in history}
    for path in [*DATASETS_DIR.glob("tool_selection_*.jsonl"), *DATASETS_DIR.glob("golden/coaching_golden_*.jsonl")]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                texts.add(row["question"])
                texts |= {text for pair in row.get("history") or [] for text in (pair["question"], pair["answer"])}
    return texts


def _assert_no_measured_questions(prompt: str) -> None:
    texts = _measured_texts()
    # 도구 선택 문항, 골든셋 v2 의 이전 대화, 날짜 질문을 모두 읽었는지 확인한다
    assert len(texts) > 400
    assert {"그럼 오늘은 뭘 하면 돼?", "최근 일주일 평균 수면 시간 알려 줘", "요즘 내 평균 수면시간을 알려줘"} <= texts
    assert [text for text in texts if text in prompt] == []
    # 안전 규칙 변경 기록이 규칙마다 예로 든 사람·약·주제 낱말도 넣지 않고 규칙으로만 쓴다
    for word in "엄마 와이프 친구 멜라토닌 수면제 진통제 주식 선거 코인 고혈압 부정맥 기절".split():
        assert word not in prompt, word


def test_v1_7_rules_have_no_measured_questions():
    _assert_no_measured_questions(load_system_prompt("v1.7"))


def test_v1_8_rules_have_no_measured_questions():
    _assert_no_measured_questions(load_system_prompt("v1.8"))


V1_7_NOT_IN_RESULTS, V1_7_RESTING_HEART = V1_7_REPLACED[V1_6_NOT_IN_RESULTS]
V1_8_SCORE_RULE = (
    "- 점수와 등급은 점수 도구 결과에 있는 값과 이유로만 설명한다. "
    "결과에 없는 점수 구간이나 기준값, 점수를 올리는 데 필요한 양은 말하지 않는다."
)
V1_8_RESTING_HEART = (
    "- 홈 화면의 안정 시 심박수는 점수 도구 결과의 수면 중 평균 심박으로 답하고, "
    "자는 동안 잰 값임을 함께 밝히며 하루 평균 심박과 섞지 않는다."
)
# v1.7 의 줄 → v1.8 에서 그 자리에 들어가는 줄. 점수 도구가 생겨 점수를 확인할 수 없는 정보에서 뺀다
V1_8_REPLACED = {
    V1_7_NOT_IN_RESULTS: (
        "- 도구 결과에 없는 내용(오늘의 한마디, 몸무게·체지방, 혈압, 혈당, 하루 전체 소모 칼로리, 착용 시간, "
        "시간대별 걸음 등)은 지어내지 않고 기록으로 확인할 수 없다고 말한다. "
        "오늘의 한마디는 홈 화면에서 볼 수 있다고 안내한다.",
        V1_8_SCORE_RULE,
    ),
    V1_7_RESTING_HEART: (V1_8_RESTING_HEART,),
}


def test_v1_8_moves_score_out_of_unavailable_info_and_adds_score_rules():
    v1_7 = load_system_prompt("v1.7").splitlines()
    assert all(v1_7.count(line) == 1 for line in V1_8_REPLACED)
    # 바꾼 두 줄 밖의 줄(기간 절, 안전 규칙 절 포함)은 v1.7 그대로다
    expected = [new for line in v1_7 for new in V1_8_REPLACED.get(line, (line,))]
    v1_8 = load_system_prompt("v1.8")
    assert v1_8.splitlines() == expected
    # 홈 화면에서 볼 수 있다고 안내하는 것은 오늘의 한마디뿐이다
    assert "점수와 오늘의 한마디는" not in v1_8 and "도구 결과에 이 값이 없으면" not in v1_8
    assert v1_8.endswith("오늘은 {today}이다.")


def test_v1_8_rules_describe_actions_without_quotes_or_examples():
    # 답으로 그대로 옮겨질 수 있는 따옴표 문구, ~라고 답한다 형식, 예시와 수치를 새 규칙에 쓰지 않는다
    for line in (V1_8_SCORE_RULE, V1_8_RESTING_HEART):
        assert not re.search("[\"'“”‘’]", line), line
        assert "라고 답한다" not in line and "예:" not in line and not re.search(r"\d", line), line


V1_8_NOT_IN_RESULTS = V1_8_REPLACED[V1_7_NOT_IN_RESULTS][0]
# 홈 화면의 오늘의 한마디를 물을 때만 적용한다. 범위 없이 쓰면 응원 요청 거절로 넓게 읽힐 수 있다
V1_10_DAILY_MESSAGE_RULE = (
    "- 홈 화면의 오늘의 한마디를 물을 때는 한마디 도구 결과에 있는 문구와 이유로만 설명하고, "
    "그 문구를 바꾸거나 다른 문구를 오늘의 한마디라고 하지 않는다."
)
# v1.9 의 줄 → v1.10 에서 그 자리에 들어가는 줄. 한마디 도구가 생겨 한마디를 확인할 수 없는 정보와 홈 화면 안내에서
# 빼고, 안정 시 심박수 줄 뒤에 한마디 규칙을 더한다
V1_10_REPLACED = {
    V1_8_NOT_IN_RESULTS: (
        "- 도구 결과에 없는 내용(몸무게·체지방, 혈압, 혈당, 하루 전체 소모 칼로리, 착용 시간, 시간대별 걸음 등)은 "
        "지어내지 않고 기록으로 확인할 수 없다고 말한다.",
    ),
    V1_8_RESTING_HEART: (V1_8_RESTING_HEART, V1_10_DAILY_MESSAGE_RULE),
}


def test_v1_10_moves_daily_message_out_of_unavailable_info_and_adds_daily_message_rule():
    v1_9 = load_system_prompt("v1.9").splitlines()
    assert all(v1_9.count(line) == 1 for line in V1_10_REPLACED)
    # 바꾼 두 줄 밖의 줄(점수 규칙, 기간 절, 안전 규칙 절 포함)은 v1.9 그대로다
    expected = [new for line in v1_9 for new in V1_10_REPLACED.get(line, (line,))]
    v1_10 = load_system_prompt("v1.10")
    assert v1_10.splitlines() == expected
    # 오늘의 한마디를 홈 화면에서 보라고 안내하지 않고, 한마디를 다루는 줄은 한마디 규칙 하나뿐이다
    assert [line for line in v1_10.splitlines() if "한마디" in line] == [V1_10_DAILY_MESSAGE_RULE]
    assert "홈 화면에서 볼 수 있다" not in v1_10
    assert v1_10.endswith("오늘은 {today}이다.")


def test_v1_10_daily_message_rule_describes_action_without_quotes_or_examples():
    line = V1_10_DAILY_MESSAGE_RULE
    assert not re.search("[\"'“”‘’]", line), line
    assert "라고 답한다" not in line and "예:" not in line and not re.search(r"\d", line), line


def test_v1_10_rules_have_no_measured_questions():
    _assert_no_measured_questions(load_system_prompt("v1.10"))


V1_11_FORMAT_LINES = (
    "- 문단으로 답하고, 필요할 때만 목록·표·소제목을 쓴다. "
    "굵게(**), 큰 제목(#, ##), 링크, 코드 블록, HTML 은 쓰지 않는다.",
    "- 한 문단에는 한 가지 내용만 세 문장 이하로 쓴다.",
    "- 기록 설명과 조언처럼 내용이 둘 이상으로 나뉘고 각각 두 문단 이상일 때만 첫 문단 뒤에 "
    "### 소제목을 짧은 명사구로 단다. 표나 목록 하나뿐인 내용에는 달지 않는다.",
    "- 소제목을 나누려고 질문에 없는 조언이나 표를 더하지 않는다.",
    "- 목록(- )은 조언처럼 여러 항목을 나열할 때 쓰고, 항목마다 한두 문장으로 쓴다.",
    "- 표는 여러 날이나 여러 지표를 나란히 비교할 때만 쓴다. 표 앞뒤에는 빈 줄을 둔다. "
    "표에 적은 수치는 문단에서 다시 늘어놓지 않는다.",
)
# v1.10 의 줄 → v1.11 에서 그 자리에 들어가는 줄. 답변 형식 절의 네 줄만 바꾼다
V1_11_REPLACED = {
    "- 문단으로 답하고, 필요할 때만 목록·표를 쓴다. 굵게(**), 제목(#), 링크, 코드 블록, HTML 은 쓰지 않는다.": (
        V1_11_FORMAT_LINES[0],
    ),
    "- 짧은 답은 문단만 쓴다.": (V1_11_FORMAT_LINES[1], "- 짧은 답은 문단만 쓴다.", *V1_11_FORMAT_LINES[2:4]),
    "- 목록(- )은 조언처럼 여러 항목을 나열할 때 쓴다.": (V1_11_FORMAT_LINES[4],),
    "- 표는 여러 날이나 여러 지표를 나란히 비교할 때만 쓴다. 표 앞뒤에는 빈 줄을 둔다.": (V1_11_FORMAT_LINES[5],),
}


def test_v1_11_changes_only_answer_format_lines():
    """v1.10 의 답변 형식 네 줄만 바꾸면 v1.11 과 줄 단위로 같다 → 다른 절은 그대로이고 날짜 자리로 끝난다."""
    v1_10 = load_system_prompt("v1.10").splitlines()
    assert all(v1_10.count(line) == 1 for line in V1_11_REPLACED)
    expected = [new for line in v1_10 for new in V1_11_REPLACED.get(line, (line,))]
    v1_11 = load_system_prompt("v1.11")
    assert v1_11.splitlines() == expected
    assert v1_11.endswith("오늘은 {today}이다.")


def test_v1_11_new_format_rules_describe_actions_without_quotes_or_examples():
    """v1.11 에서 새로 들어간 줄 → 따옴표, 라고 답한다, 예:, 숫자가 없다."""
    v1_10 = set(load_system_prompt("v1.10").splitlines())
    added = [line for line in load_system_prompt("v1.11").splitlines() if line not in v1_10]
    assert len(added) == 6
    for line in added:
        assert not re.search("[\"'“”‘’]", line), line
        assert "라고 답한다" not in line and "예:" not in line and not re.search(r"\d", line), line


def test_v1_11_allows_only_level_three_subheadings():
    """v1.11 답변 형식 절 → ### 소제목은 허용하고 # ## 는 쓰지 않으며 수치 겹침 금지는 표 규칙 줄에만 있다."""
    section = load_system_prompt("v1.11").split("## 답변 형식", 1)[1].split("\n## ", 1)[0]
    assert "### 소제목" in section
    assert "큰 제목(#, ##)" in section and "굵게(**), 제목(#)" not in section
    assert "같은 수치를 문단과 표에 겹쳐 쓰지 않는다" not in section
    assert "표에 적은 수치는 문단에서 다시 늘어놓지 않는다" in section


def test_v1_11_rules_have_no_measured_questions():
    """v1.11 → 측정 문항 문장과 안전 규칙의 예시 낱말이 없다."""
    _assert_no_measured_questions(load_system_prompt("v1.11"))


# v1.12 는 기간 표에 시작부터 끝까지 줄을, 규칙에 시작..끝 줄을 더한다. v1.11 의 줄 → v1.12 에서 그 자리에 들어가는 줄
V1_12_RANGE_ROW = "| 시작부터 끝까지 | 시작..끝 | 3주 전 금요일부터 지난주까지 weeks_ago:3.fri..weeks_ago:1 |"
V1_12_RANGE_RULE = "- 시작과 끝을 말한 기간은 시작..끝 으로 쓰고, 날짜를 직접 세어 dates: 로 바꾸지 않는다."
V1_11_WEEKDAY_ROW = (
    "| 요일 하루 (오늘 전 가장 최근) | weekday:요일코드 | 월 mon, 화 tue, 수 wed, 목 thu, 금 fri, 토 sat, 일 sun |"
)
V1_11_DATE_RULE = "- 날짜를 말하면 date: 로 그날만 조회한다. 연도는 질문에 있을 때만 붙인다(date:2025-03-05)."
V1_12_REPLACED = {
    V1_11_WEEKDAY_ROW: (V1_11_WEEKDAY_ROW, V1_12_RANGE_ROW),
    V1_11_DATE_RULE: (V1_12_RANGE_RULE, V1_11_DATE_RULE),
}


def test_v1_12_adds_only_range_row_and_rule_to_v1_11():
    """v1.12 ↔ v1.11 → 기간 절에 시작부터 끝까지 표 줄과 시작..끝 규칙 줄만 더하고 나머지 줄은 같다."""
    v1_11 = load_system_prompt("v1.11").splitlines()
    assert all(v1_11.count(line) == 1 for line in V1_12_REPLACED)
    expected = [new for line in v1_11 for new in V1_12_REPLACED.get(line, (line,))]
    v1_12 = load_system_prompt("v1.12")
    assert v1_12.splitlines() == expected
    # 주와 요일을 함께 말한 하루 표현(weeks_ago:N.요일)은 표와 규칙에 넣지 않고 범위의 시작·끝으로만 보인다
    assert "N주 전 주의 요일 하루" not in v1_12 and "weeks_ago:N.요일코드" not in v1_12
    assert "지난주 X요일처럼 주를 말하면 그 주를 날짜별 조회" in v1_12
    assert v1_12.endswith("오늘은 {today}이다.")


def test_v1_12_range_example_does_not_appear_in_measured_questions():
    """v1.12 표의 시작부터 끝까지 예 → 측정에 쓰는 질문에 나오지 않고 측정 문항 문장도 프롬프트에 없다."""
    examples = _table_examples(load_system_prompt("v1.12"))
    assert examples["시작부터 끝까지"] == "3주 전 금요일부터 지난주까지 weeks_ago:3.fri..weeks_ago:1"
    texts = _measured_texts()
    assert {"지난주 토요일에 몇 걸음 걸었어?", "지난주 토요일에 운동 몇 분 했어?"} <= texts
    assert not any("3주 전 금요일부터 지난주까지" in text for text in texts)
    _assert_no_measured_questions(load_system_prompt("v1.12"))


V1_13_ROLE = (
    "너는 사용자의 웨어러블 건강 기록을 해석하고 사용자와 다음 행동을 함께 찾는 건강 코치다. 한국어 존댓말로 답한다."
)
# 판정을 먼저 말하는 규칙과 행동 하나를 권하는 규칙, 사실만 묻는 질문의 조언 규칙을 이 한 줄에 둔다
V1_13_FLOW = (
    "- 첫 문장에는 평가 질문이면 결과의 판정이나 가장 중요한 사실을, 조언 질문이면 권하는 구체적인 행동 하나를 말하고, "
    "그다음 근거가 된 값과 밤 수를 말한다. 동기화 전·기록 없음·연동 전 안내는 첫 문단에 함께 말한다. "
    "판정이 있으면 판단할 수 없다고 하지 않고, 없으면 만들지 않고 값과 기록 일수를 말한다. "
    "사실만 묻는 질문에는 사실만 답한다."
)
V1_13_TONE = "- 따뜻하게 말하되 과장하거나 겁주지 않는다."
V1_13_CAUSE = (
    "- 운동 시간 집계·제외 이유를 물으면 세션을 조회한다. 그 외 원인은 단정하지 않되, "
    "결과의 사실을 밤 수와 함께 짚는다. 결측 탓·미착용 추측은 하지 않는다."
)
V1_13_CONDITION = "- 조건별 차이는 원인이라고 하지 않고, 조건은 결과에 있는 것만 말한다."
V1_13_TABLE = "- 소제목을 나누려고 질문에 없는 표를 더하지 않는다."
V1_13_LIST = "- 목록(- )은 기록 여러 개처럼 여러 항목을 나열할 때 쓰고, 항목마다 한두 문장으로 쓴다."
V1_13_NOT_IN_RESULTS = (
    "- 도구 결과에 없는 내용(몸무게·체지방, 혈압, 혈당, 하루 전체 소모 칼로리, 착용 시간, 시간대별 걸음, "
    "삼성 수면 점수, 잠드는 데 걸린 시간, 피부 온도, 무호흡 분석 등)은 지어내지 않고 기록으로 확인할 수 없다고 말한다."
)
V1_13_USER_SAID = "- 사용자가 말한 정보(코골이, 피로 등)는 그렇게 말했다고 받고, 기록으로 확인한 것처럼 말하지 않는다."
V1_13_SCORE = (
    "- 점수와 등급은 점수 도구 결과에 있는 값과 이유로만 설명한다. "
    "점수 구간·기준값, 점수를 올리는 데 필요한 양은 결과에 있는 것만 말한다."
)
V1_13_FOLLOW_UP = "- 앞 질문에 이어 기간 없이 물으면 앞 질문 기간으로 다시 조회한다."
V1_13_DIAGNOSIS = (
    "- 질병은 판단하지 않고, 수면 유형·이상 신호를 진단명으로 바꾸지 않는다. "
    "질환 언급·진단 요청도 물은 지표만 조회해 기록으로 답한다. 기간 없는 기록 요청은 recent_days:7 로 조회한다."
)
V1_13_NUTRIENT = (
    "- 호르몬이나 영양 성분을 물으면 그 성분이 든 음식과 생활 습관만 말하고, 보충제와 용량은 안내하지 않는다. "
    "음식·습관 같은 생활 요령의 효과는 어떤 답에서도 단정하지 않는다."
)


def test_v1_13_changes_only_listed_lines_of_v1_12():
    """v1.12 ↔ v1.13 → 역할 문장과 아래 규칙 줄만 바꾸거나 더하고 나머지 줄은 같다."""
    v1_12 = load_system_prompt("v1.12").splitlines()

    def line(prefix: str) -> str:
        [found] = [text for text in v1_12 if text.startswith(prefix)]
        return found

    first_paragraph = line("- 첫 문단은")
    unavailable = line("- 질문한 기간을 비슷한 기간으로")
    replaced = {
        line("너는 "): (V1_13_ROLE,),
        first_paragraph: (first_paragraph, V1_13_FLOW, V1_13_TONE),
        line("- 운동 시간 집계"): (V1_13_CAUSE, V1_13_CONDITION),
        line("- 소제목을 나누려고"): (V1_13_TABLE,),
        line("- 목록(- )은"): (V1_13_LIST,),
        line("- 도구 결과에 없는 내용"): (V1_13_NOT_IN_RESULTS, V1_13_USER_SAID),
        line("- 점수와 등급은"): (V1_13_SCORE,),
        unavailable: (unavailable, V1_13_FOLLOW_UP),
        line("- 질병은 판단하지"): (V1_13_DIAGNOSIS,),
        line("- 호르몬이나 영양 성분"): (V1_13_NUTRIENT,),
    }
    expected = [new for text in v1_12 for new in replaced.get(text, (text,))]
    v1_13 = load_system_prompt("v1.13")
    assert v1_13.splitlines() == expected
    assert v1_13.endswith("오늘은 {today}이다.")


def test_v1_13_new_rules_describe_actions_without_quotes_or_examples():
    """v1.13 에서 바뀌거나 새로 들어간 줄 → 따옴표, 라고 답한다, 예:가 없고 기존 기간 표현 밖에는 숫자가 없다."""
    v1_12 = set(load_system_prompt("v1.12").splitlines())
    added = [line for line in load_system_prompt("v1.13").splitlines() if line not in v1_12]
    assert len(added) == 13
    for line in added:
        assert not re.search("[\"'“”‘’]", line), line
        assert "라고 답한다" not in line and "예:" not in line, line
        assert not re.search(r"\d", line.replace("recent_days:7", "")), line


def test_v1_13_keeps_each_rule_in_one_place():
    """v1.13 → 판정·행동 하나·사실만 답하기·기록 상태 안내 자리는 첫 문장 줄에, 효과·진단명 규칙도 한 곳에 있다."""
    v1_13 = load_system_prompt("v1.13")
    flow = ("판단할 수 없다고 하지 않", "행동 하나", "사실만 묻는 질문", "첫 문단에 함께")
    for phrase in (*flow, "효과", "진단명", "조건별 차이"):
        assert v1_13.count(phrase) == 1, phrase
    assert [line for line in v1_13.splitlines() if "행동 하나" in line] == [V1_13_FLOW]
    assert "기록만으로 원인을 확인할 수 없습니다" not in v1_13 and "조언을 더하지 않" not in v1_13
    # 조언은 행동 하나를 권하므로 목록 줄의 예는 조언이 아니라 기록이다
    assert "조언처럼 여러 항목" not in v1_13
    # 어젯밤과 평소의 비교가 도구 결과에 없어 오늘 활동량 줄은 넣지 않는다
    assert "어젯밤" not in v1_13.split("## 기간", 1)[0] and "가벼운 활동" not in v1_13


def test_v1_13_rules_have_no_measured_questions():
    """v1.13 → 측정 문항 문장과 안전 규칙의 예시 낱말이 없다."""
    _assert_no_measured_questions(load_system_prompt("v1.13"))


# 수면 기록 결과에 어젯밤과 그 전 7일 비교(calculation-rules.md 11.6)가 들어가 v1.13 에서 뺀 오늘 활동량 줄을
# 다시 넣는다
V1_14_TODAY_ACTIVITY = (
    "- 결과에 어젯밤 수면과 평소의 비교가 있고 그 차이가 클 때만 고강도 운동보다 가벼운 활동을 제안한다. "
    "결과에 평소와의 비교가 없으면 평소보다 짧다고 말하지 않는다."
)


V1_13_REFETCH = "- 건강 수치는 이전 답변에 있더라도 매번 도구로 다시 조회하고, 이전 답변의 수치를 근거로 쓰지 않는다."
# 이전 답의 수치나 "확인할 수 없다"는 답을 도구 없이 되풀이하는 실패가 있어, 도구를 부르지 않은 답에서는 확인했다고
# 말하지 않게 한다
V1_14_REFETCH = (
    "- 건강 수치는 이전 답변에 있더라도 매번 도구로 다시 조회하고, 이전 답변의 수치를 근거로 쓰지 않는다. "
    "이번 답에서 도구를 호출하지 않았으면 확인했다고 말하지 않는다."
)


def test_v1_14_adds_today_activity_line_after_first_sentence_line():
    """v1.13 → v1.14 는 첫 문장 줄 바로 뒤에 오늘 활동량 줄 하나를 더하고 다시 조회 줄을 고친다. 나머지 줄은 같다."""
    v1_13 = load_system_prompt("v1.13").splitlines()
    assert v1_13.count(V1_13_REFETCH) == 1
    expected = []
    for line in v1_13:
        if line == V1_13_FLOW:
            expected += [line, V1_14_TODAY_ACTIVITY]
        else:
            expected.append(V1_14_REFETCH if line == V1_13_REFETCH else line)
    assert len(expected) == len(v1_13) + 1
    v1_14 = load_system_prompt("v1.14")
    assert v1_14.splitlines() == expected
    assert v1_14.endswith("오늘은 {today}이다.")


def test_v1_14_refetch_line_is_a_general_rule():
    """다시 조회 줄 → 질문에 맞춘 예시·따옴표·숫자·지표 이름 없는 일반 규칙만 적는다."""
    assert not re.search("[\"'“”‘’]", V1_14_REFETCH)
    assert "예:" not in V1_14_REFETCH and not re.search(r"\d", V1_14_REFETCH)
    assert "산소포화도" not in V1_14_REFETCH and "수면" not in V1_14_REFETCH
    v1_14 = load_system_prompt("v1.14")
    assert v1_14.count(V1_14_REFETCH) == 1 and V1_13_REFETCH not in v1_14.splitlines()


def test_v1_14_today_activity_line_has_no_question_example():
    """오늘 활동량 줄 → 질문에 맞춘 예시·따옴표·숫자 없이 결과 조건만 적고, 측정 문항 문장이 없다."""
    assert not re.search("[\"'“”‘’]", V1_14_TODAY_ACTIVITY)
    assert "예:" not in V1_14_TODAY_ACTIVITY and not re.search(r"\d", V1_14_TODAY_ACTIVITY)
    v1_14 = load_system_prompt("v1.14")
    assert v1_14.count("가벼운 활동") == 1
    _assert_no_measured_questions(v1_14)


# v1.15 는 v1.13 에서 다섯 줄만 고친다. 다시 조회 줄과 in_progress 줄은 v1.13 그대로다
V1_13_STATUS = "동기화 전·기록 없음·연동 전 안내는 첫 문단에 함께 말한다."
V1_15_STATUS = (
    "동기화 전·기록 없음·연동 전, 수집 실패로 이전에 저장된 기록을 쓴 경우, 오늘 값이 아직 진행 중인 경우의 안내는 "
    "첫 문단에 함께 말한다."
)
V1_13_WEEKDAY_END = "이때 하루를 묻는 질문에는 기간 요약을 쓰지 않는다."
V1_15_WEEKDAY_END = "이때 그 주의 기간 요약은 쓰지 않는다."
V1_15_UNAVAILABLE = (
    "- 질문한 기간을 비슷한 기간으로 바꾸거나 줄여서 조회하지 않는다. 도구가 그 기간은 조회할 수 없다고 "
    "안내하면(unavailable_period) 안내에 나눈 기간이 있으면 그 기간을 모두 조회하고, 없으면 그 기간은 확인할 수 "
    "없다고 말한 뒤 확인할 수 있는 기간을 제안한다. 조회하지 못한 기간의 기록을 있다고 말하지 않고, 그 이유를 "
    "동기화·연동 상태로 말하지 않는다."
)
V1_15_UNSYNCED = (
    "- 결과의 unsynced_days 에 있는 날만 미동기화로 확인 불가라고 먼저 답하고 동기화를 안내한다. "
    "기록 없음·수면 0으로 답하거나 기록 일수에 포함하지 않는다."
)
V1_15_DRUG = (
    "- 약은 일반적인 분류와 차이까지만 말하고, 제품명·성분명·용량·복용 여부와 쓰는 때·방법·대상은 말하지 않는다. "
    "복용 판단은 의사·약사 상담을 권한다. 앞 대화에서 물은 약을 이어서 물어도 용량과 복용 시점은 안내하지 않는다."
)


def test_v1_15_changes_only_five_lines_of_v1_13():
    """v1.13 → v1.15 는 첫 문장 줄·지난주 X요일 줄·조회할 수 없는 기간 줄·미동기화 줄·약 줄만 고치고 줄 수는 같다."""
    v1_13 = load_system_prompt("v1.13").splitlines()

    def line(prefix: str) -> str:
        [found] = [text for text in v1_13 if text.startswith(prefix)]
        return found

    weekday = line("- 요일 이름만으로")
    assert V1_13_STATUS in V1_13_FLOW and weekday.endswith(V1_13_WEEKDAY_END)
    replaced = {
        V1_13_FLOW: V1_13_FLOW.replace(V1_13_STATUS, V1_15_STATUS),
        weekday: weekday.replace(V1_13_WEEKDAY_END, V1_15_WEEKDAY_END),
        line("- 질문한 기간을 비슷한 기간으로"): V1_15_UNAVAILABLE,
        line("- unsynced_days 는"): V1_15_UNSYNCED,
        line("- 약의 처방"): V1_15_DRUG,
    }
    v1_15 = load_system_prompt("v1.15")
    assert v1_15.splitlines() == [replaced.get(text, text) for text in v1_13]
    changed = [i for i, (a, b) in enumerate(zip(v1_13, v1_15.splitlines(), strict=True), 1) if a != b]
    assert changed == [7, 52, 55, 63, 71]
    assert v1_15.endswith("오늘은 {today}이다.")


def test_v1_15_keeps_refetch_and_in_progress_lines_of_v1_13():
    """v1.15 → 다시 조회 줄은 v1.13 줄 그대로이고 v1.14 의 문장을 더하지 않으며, in_progress 줄도 그대로다."""
    v1_15 = load_system_prompt("v1.15").splitlines()
    assert v1_15.count(V1_13_REFETCH) == 1 and V1_14_REFETCH not in v1_15
    [in_progress] = [text for text in load_system_prompt("v1.13").splitlines() if text.startswith("- in_progress 는")]
    assert v1_15.count(in_progress) == 1
    assert "가벼운 활동" not in "\n".join(v1_15)


def test_v1_15_changed_lines_have_no_drug_names_or_measured_questions():
    """v1.15 에서 바뀐 줄 → 따옴표·예:·숫자·약 이름 없이 규칙만 적고, 측정 문항 문장이 없다."""
    for text in (V1_15_STATUS, V1_15_WEEKDAY_END, V1_15_UNAVAILABLE, V1_15_UNSYNCED, V1_15_DRUG):
        assert not re.search("[\"'“”‘’]", text), text
        # 수면 0 은 v1.13 미동기화 줄에 있던 값이다
        assert "예:" not in text and not re.search(r"\d", text.replace("수면 0으로", "")), text
    assert not guard._DRUG_NAME.search(V1_15_DRUG) and not guard._HORMONE.search(V1_15_DRUG)
    _assert_no_measured_questions(load_system_prompt("v1.15"))


@pytest.mark.parametrize(("version", "sha"), [("v1", "29735c95092f"), ("v1.1", "24c6c38773a4")])
def test_measured_prompts_stay_without_date(version, sha):
    # 기준선(v1)과 날짜를 넣기 전 측정(v1.1)의 시스템 프롬프트로 다시 잴 수 있게 두 파일은 바꾸지 않는다
    prompt = load_system_prompt(version)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12] == sha
    assert TODAY_SLOT not in prompt
    assert render_system_prompt(prompt, date(2026, 10, 27)) == prompt


def test_v1_2_is_v1_1_with_date_sentence_at_end():
    template = load_system_prompt("v1.2")
    assert template.count(TODAY_SLOT) == 1
    rendered = render_system_prompt(template, date(2026, 10, 27))
    assert rendered == load_system_prompt("v1.1") + "\n\n오늘은 2026년 10월 27일 화요일이다."


@pytest.mark.parametrize(
    ("day", "text"),
    [
        (date(2026, 10, 26), "2026년 10월 26일 월요일"),
        (date(2026, 10, 27), "2026년 10월 27일 화요일"),
        (date(2026, 10, 28), "2026년 10월 28일 수요일"),
        (date(2026, 10, 29), "2026년 10월 29일 목요일"),
        (date(2026, 10, 30), "2026년 10월 30일 금요일"),
        (date(2026, 10, 31), "2026년 10월 31일 토요일"),
        (date(2026, 11, 1), "2026년 11월 1일 일요일"),
    ],
)
def test_today_text_names_weekday(day, text):
    assert today_text(day) == text


@pytest.mark.parametrize(
    ("now", "day", "text"),
    [
        # UTC 로는 10월 26일 15시이고 한국 시간으로는 10월 27일 0시다
        (datetime(2026, 10, 26, 15, 0, tzinfo=UTC), date(2026, 10, 27), "2026년 10월 27일 화요일"),
        (datetime(2026, 10, 26, 14, 59, tzinfo=UTC), date(2026, 10, 26), "2026년 10월 26일 월요일"),
    ],
)
@respx.mock
async def test_system_prompt_ends_with_kst_date_of_request(model, now, day, text):
    handler = RecordingHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "today"})),
            text_response("오늘 7,412걸음이에요."),
        ]
    )
    kw = {"handlers": {"get_metric_summary": handler}, "prompt_version": "v1.2"}
    await run_coach(CTX, "오늘 몇 걸음 걸었어?", port=FakeHealthData(), model=model, sink=ListSink(), now=now, **kw)

    systems = [body["messages"][0]["content"] for body in bodies(route)]
    assert systems == [f"{load_system_prompt('v1.1')}\n\n오늘은 {text}이다."] * 2
    # 프롬프트의 날짜와 도구 기간 계산의 오늘이 같다
    assert handler.todays == [day]


@respx.mock
async def test_now_without_timezone_ends_with_internal_before_llm(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    sink = ListSink()
    # 시간대가 없는 시각으로는 사용자 시간대의 날짜를 정할 수 없다
    now = datetime(2026, 10, 27, 9, 0)
    result = await run_coach(CTX, "오늘 몇 걸음 걸었어?", port=FakeHealthData(), model=model, sink=sink, now=now)

    assert [e["type"] for e in sink.events] == ["error"] and sink.events[0]["code"] == "internal"
    assert (result.finish_reason, result.error_code) == ("error", "internal")
    assert isinstance(result.exception, ValueError)
    assert route.call_count == 0


@respx.mock
async def test_summary_follows_date_sentence(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    await run(model, question="잠을 잘 자려면?", ctx=replace(CTX, summary=SUMMARY), prompt_version="v1.2")

    # 날짜는 요약 앞에 온다. 같은 날 요청은 회원·대화가 달라도 날짜까지 앞부분이 같다
    parts = [
        load_system_prompt("v1.1"),
        "오늘은 2026년 10월 15일 목요일이다.",
        "## 이전 대화 요약",
        "걸음 수를 자주 묻는다.",
    ]
    assert bodies(route)[0]["messages"][0]["content"] == "\n\n".join(parts)


@respx.mock
async def test_run_coach_uses_default_prompt_version(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("잠들기 전 화면을 줄여 보세요."))
    await run(model, question="잠을 잘 자려면?")
    expected = render_system_prompt(load_system_prompt(DEFAULT_PROMPT_VERSION), date(2026, 10, 15))
    assert bodies(route)[0]["messages"][0]["content"] == expected


@respx.mock
async def test_prompt_without_date_slot_is_sent_as_is(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    await run(model, question="잠을 잘 자려면?", prompt_version="v1.1")
    assert bodies(route)[0]["messages"][0]["content"] == load_system_prompt("v1.1")


def test_run_coach_takes_previous_conversation_only_from_context():
    assert not {"summary", "history"} & set(inspect.signature(run_coach).parameters)


@respx.mock
async def test_context_history_goes_into_system_summary_and_messages(model):
    route = respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    await run(model, question="오늘은?", ctx=replace(CTX, summary=SUMMARY, turns=TURNS))

    messages = json.loads(route.calls[0].request.content)["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user", "assistant", "user"]
    # 요약은 시스템 메시지 끝의 이전 대화 요약 자리, 쌍은 질문·답변 메시지로 들어간다
    assert messages[0]["content"].endswith("\n\n## 이전 대화 요약\n\n걸음 수를 자주 묻는다.")
    assert [m["content"] for m in messages[1:]] == [
        "어제 몇 걸음 걸었어?", "8,000걸음이에요.", "그제는?", "6,500걸음이에요.", "오늘은?",
    ]  # fmt: skip


@respx.mock
async def test_turn_ids_stay_out_of_llm_requests_events_evidence_and_usage(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("지난 7일 평균 7,412걸음이에요."),
        ]
    )
    ctx = replace(CTX, summary=SUMMARY, turns=TURNS)
    result, events = await run(model, ctx=ctx, handlers={"get_metric_summary": RecordingHandler()})

    sent = [json.dumps(body, ensure_ascii=False) for body in bodies(route)]
    # 쌍의 질문·답변은 들어가고, 둘째 요청에는 도구 메시지도 들어간다
    assert len(sent) == 2 and all("8,000걸음이에요." in body for body in sent)
    assert bodies(route)[1]["messages"][-1]["role"] == "tool"
    shown = [
        json.dumps(events, ensure_ascii=False),
        json.dumps([e.event_item() for e in result.evidence], ensure_ascii=False),
        repr(result.history_usage),
    ]
    for value in TURN_IDS:
        assert all(value not in text for text in sent + shown)


@pytest.mark.parametrize("max_tool_rounds", [1, 3])
@respx.mock
async def test_answer_dates_stay_out_of_llm_requests_events_evidence_and_usage(model, max_tool_rounds):
    # 한도 1 이면 둘째 호출이 도구 없이 보내는 한도 호출이다
    route = respx.post(CHAT_URL).mock(side_effect=[tool_call_response(CALL), text_response("네.")] * 3)
    same_day = tuple(replace(t, answered_on=date(2026, 10, 15)) for t in TURNS)
    other_days = tuple(replace(t, answered_on=date(2026, 10, 13 + i)) for i, t in enumerate(TURNS))
    runs = []
    for turns in (TURNS, same_day, other_days):
        ctx = replace(CTX, summary=SUMMARY, turns=turns)
        handlers = {"get_metric_summary": RecordingHandler()}
        runs.append(await run(model, ctx=ctx, handlers=handlers, max_tool_rounds=max_tool_rounds))

    sent = [call.request.content for call in route.calls]
    assert len(sent) == 6
    # 같은 날 질문한 쌍만 있으면 날짜를 모를 때와 요청 본문이 같다
    assert sent[2:4] == sent[:2]
    # 다른 날 질문한 쌍이 있으면 시스템 메시지의 요약 뒤에만 안내가 붙고, 쌍의 날짜는 요청에 들어가지 않는다
    plain_bodies, other_bodies = bodies(route)[:2], bodies(route)[4:]
    for plain_body, other_body in zip(plain_bodies, other_bodies, strict=True):
        system = plain_body["messages"][0]["content"]
        assert other_body["messages"][0]["content"] == f"{system}\n\n{OTHER_DAY_NOTICE}"
        assert other_body["messages"][1:] == plain_body["messages"][1:]
        assert {k: v for k, v in other_body.items() if k != "messages"} == {
            k: v for k, v in plain_body.items() if k != "messages"
        }
    texts = [json.dumps(body, ensure_ascii=False) for body in other_bodies]
    assert not any(day in text for text in texts for day in ("10월 13일", "10월 14일", "2026-10-13", "2026-10-14"))

    def shown(result, events) -> str:
        # 근거 ID 는 실행마다 새로 만드는 UUID 라 순번으로 바꿔 비교한다
        items = [e.event_item() for e in result.evidence]
        text = json.dumps([events, items], ensure_ascii=False)
        for n, item in enumerate(items, 1):
            text = text.replace(item["id"], f"evidence-{n}")
        return text

    (plain, plain_events), *dated = runs
    assert plain.evidence
    for result, events in dated:
        assert shown(result, events) == shown(plain, plain_events)
        assert result.history_usage == plain.history_usage


def test_other_day_notice_is_only_for_pairs_asked_on_another_day():
    today = date(2026, 10, 15)
    # 날짜를 모르는 쌍은 세지 않는다
    assert other_day_notice([], today) is None
    assert other_day_notice([None, today, today], today) is None
    assert other_day_notice([None, date(2026, 10, 14), today], today) == OTHER_DAY_NOTICE
    # 안내는 사실 문장만 쓴다
    sentences = [s for s in OTHER_DAY_NOTICE.split(". ") if s]
    assert all(s.rstrip(".").endswith("다") and not re.search(r"(주세요|한다|않는다)\.?$", s) for s in sentences)


@respx.mock
async def test_history_usage_counts_turns_and_summary_of_last_llm_call(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_metric_summary", {"metric": "steps", "period": "last_7_days"})),
            text_response("지난 7일 평균 7,412걸음이에요."),
        ]
    )
    ctx = replace(CTX, summary=DIGIT_SUMMARY, turns=DIGIT_TURNS)
    result, _ = await run(model, ctx=ctx, handlers={"get_metric_summary": RecordingHandler()})

    # 이번 질문은 세지 않는다. 두 호출 모두 이전 대화를 전부 넣는다
    assert result.finish_reason == "stop" and result.history_usage == DIGIT_USAGE
    for body in bodies(route):
        assert [m["content"] for m in body["messages"][1:5]] == ["12", "345", "6789", "0"]
        assert body["messages"][0]["content"].endswith("\n\n## 이전 대화 요약\n\n1234567")


@respx.mock
async def test_history_usage_without_summary_marks_summary_unused(model):
    respx.post(CHAT_URL).mock(return_value=text_response("네."))
    result, _ = await run(model, question="오늘은?", ctx=replace(CTX, turns=DIGIT_TURNS))
    assert result.history_usage == HistoryUsage(
        summary_used=False, included_turns=2, excluded_turns=0, history_tokens=10, summary_tokens=0
    )


@respx.mock
async def test_history_usage_is_zero_without_previous_conversation(model):
    respx.post(CHAT_URL).mock(return_value=text_response("잠들기 전 화면을 줄여 보세요."))
    result, _ = await run(model, question="잠을 잘 자려면?")
    assert result.history_usage == HistoryUsage(
        summary_used=False, included_turns=0, excluded_turns=0, history_tokens=0, summary_tokens=0
    )


@respx.mock
async def test_guard_answer_has_no_history_usage(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result, _ = await run(model, question="가슴이 답답하고 숨을 못 쉬겠어요", ctx=replace(CTX, turns=DIGIT_TURNS))

    # 경계 확인에서 끝나면 LLM 을 호출하지 않는다
    assert route.call_count == 0 and result.finish_reason == "guard"
    assert result.history_usage is None


@respx.mock
async def test_llm_failure_keeps_history_usage_of_failed_call(settings):
    settings.llm_max_retries = 0
    model = create_chat_model(settings, recorder=MetricsRecorder())
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(503, json={"error": {"message": "down"}}))
    result, _ = await run(model, ctx=replace(CTX, summary=DIGIT_SUMMARY, turns=DIGIT_TURNS))

    assert result.error_code == "llm_unavailable" and route.call_count == 1
    assert result.history_usage == DIGIT_USAGE


# 답변이 숫자라 답변 글자 수가 그 토큰 수다
BIG_SUMMARY = StoredSummary(text="걸음 수를 자주 묻는다.", until_seq=2)
BIG_TURNS = (
    history_turn(3, "첫 질문", 4, "1" * 300),
    history_turn(5, "둘째 질문", 6, "2" * 300),
    history_turn(7, "셋째 질문", 8, "3" * 300),
)
TODAY_PROMPT = render_system_prompt(load_system_prompt(), date(2026, 10, 15))
CALL = ("get_metric_summary", {"metric": "steps", "period": "last_7_days"})


def history_ctx(summary=BIG_SUMMARY, turns=BIG_TURNS) -> CoachContext:
    return replace(CTX, summary=summary, turns=turns)


def first_call(question: str, summary=BIG_SUMMARY, turns=BIG_TURNS):
    """run_coach 가 예산 검사 전에 만드는 첫 호출 메시지."""
    chats = [chat for t in turns for chat in (ChatTurn("user", t.question), ChatTurn("assistant", t.answer))]
    return build_messages(question, None if summary is None else summary.text, chats, TODAY_PROMPT)


def tool_round(size: int):
    """CALL 한 번에 BlobHandler(size) 결과가 붙을 때 다음 호출에 더해지는 메시지."""
    ai = AIMessage(content="", tool_calls=[{"name": CALL[0], "args": CALL[1], "id": "call_1"}])
    content = json.dumps({"evidence_ids": ["E1"], "result": {"blob": "9" * size}}, ensure_ascii=False)
    return [ai, ToolMessage(content=content, tool_call_id="call_1")]


def contents(body: dict) -> list[str]:
    return [m["content"] for m in body["messages"]]


class BlobHandler:
    """get_metric_summary 대신 숫자 size 개짜리 결과를 반환한다."""

    def __init__(self, size: int) -> None:
        self.size = size

    async def __call__(self, inp, req: ToolRequest) -> ToolOutcome:
        draft = EvidenceDraft("get_metric_summary", PUBLIC_METRICS[inp.metric], SPAN, "요약", SNAPSHOT)
        return ToolOutcome("ok", {"blob": "9" * self.size}, (draft,))


@respx.mock
async def test_first_call_at_limit_keeps_history_and_one_over_drops_oldest_pair(model):
    route = respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    fill = INPUT_TOKENS_LIMIT - count_request_tokens(first_call(""), tool_specs())
    at_limit, _ = await run(model, question="7" * fill, ctx=history_ctx())
    over, _ = await run(model, question="7" * (fill + 1), ctx=history_ctx())

    kept, dropped = bodies(route)
    pairs = [text for t in BIG_TURNS for text in (t.question, t.answer)]
    assert contents(kept)[1:7] == pairs and OMITTED_HISTORY_NOTICE not in contents(kept)[0]
    assert at_limit.history_usage == HistoryUsage.of(BIG_SUMMARY, BIG_TURNS, received_turns=3)
    # 가장 오래된 쌍을 질문·답변 함께 빼고, 요약 뒤에 안내를 붙인다
    assert contents(dropped)[1:] == [*pairs[2:], "7" * (fill + 1)]
    assert contents(dropped)[0] == system_with_history(TODAY_PROMPT, BIG_SUMMARY.text, omitted=True)
    assert over.history_usage == HistoryUsage.of(BIG_SUMMARY, BIG_TURNS[1:], received_turns=3)
    assert over.history_usage.excluded_turns == 1 and over.finish_reason == "stop"


@respx.mock
async def test_output_reservation_follows_max_tokens_of_request(settings):
    settings.llm_max_tokens = 1200
    model = create_chat_model(settings, recorder=MetricsRecorder(), retry_base_delay=0.0)
    route = respx.post(CHAT_URL).mock(side_effect=lambda request: text_response("네."))
    fill = input_tokens_limit(1200) - count_request_tokens(first_call(""), tool_specs())
    at_limit, _ = await run(model, question="7" * fill, ctx=history_ctx())
    over, _ = await run(model, question="7" * (fill + 1), ctx=history_ctx())

    # 예산 검사가 출력 몫으로 예약한 값과 요청의 최대 출력이 같아야 한다
    kept, dropped = bodies(route)
    assert kept["max_tokens"] == dropped["max_tokens"] == 1200
    assert (at_limit.history_usage.excluded_turns, over.history_usage.excluded_turns) == (0, 1)


@respx.mock
async def test_summary_is_dropped_after_all_pairs(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    without = [SystemMessage(content=system_with_history(TODAY_PROMPT, None, omitted=True)), HumanMessage(content="")]
    fill = INPUT_TOKENS_LIMIT - count_request_tokens(without, tool_specs())
    result, _ = await run(model, question="7" * fill, ctx=history_ctx())

    [body] = bodies(route)
    assert contents(body) == [system_with_history(TODAY_PROMPT, None, omitted=True), "7" * fill]
    assert result.history_usage == HistoryUsage(
        summary_used=False, included_turns=0, excluded_turns=3, history_tokens=0, summary_tokens=0
    )


@respx.mock
async def test_first_call_over_limit_without_history_ends_with_input_too_large(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    without = [SystemMessage(content=system_with_history(TODAY_PROMPT, None, omitted=True)), HumanMessage(content="")]
    fill = INPUT_TOKENS_LIMIT - count_request_tokens(without, tool_specs()) + 1
    result, events = await run(model, question="7" * fill, ctx=history_ctx())

    assert route.call_count == 0
    error = {k: events[-1][k] for k in ("type", "code", "message", "retryable", "metrics")}
    assert error == {
        "type": "error",
        "code": "input_too_large",
        "message": INPUT_TOO_LARGE_MESSAGE,
        "retryable": False,
        "metrics": [],
    }
    assert (result.finish_reason, result.error_code, result.answer) == ("error", "input_too_large", "")
    assert isinstance(result.exception, InputTooLargeError) and result.history_usage is None
    # 오류 문구에 질문·이전 대화가 들어가지 않는다
    shown = json.dumps(events, ensure_ascii=False) + str(result.exception)
    assert all(text not in shown for text in ("7777", BIG_SUMMARY.text, "첫 질문", "1" * 300))


@respx.mock
async def test_tool_result_call_drops_more_pairs_and_usage_follows_last_call(model):
    question = "지난 7일 걸음 수 알려줘"
    size = INPUT_TOKENS_LIMIT - count_request_tokens([*first_call(question), *tool_round(0)], tool_specs()) + 1
    route = respx.post(CHAT_URL).mock(side_effect=[tool_call_response(CALL), text_response("네.")])
    handlers = {"get_metric_summary": BlobHandler(size)}
    result, _ = await run(model, question=question, ctx=history_ctx(), handlers=handlers)

    first, second = bodies(route)
    pairs = [text for t in BIG_TURNS for text in (t.question, t.answer)]
    assert contents(first)[1:7] == pairs and OMITTED_HISTORY_NOTICE not in contents(first)[0]
    # 도구 결과가 붙은 호출에서 1 넘어 가장 오래된 쌍을 빼고 안내를 붙인다
    assert contents(second)[0] == system_with_history(TODAY_PROMPT, BIG_SUMMARY.text, omitted=True)
    assert contents(second)[1:6] == [*pairs[2:], question]
    assert second["messages"][-1]["role"] == "tool"
    assert len(json.loads(contents(second)[-1])["result"]["blob"]) == size
    assert result.finish_reason == "stop"
    assert result.history_usage == HistoryUsage.of(BIG_SUMMARY, BIG_TURNS[1:], received_turns=3)


@respx.mock
async def test_tool_result_call_over_limit_ends_with_input_too_large(model):
    route = respx.post(CHAT_URL).mock(side_effect=[tool_call_response(CALL), text_response("네.")])
    handlers = {"get_metric_summary": BlobHandler(INPUT_TOKENS_LIMIT)}
    result, events = await run(model, ctx=history_ctx(), handlers=handlers)

    assert route.call_count == 1
    assert (events[-1]["type"], events[-1]["code"], events[-1]["retryable"]) == ("error", "input_too_large", False)
    assert (result.error_code, result.evidence) == ("input_too_large", [])
    # 사용량은 마지막으로 보낸 첫 호출의 값이다
    assert result.history_usage == HistoryUsage.of(BIG_SUMMARY, BIG_TURNS, received_turns=3)


@respx.mock
async def test_recent_pairs_over_2400_tokens_leave_out_oldest_even_when_total_fits(model):
    turns = (history_turn(3, "", 4, "1" * 1200), history_turn(5, "", 6, "2" * 1200), history_turn(7, "", 8, "3"))
    route = respx.post(CHAT_URL).mock(return_value=text_response("네."))
    result, _ = await run(model, question="오늘은?", ctx=replace(CTX, turns=turns))

    [body] = bodies(route)
    system = system_with_history(TODAY_PROMPT, None, omitted=True)
    assert contents(body) == [system, "", "2" * 1200, "", "3", "오늘은?"]
    assert result.history_usage == HistoryUsage(
        summary_used=False, included_turns=2, excluded_turns=1, history_tokens=1201, summary_tokens=0
    )


@pytest.mark.parametrize("ctx", [CTX, replace(CTX, summary=SUMMARY, turns=TURNS)], ids=["no_history", "short"])
@pytest.mark.parametrize("max_tool_rounds", [1, 3])
@respx.mock
async def test_request_bodies_stay_the_same_when_history_fits(model, monkeypatch, ctx, max_tool_rounds):
    # 한도 1 이면 둘째 호출이 도구 없이 보내는 한도 호출이다
    route = respx.post(CHAT_URL).mock(side_effect=[tool_call_response(CALL), text_response("네.")] * 2)
    handlers = {"get_metric_summary": RecordingHandler()}
    await run(model, ctx=ctx, handlers=handlers, max_tool_rounds=max_tool_rounds)

    def unchanged(messages, previous, *, tools, output_tokens):
        return messages, HistoryUsage.of(None, (), received_turns=0)

    monkeypatch.setattr(model_node, "fit_request", unchanged)
    await run(model, ctx=ctx, handlers=handlers, max_tool_rounds=max_tool_rounds)

    sent = [call.request.content for call in route.calls]
    assert len(sent) == 4 and sent[:2] == sent[2:]


# 생각 표시만 있고 닫는 표시 없이 끝난 응답은 래퍼가 생각 부분을 모두 제외해 빈 응답이 된다
EMPTY = {"empty": (), "unclosed_thought": ("<|channel>thought\n", "생각만 하다 끝남")}
NOT_EMPTY = {
    "text": lambda: [text_response("잠들기 전 화면을 줄여 보세요.")],
    "length": lambda: [text_response("잘린 답", finish_reason="length")],
    "tool_call": lambda: [tool_call_response(CALL), text_response("평균 7,412걸음이에요.")],
    "text_before_tool_call": lambda: [tool_call_response(CALL, text=("기록을 확인해 볼게요. ",)), text_response("네.")],
}


def empty_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == model_node.__name__]


@pytest.mark.parametrize("parts", EMPTY.values(), ids=EMPTY)
@respx.mock
async def test_empty_response_is_sent_again_once(model, recorder, caplog, parts):
    answer = "잠들기 전 화면을 줄여 보세요."
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(*parts), text_response(answer)])
    result, events = await run(model, question="잠을 잘 자려면?")

    first, again = (call.request.content for call in route.calls)
    assert first == again
    assert (result.finish_reason, result.answer) == ("stop", answer)
    kinds = [e["type"] if e["type"] != "status" else f"status:{e['stage']}" for e in events]
    assert kinds == ["status:thinking", "status:answering", "token", "done"]
    # 빈 응답은 결과 메시지에 넣지 않는다
    assert [type(m) for m in result.messages] == [SystemMessage, HumanMessage, AIMessage]
    # 다시 보낸 호출도 LLM 호출 기록에 한 건으로 기록되고 사용량에 들어간다
    assert [r.node for r in recorder.records("llm")] == ["agent", "agent"]
    assert events[-1]["usage"] == {"prompt_tokens": 1000, "completion_tokens": 80}
    [log] = empty_logs(caplog)
    assert all(f in log for f in ("request_id=r-1", "tool_rounds=0", "attempt=1", "finish_reason=stop", "resend=True"))


@pytest.mark.parametrize("parts", EMPTY.values(), ids=EMPTY)
@respx.mock
async def test_empty_answer_after_tool_result_is_sent_again_once(model, parts):
    answer = "지난 7일 평균 7,412걸음이에요."
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(CALL), text_response(*parts), text_response(answer)]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    _, first, again = (call.request.content for call in route.calls)
    assert first == again
    assert (result.finish_reason, result.answer, result.tool_rounds) == ("stop", answer, 1)
    assert [e.ref for e in result.evidence] == ["E1"]
    assert streamed(events) == answer and events[-1]["type"] == "done"


@respx.mock
async def test_tool_call_in_resent_response_goes_on_to_tools(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[text_response(), tool_call_response(CALL), text_response("평균 7,412걸음이에요.")]
    )
    result, _ = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    first, again, answer = bodies(route)
    assert first == again
    # 빈 응답은 다음 호출의 메시지에도 들어가지 않는다
    assert [m["role"] for m in answer["messages"]] == ["system", "user", "assistant", "tool"]
    assert (result.finish_reason, result.answer, result.tool_rounds) == ("stop", "평균 7,412걸음이에요.", 1)


@pytest.mark.parametrize("parts", EMPTY.values(), ids=EMPTY)
@pytest.mark.parametrize("rounds", [0, 1], ids=["tool_choice", "answer"])
@respx.mock
async def test_empty_response_after_resend_ends_with_llm_unavailable(model, recorder, caplog, parts, rounds):
    before = [tool_call_response(CALL)] * rounds
    route = respx.post(CHAT_URL).mock(
        side_effect=[*before, text_response(*parts), text_response(*parts), text_response("네.")]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert route.call_count == len(recorder.records("llm")) == rounds + 2
    assert events[-1] == {
        "type": "error", "conversation_id": "c-1", "request_id": "r-1",
        "code": "llm_unavailable", "message": USER_MESSAGES["llm_unavailable"], "retryable": True, "metrics": [],
    }  # fmt: skip
    assert (result.finish_reason, result.error_code, result.answer) == ("error", "llm_unavailable", "")
    assert isinstance(result.exception, LLMEmptyResponseError) and result.evidence == []
    first, again = empty_logs(caplog)
    assert all(f in first for f in (f"tool_rounds={rounds}", "attempt=1", "resend=True"))
    assert all(f in again for f in (f"tool_rounds={rounds}", "attempt=2", "resend=False"))
    # 응답 원문은 로그에 넣지 않는다
    assert all("생각" not in log for log in (first, again))


@respx.mock
async def test_empty_answer_after_text_on_screen_is_sent_again_once(model, caplog):
    # 앞 왕복에서 도구 호출 앞에 보낸 글은 머리말이라 화면에 있어도 다시 보내고, 다시 받은 답변이 그 뒤에 이어진다
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(CALL, text=("기록을 확인해 볼게요. ",)),
            text_response(),
            text_response("평균 7,412걸음이에요."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    _, first, again = (call.request.content for call in route.calls)
    assert first == again
    assert streamed(events) == result.answer == "기록을 확인해 볼게요. 평균 7,412걸음이에요."
    assert (result.finish_reason, events[-1]["type"]) == ("stop", "done")
    [log] = empty_logs(caplog)
    assert "attempt=1" in log and "resend=True" in log


@respx.mock
async def test_empty_answer_again_after_text_on_screen_keeps_text_as_partial_answer(model, caplog):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(CALL, text=("기록을 확인해 볼게요. ",)),
            text_response(),
            text_response(),
            text_response("네."),
        ]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert route.call_count == 3
    assert (events[-1]["type"], result.error_code) == ("error", "llm_unavailable")
    assert result.answer == "기록을 확인해 볼게요. " and isinstance(result.exception, LLMEmptyResponseError)
    first, again = empty_logs(caplog)
    assert "resend=True" in first and "resend=False" in again


@respx.mock
async def test_whitespace_only_response_is_sent_again(model):
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(CALL), text_response("\n"), text_response("평균 7,412걸음이에요.")]
    )
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    # 공백만 온 응답도 빈 응답이다. 이미 보낸 공백 조각은 되돌릴 수 없어 다시 보낸 답변이 그 뒤에 이어진다
    assert route.call_count == 3
    assert [e["delta"] for e in events if e["type"] == "token"] == ["\n", "평균 7,412걸음이에요."]
    stages = [e["stage"] for e in events if e["type"] == "status"]
    assert stages == ["thinking", "tool_call", "tool_done", "thinking", "answering"]
    assert (result.finish_reason, result.answer) == ("stop", "\n평균 7,412걸음이에요.")


@respx.mock
async def test_whitespace_only_first_response_is_dropped_and_sent_again(model):
    route = respx.post(CHAT_URL).mock(side_effect=[text_response("\n"), text_response("오늘은 일찍 쉬어 보세요.")])
    result, events = await run(model, "오늘은 뭘 하면 좋아?")

    # 첫 호출의 공백 조각은 모아 두었다가 다시 보낼 때 버려, 다시 받은 답변만 나간다
    assert route.call_count == 2
    assert [e["delta"] for e in events if e["type"] == "token"] == ["오늘은 일찍 쉬어 보세요."]
    assert [e["stage"] for e in events if e["type"] == "status"] == ["thinking", "answering"]
    assert (result.finish_reason, result.answer) == ("stop", "오늘은 일찍 쉬어 보세요.")


@pytest.mark.parametrize("parts", EMPTY.values(), ids=EMPTY)
@respx.mock
async def test_empty_tool_limit_call_ends_with_tool_limit_answer_without_resend(model, caplog, parts):
    route = respx.post(CHAT_URL).mock(side_effect=[*limit_rounds(), text_response(*parts), text_response("네.")])
    result, events = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert route.call_count == 4
    assert (result.finish_reason, result.answer) == ("tool_limit", TOOL_LIMIT_ANSWER)
    assert events[-1]["type"] == "done" and empty_logs(caplog) == []


@pytest.mark.parametrize("responses", NOT_EMPTY.values(), ids=NOT_EMPTY)
@respx.mock
async def test_response_with_text_or_tool_call_is_sent_once(model, recorder, caplog, responses):
    sent = responses()
    route = respx.post(CHAT_URL).mock(side_effect=[*sent, text_response("다시 보낸 답변")])
    result, _ = await run(model, handlers={"get_metric_summary": RecordingHandler()})

    assert route.call_count == len(recorder.records("llm")) == len(sent)
    assert result.finish_reason in ("stop", "length") and empty_logs(caplog) == []


@respx.mock
async def test_sleep_claim_recheck_executes_pattern_and_holds_bad_explanation(model):
    fixture = gf.load_fixture("synth-11")
    question = "7시간 30분 잤는데 왜 피곤할까?"
    old = "어젯밤 3시간 4분으로, 동기화가 안 됐을 수 있습니다."
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(old),
            tool_call_response(
                ("compare_periods", {"metric": "sleep", "current": "days_ago:0", "baseline": "weeks_ago:1"})
            ),
            text_response("7시간 30분은 워치가 다르게 인식했을 수 있습니다."),
        ]
    )
    sink = ListSink()
    ctx = replace(CTX, user_id=fixture.user_id, turns=(history_turn(1, question, 2, old),))
    result = await run_coach(ctx, question, port=fixture.port(), model=model, sink=sink, now=fixture.now)
    assert result.error_code is None and result.answer_rechecks == 1 and route.call_count == 3
    calls = [c for m in result.messages if isinstance(m, AIMessage) for c in m.tool_calls]
    assert calls[-1]["args"] == {"analysis": "pattern", "period": "days_ago:0"}
    assert "5시간 8분" in result.answer and "누워" in result.answer
    assert "인식" not in result.answer and "동기화" not in result.answer
    assert "".join(tokens(sink.events)) == result.answer


@respx.mock
async def test_signals_summary_overlap_is_not_executed_after_recheck(model):
    fixture = gf.load_fixture("synth-11")
    question = "내 산소포화도는 왜 들쭉날쭉해?"
    old = "밤별 산소포화도는 83.7%에서 97.7%입니다."
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response(old),
            tool_call_response(
                ("get_sleep_records", {"analysis": "signals", "period": "recent_days:14"}),
                ("get_metric_summary", {"metric": "oxygen_saturation", "period": "recent_days:14"}),
            ),
            text_response("기록만으로 변동 원인을 확인할 수 없습니다."),
        ]
    )
    ctx = replace(CTX, user_id=fixture.user_id, turns=(history_turn(1, question, 2, old),))
    result = await run_coach(ctx, question, port=fixture.port(), model=model, sink=ListSink(), now=fixture.now)
    assert result.error_code is None and result.answer_rechecks == 1 and route.call_count == 3
    replies = [m for m in result.messages if isinstance(m, ToolMessage)]
    assert len(replies) == 1 and replies[0].name == "get_sleep_records"


@pytest.mark.parametrize("history", [True, False])
@respx.mock
async def test_sleep_claim_retry_without_tools_never_streams_speculation(model, history):
    from coach_ai.agent.rules.sleep_grounding import NO_SLEEP_RECORD

    fixture = gf.load_fixture("synth-11")
    question = "7시간 30분 잤는데 왜 피곤할까?"
    old = "어젯밤 3시간 4분으로, 동기화가 안 됐을 수 있습니다."
    route = respx.post(CHAT_URL).mock(side_effect=[text_response(old), text_response(old)])
    sink = ListSink()
    ctx = replace(CTX, user_id=fixture.user_id, turns=(history_turn(1, question, 2, old),) if history else ())
    result = await run_coach(ctx, question, port=fixture.port(), model=model, sink=sink, now=fixture.now)
    assert result.error_code is None and result.answer_rechecks == 1 and route.call_count == 2
    assert result.answer == NO_SLEEP_RECORD and result.tool_rounds == 0
    assert "".join(tokens(sink.events)) == NO_SLEEP_RECORD


@pytest.mark.parametrize(
    "speculation",
    [
        "워치 센서가 잘못 측정해서 실제보다 짧게 기록된 것 같습니다.",
        "센서 오측정으로 수면이 짧게 나왔을 가능성이 있습니다.",
        "스트레스가 쌓여 피로가 생긴 것 같습니다.",
    ],
)
@respx.mock
async def test_sleep_claim_alternate_causes_never_reach_user(model, speculation):
    fixture = gf.load_fixture("synth-11")
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_sleep_records", {"analysis": "pattern"})),
            text_response("실제 수면 시간은 5시간 8분이며 누워 있던 시간은 제외됩니다. " + speculation),
        ]
    )
    sink = ListSink()
    result = await run_coach(
        replace(CTX, user_id=fixture.user_id),
        "7시간 30분 잤는데 왜 피곤할까?",
        port=fixture.port(),
        model=model,
        sink=sink,
        now=fixture.now,
    )
    assert result.error_code is None and "5시간 8분" in result.answer
    assert speculation not in result.answer and "확인할 수 없습니다" in result.answer
    assert "".join(tokens(sink.events)) == result.answer


@pytest.mark.parametrize("period", ["recent_days:14", "dates:10-09..10-22"])
@respx.mock
async def test_signals_later_round_summary_is_removed_and_answer_finishes(model, period):
    fixture = gf.load_fixture("synth-11")
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(("get_sleep_records", {"analysis": "signals", "period": "recent_days:14"})),
            tool_call_response(("get_metric_summary", {"metric": "oxygen_saturation", "period": period})),
        ]
    )
    sink = ListSink()
    result = await run_coach(
        replace(CTX, user_id=fixture.user_id),
        "내 산소포화도는 왜 들쭉날쭉해?",
        port=fixture.port(),
        model=model,
        sink=sink,
        now=fixture.now,
    )
    assert result.error_code is None and route.call_count == 2 and result.tool_rounds == 1
    assert [m.name for m in result.messages if isinstance(m, ToolMessage)] == ["get_sleep_records"]
    assert "확인할 수 없습니다" in result.answer and "".join(tokens(sink.events)) == result.answer
