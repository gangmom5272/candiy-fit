"""약 질문의 그래프 흐름. 복용 판단은 가드에서 고정 답으로 끝나고, 일반 지식은 답을 다 받은 뒤 검사하고 보낸다."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
import respx

from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.agent.nodes.model import TOOL_LIMIT_ANSWER
from coach_ai.agent.prompts import load_system_prompt, render_system_prompt
from coach_ai.agent.rules.guard import medication_answer
from coach_ai.history.types import HistoryTurn
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.security.gateway import ToolGateway
from tests.fakes import FakeHealthData
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # 2026-10-15 12:00 KST
TODAY = date(2026, 10, 15)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
SLEEP_ANSWER = medication_answer(sleep=True)
OTHER_ANSWER = medication_answer(sleep=False)
SLEEP_DECISION = "수면 유도제 먹으면 도움이 될까?"
SLEEP_GENERAL = "수면제랑 수면 유도제는 뭐가 달라?"
# 일반 차이와 상담 안내만 있는 답을 세 조각으로 나눈 모의 응답
GENERAL_PARTS = (
    "수면제는 보통 의사 처방이 필요한 전문 의약품이고, ",
    "수면 유도제는 처방 없이 살 수 있는 일반 의약품이에요.\n\n",
    "나에게 맞는지, 먹어도 되는지는 안내해 드릴 수 없어요. 의사나 약사와 상담해 주세요.",
)
SLEEP_TOOL = ("get_sleep_records", {"period": "last_7_days"})


@pytest.fixture
def model(settings):
    return create_chat_model(settings, recorder=MetricsRecorder(), retry_base_delay=0.0)


def turn(question: str, answer: str) -> HistoryTurn:
    def uid(kind: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-drug-{kind}"))

    return HistoryTurn(
        request_id=uid("request"),
        question_message_id=uid("question"),
        answer_message_id=uid("answer"),
        question_seq=1,
        answer_seq=2,
        question=question,
        answer=answer,
    )


async def run(model, question, *, port=None, ctx=CTX, **kw):
    sink = ListSink()
    port = FakeHealthData() if port is None else port
    # 근거 허용 목록을 직접 넘겨 설정(.env)을 읽지 않는다
    gateway = ToolGateway(port, TODAY, evidence_enabled=frozenset())
    result = await run_coach(ctx, question, port=port, model=model, sink=sink, now=NOW, executor=gateway, **kw)
    return result, sink.events


def kinds(events) -> list[str]:
    return [e["type"] if e["type"] != "status" else f"status:{e['stage']}" for e in events]


def tokens(events) -> list[str]:
    return [e["delta"] for e in events if e["type"] == "token"]


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (SLEEP_DECISION, SLEEP_ANSWER),
        ("멜라토닌 먹어도 돼?", SLEEP_ANSWER),
        ("진통제 먹어도 돼?", OTHER_ANSWER),
        ("영양제 먹어도 돼?", OTHER_ANSWER),
    ],
)
@respx.mock
async def test_decision_question_ends_at_guard_with_next_question(model, question, answer):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    result, events = await run(model, question)

    assert route.call_count == 0
    assert (result.answer, result.finish_reason, result.blocked_by) == (answer, "guard", "medication")
    assert result.history_usage is None
    assert kinds(events) == ["status:answering", "token", "done"]


@respx.mock
async def test_general_question_answer_is_sent_whole_after_check(model):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*GENERAL_PARTS))
    result, events = await run(model, SLEEP_GENERAL)

    assert route.call_count == 1
    assert (result.answer, result.finish_reason, result.blocked_by) == ("".join(GENERAL_PARTS), "stop", None)
    # 조각마다 보내지 않고 다 받은 답을 한 번에 보낸다
    assert tokens(events) == ["".join(GENERAL_PARTS)]
    assert kinds(events) == ["status:thinking", "status:answering", "token", "done"]


@respx.mock
async def test_general_question_answer_passing_check_is_hedged(model):
    # 약 답 검사를 지난 답도 효과 단정 끝맺음은 완곡하게 바꿔 한 번에 보낸다
    parts = (*GENERAL_PARTS, " 같은 시각에 일어나면 아침이 수월해집니다.")
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    result, events = await run(model, SLEEP_GENERAL)

    expected = "".join(GENERAL_PARTS) + " 같은 시각에 일어나면 아침이 수월해질 수 있어요."
    assert (result.answer, result.finish_reason, result.hedged_sentences) == (expected, "stop", 1)
    assert tokens(events) == [expected]
    assert result.messages[-1].content == expected


@respx.mock
async def test_general_question_answer_is_not_rechecked(model):
    # 약 일반 지식 경로는 약 답 검사만 한다. 앞 답의 수치를 다시 써도 되돌리지 않는다
    previous = turn("합성 이전 질문", "전날 기록은 6시간 10분이었어요.")
    parts = ("어제 6시간 10분 주무셨는데, ", *GENERAL_PARTS)
    route = respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    result, _ = await run(model, SLEEP_GENERAL, ctx=replace(CTX, turns=(previous,)))

    assert route.call_count == 1
    assert (result.answer, result.answer_rechecks) == ("".join(parts), 0)


@respx.mock
async def test_general_question_answer_is_not_checked_for_unsupported_numbers(model):
    # 앞 답에도 질문에도 없는 수치를 말해도 약 일반 지식 경로는 되돌리지 않는다
    parts = ("어젯밤 5시간 20분 주무셨는데, ", *GENERAL_PARTS)
    route = respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    result, _ = await run(model, SLEEP_GENERAL)

    assert route.call_count == 1
    assert (result.answer, result.answer_rechecks, result.unsupported_rechecks) == ("".join(parts), 0, 0)


@respx.mock
async def test_other_question_is_still_streamed_piece_by_piece(model):
    respx.post(CHAT_URL).mock(return_value=text_response("잠은 ", "규칙적으로."))
    result, events = await run(model, "잠을 잘 자려면 어떻게 해야 해?")

    assert tokens(events) == ["잠은 ", "규칙적으로."]
    assert result.finish_reason == "stop"


@pytest.mark.parametrize(
    ("question", "parts", "answer"),
    [
        (SLEEP_GENERAL, ("수면 유도제는 자기 전에 ", "1정 드시면 돼요."), SLEEP_ANSWER),
        (SLEEP_GENERAL, ("가끔 드시는 건 ", "괜찮아요."), SLEEP_ANSWER),
        ("진통제랑 해열제는 뭐가 달라?", ("두통에는 진통제를 ", "드셔 보세요."), OTHER_ANSWER),
        ("영양제랑 약은 뭐가 달라?", ("영양제는 하루 ", "2알씩 드시면 돼요."), OTHER_ANSWER),
    ],
)
@respx.mock
async def test_general_question_with_leaking_answer_gets_fixed_answer(model, question, parts, answer):
    route = respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    result, events = await run(model, question)

    assert route.call_count == 1
    assert (result.answer, result.finish_reason, result.blocked_by) == (answer, "guard", "medication")
    assert result.evidence == []
    # 새는 답의 글자는 한 조각도 보내지 않는다
    assert tokens(events) == [answer]
    assert kinds(events) == ["status:thinking", "status:answering", "token", "done"]
    assert result.messages[-1].content == answer


@respx.mock
async def test_general_question_with_tool_call_sends_evidence_only_when_answer_passes(model):
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(SLEEP_TOOL, text=("기록도 확인해 볼게요. ",)),
            text_response("수면제는 처방이 필요한 약이에요."),
        ]
    )
    result, events = await run(model, SLEEP_GENERAL, port=sample_health_data("u1", NOW))

    assert result.answer == "기록도 확인해 볼게요. 수면제는 처방이 필요한 약이에요."
    assert tokens(events) == [result.answer]
    assert result.evidence and kinds(events)[-3:] == ["evidence", "token", "done"]


@respx.mock
async def test_text_before_tool_call_is_checked_with_final_answer(model):
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(SLEEP_TOOL, text=("반 알만 드셔 보세요. ",)),
            text_response("최근 기록도 함께 확인했어요."),
        ]
    )
    result, events = await run(model, SLEEP_GENERAL, port=sample_health_data("u1", NOW))

    assert (result.answer, result.finish_reason, result.evidence) == (SLEEP_ANSWER, "guard", [])
    assert tokens(events) == [SLEEP_ANSWER]
    assert "evidence" not in kinds(events)


@respx.mock
async def test_general_question_at_tool_limit_sends_limit_answer(model):
    respx.post(CHAT_URL).mock(return_value=text_response())
    result, events = await run(model, SLEEP_GENERAL, max_tool_rounds=0)

    assert (result.answer, result.finish_reason) == (TOOL_LIMIT_ANSWER, "tool_limit")
    assert tokens(events) == [TOOL_LIMIT_ANSWER]


@pytest.mark.parametrize("reply", ["응", "알려 줘"])
@respx.mock
async def test_reply_to_next_question_looks_up_sleep_records_in_normal_flow(model, reply):
    route = respx.post(CHAT_URL).mock(
        side_effect=[tool_call_response(SLEEP_TOOL), text_response("최근 7일 ", "잠든 시각을 보면요.")]
    )
    ctx = replace(CTX, turns=(turn(SLEEP_DECISION, SLEEP_ANSWER),))
    result, events = await run(model, reply, port=sample_health_data("u1", NOW), ctx=ctx)

    # 가드에 다시 걸리지 않고, 앞 대화의 제안을 받은 LLM 이 수면 기록을 조회한다
    assert route.call_count == 2 and result.blocked_by is None and result.finish_reason == "stop"
    first = json.loads(route.calls[0].request.content)
    assert [(m["role"], m["content"]) for m in first["messages"][1:]] == [
        ("user", SLEEP_DECISION),
        ("assistant", SLEEP_ANSWER),
        ("user", reply),
    ]
    # 시스템 메시지에 약 안내를 따로 붙이지 않은 평소 요청이다
    assert first["messages"][0]["content"] == render_system_prompt(load_system_prompt(), TODAY)
    assert [e["tool"] for e in events if e["type"] == "status" and e["stage"] == "tool_call"] == ["get_sleep_records"]
    assert result.evidence
    # 답을 검사하지 않는 평소 흐름이라 조각마다 바로 보낸다
    assert tokens(events) == ["최근 7일 ", "잠든 시각을 보면요."]


@pytest.mark.parametrize(
    ("question", "answer"),
    [("그럼 하루 몇 알 먹어?", SLEEP_ANSWER), ("그럼 멜라토닌은 먹어도 돼?", SLEEP_ANSWER)],
)
@respx.mock
async def test_drug_question_after_next_question_is_blocked_again(model, question, answer):
    route = respx.post(CHAT_URL).mock(return_value=text_response("x"))
    ctx = replace(CTX, turns=(turn(SLEEP_DECISION, SLEEP_ANSWER),))
    result, _ = await run(model, question, ctx=ctx)

    assert route.call_count == 0
    assert (result.answer, result.blocked_by) == (answer, "medication")
