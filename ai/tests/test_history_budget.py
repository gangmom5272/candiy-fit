"""LLM 호출 하나의 이전 대화 예산 검사 테스트. LLM 을 호출하지 않는다."""

import json
from datetime import date

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import invalid_tool_call

from coach_ai.agent.budget import (
    PreviousConversation,
    count_message_tokens,
    count_request_tokens,
    fit_request,
    reserved_output_tokens,
)
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import ChatTurn, build_messages
from coach_ai.agent.prompts import (
    OMITTED_HISTORY_NOTICE,
    load_system_prompt,
    other_day_notice,
    render_system_prompt,
    system_with_history,
)
from coach_ai.history import budget as history_budget
from coach_ai.history.budget import (
    CONTEXT_TOKENS,
    INPUT_TOKENS_LIMIT,
    INPUT_TOO_LARGE_MESSAGE,
    OUTPUT_TOKENS,
    RECENT_TURNS_MAX_TOKENS,
    SAFETY_MARGIN,
    HistoryFit,
    InputTooLargeError,
    fit_history,
    input_tokens_limit,
)
from coach_ai.history.tokens import MESSAGE_TOKENS, count_tokens, count_tool_definition_tokens
from coach_ai.history.types import HistoryTurn, HistoryUsage, StoredSummary
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.llm_fakes import CHAT_URL, completion

# 쌍 하나에 붙는 메시지 표시 (질문·답변)
PAIR = 2 * MESSAGE_TOKENS


def output_for(limit: int) -> int:
    """추정 입력 상한이 limit 가 되는 출력 예약."""
    return CONTEXT_TOKENS - SAFETY_MARGIN - limit


def digit_turn(n: int, tokens: int) -> HistoryTurn:
    """n 번째 쌍. 숫자만 쓰므로 글자 수가 곧 토큰 수다."""
    half = tokens // 2
    return HistoryTurn(
        request_id=f"request-{n}",
        question_message_id=f"message-{2 * n - 1}",
        answer_message_id=f"message-{2 * n}",
        question_seq=2 * n - 1,
        answer_seq=2 * n,
        question="1" * half,
        answer="2" * (tokens - half),
    )


def system(summary_used: bool, omitted: bool) -> int:
    """시스템 메시지 몫. 요약이 300, 뺀 이전 대화 안내가 40 이다."""
    return 1000 + (300 if summary_used else 0) + (40 if omitted else 0)


def fit(turns, rest, *, has_summary=True, output_tokens=OUTPUT_TOKENS):
    return fit_history(
        turns, rest_tokens=rest, system_tokens=system, has_summary=has_summary, output_tokens=output_tokens
    )


def test_input_limit_leaves_output_reservation_and_margin():
    assert (OUTPUT_TOKENS, INPUT_TOKENS_LIMIT) == (1000, 14_872)
    assert input_tokens_limit(OUTPUT_TOKENS) == INPUT_TOKENS_LIMIT
    assert input_tokens_limit(1200) == 16_384 - 1200 - 512


def test_larger_output_reservation_lowers_the_limit():
    turns = (digit_turn(1, 100),)
    rest = input_tokens_limit(1200) - (1300 + 100 + PAIR)
    assert fit(turns, rest, output_tokens=1200) == HistoryFit(0, True, False, input_tokens_limit(1200))
    assert fit(turns, rest + 1, output_tokens=1200).first_turn == 1
    limit = input_tokens_limit(1200)
    with pytest.raises(InputTooLargeError) as caught:
        fit((), limit - 1000 + 1, has_summary=False, output_tokens=1200)
    assert (caught.value.tokens, caught.value.limit) == (limit + 1, limit)
    assert str(caught.value) == f"input_too_large: 추정 입력 {limit + 1} > {limit}"


@pytest.mark.parametrize(
    ("overrides", "expected"), [({}, 1000), ({"max_tokens": 1200}, 1200), ({"max_tokens": None}, None)]
)
@respx.mock
async def test_reserved_output_is_max_tokens_of_request(settings, overrides, expected):
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion()))
    model = create_chat_model(settings, recorder=MetricsRecorder(), **overrides)
    await model.ainvoke("질문")

    # 요청에 최대 출력을 넣지 않으면 서버가 남은 컨텍스트만큼 생성할 수 있어 합의한 답변 몫을 예약한다
    body = json.loads(route.calls.last.request.content)
    assert body.get("max_tokens") == expected
    assert reserved_output_tokens(model) == (OUTPUT_TOKENS if expected is None else expected)


def test_reserved_output_of_model_without_max_tokens_is_default():
    class Plain:
        pass

    assert reserved_output_tokens(Plain()) == OUTPUT_TOKENS


def test_everything_fits_when_total_equals_limit():
    turns = (digit_turn(1, 100), digit_turn(2, 200))
    rest = INPUT_TOKENS_LIMIT - (1300 + 300 + 2 * PAIR)
    assert fit(turns, rest) == HistoryFit(first_turn=0, summary_used=True, omitted=False, tokens=INPUT_TOKENS_LIMIT)


def test_oldest_pair_goes_first_when_one_over_limit():
    turns = (digit_turn(1, 100), digit_turn(2, 200))
    rest = INPUT_TOKENS_LIMIT - (1300 + 300 + 2 * PAIR) + 1
    # 안내 40 을 더하고 첫 쌍 100 + 16 을 뺀다
    assert fit(turns, rest) == HistoryFit(1, True, True, INPUT_TOKENS_LIMIT + 1 + 40 - 116)


def test_pairs_go_oldest_first_until_total_fits():
    turns = (digit_turn(1, 300), digit_turn(2, 300), digit_turn(3, 300))
    full = 1300 + 900 + 3 * PAIR
    # 두 쌍을 빼야 들어가는 크기
    rest = INPUT_TOKENS_LIMIT - full + 400
    result = fit(turns, rest)
    assert (result.first_turn, result.summary_used, result.omitted) == (2, True, True)
    assert result.tokens == full + rest + 40 - 2 * (300 + PAIR) <= INPUT_TOKENS_LIMIT


def test_notice_tokens_count_when_choosing_pairs():
    # 빈 쌍은 16 이라 안내 40 보다 작다. 쌍을 빼도 줄지 않아 요약까지 뺀다
    turns = (digit_turn(1, 0), digit_turn(2, 0))
    rest = INPUT_TOKENS_LIMIT - (1300 + 2 * PAIR) + 1
    assert fit(turns, rest) == HistoryFit(2, False, True, 1040 + rest)


def test_summary_goes_after_all_pairs():
    turns = (digit_turn(1, 100),)
    # 쌍을 모두 빼도 요약 300 때문에 넘고, 요약까지 빼면 들어간다
    rest = INPUT_TOKENS_LIMIT - 1040 - 100
    assert fit(turns, rest) == HistoryFit(1, False, True, INPUT_TOKENS_LIMIT - 100)


def test_summary_only_is_dropped_with_notice():
    rest = INPUT_TOKENS_LIMIT - 1040
    assert fit((), rest) == HistoryFit(0, False, True, INPUT_TOKENS_LIMIT)


def test_input_too_large_when_nothing_fits():
    turns = (digit_turn(1, 100),)
    with pytest.raises(InputTooLargeError) as caught:
        fit(turns, INPUT_TOKENS_LIMIT - 1040 + 1)
    e = caught.value
    assert (e.code, e.retryable, e.message) == ("input_too_large", False, INPUT_TOO_LARGE_MESSAGE)
    assert e.tokens == INPUT_TOKENS_LIMIT + 1
    assert str(e) == f"input_too_large: 추정 입력 {INPUT_TOKENS_LIMIT + 1} > {INPUT_TOKENS_LIMIT}"


def test_without_previous_conversation_only_limit_is_checked():
    assert fit((), INPUT_TOKENS_LIMIT - 1000, has_summary=False) == HistoryFit(0, False, False, INPUT_TOKENS_LIMIT)
    with pytest.raises(InputTooLargeError):
        fit((), INPUT_TOKENS_LIMIT - 999, has_summary=False)


def test_recent_pairs_stay_within_2400_text_tokens_even_when_total_fits():
    assert RECENT_TURNS_MAX_TOKENS == 2400
    # 최신 쌍부터 1, 801, 1,501, 2,401 이라 가장 오래된 쌍은 넣지 않는다
    turns = (digit_turn(1, 900), digit_turn(2, 700), digit_turn(3, 800), digit_turn(4, 1))
    assert fit(turns, 0) == HistoryFit(1, True, True, 1340 + 1501 + 3 * PAIR)
    turns = (digit_turn(1, 900), digit_turn(2, 700), digit_turn(3, 800))
    assert fit(turns, 0) == HistoryFit(0, True, False, 1300 + 2400 + 3 * PAIR)


def test_latest_pair_over_2400_is_left_out():
    turns = (digit_turn(1, 10), digit_turn(2, 2401))
    assert fit(turns, 0) == HistoryFit(2, True, True, 1340)


SYSTEM_PROMPT = render_system_prompt(load_system_prompt(), date(2026, 10, 15))
SUMMARY = StoredSummary(text="걸음 수를 자주 묻는다.", until_seq=2)
TURNS = (digit_turn(2, 120), digit_turn(3, 80))


def state_messages(question: str, summary=SUMMARY, turns=TURNS, extra=(), day_notice=None):
    chats = [chat for t in turns for chat in (ChatTurn("user", t.question), ChatTurn("assistant", t.answer))]
    text = None if summary is None else summary.text
    return [*build_messages(question, text, chats, SYSTEM_PROMPT, day_notice=day_notice), *extra]


TOOL_ROUND = (
    AIMessage(content="", tool_calls=[{"name": "get_metric_summary", "args": {"metric": "steps"}, "id": "call_1"}]),
    ToolMessage(content='{"evidence_ids": ["E1"], "result": {"avg": 7412}}', tool_call_id="call_1"),
)


def test_message_tokens_count_text_template_and_tool_calls():
    assert count_message_tokens(HumanMessage(content="1234")) == MESSAGE_TOKENS + 4
    [ai, tool] = TOOL_ROUND
    call = count_tokens('get_metric_summary{"metric": "steps"}')
    assert count_message_tokens(ai) == MESSAGE_TOKENS + call
    assert count_message_tokens(tool) == MESSAGE_TOKENS + count_tokens(tool.content)
    bad = AIMessage(content="12", invalid_tool_calls=[invalid_tool_call(name="x", args="{oops", id="c", error=None)])
    assert count_message_tokens(bad) == MESSAGE_TOKENS + 2 + count_tokens("x{oops")


def test_request_tokens_add_tool_definitions_only_when_tools_are_bound():
    messages = state_messages("오늘은?")
    plain = sum(map(count_message_tokens, messages))
    assert count_request_tokens(messages) == count_request_tokens(messages, None) == plain
    assert count_request_tokens(messages, tool_specs()) == plain + count_tool_definition_tokens(tool_specs())


def usage(summary, turns, received):
    return HistoryUsage.of(summary, turns, received_turns=received)


def test_request_is_returned_as_is_when_history_fits():
    messages = state_messages("오늘은?", extra=TOOL_ROUND)
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT, summary=SUMMARY, turns=TURNS)
    sent, used = fit_request(messages, previous, tools=tool_specs())
    assert sent == messages and all(a is b for a, b in zip(sent, messages, strict=True))
    assert used == usage(SUMMARY, TURNS, 2) and used.excluded_turns == 0


def test_request_drops_oldest_pair_and_adds_notice_after_summary():
    messages = state_messages("오늘은?", extra=TOOL_ROUND)
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT, summary=SUMMARY, turns=TURNS)
    full = count_request_tokens(messages, tool_specs())
    sent, used = fit_request(messages, previous, tools=tool_specs(), output_tokens=output_for(full - 1))

    system = system_with_history(SYSTEM_PROMPT, SUMMARY.text, omitted=True)
    assert system.endswith(f"## 이전 대화 요약\n\n{SUMMARY.text}\n\n{OMITTED_HISTORY_NOTICE}")
    assert sent == [SystemMessage(content=system), *messages[3:]]
    assert [m.content for m in sent[1:3]] == [TURNS[1].question, TURNS[1].answer]
    assert used == usage(SUMMARY, TURNS[1:], 2) and used.excluded_turns == 1
    assert count_request_tokens(sent, tool_specs()) <= full - 1


def test_other_day_notice_follows_summary_and_counts_in_budget():
    notice = other_day_notice([date(2026, 10, 14), date(2026, 10, 15)], date(2026, 10, 15))
    messages = state_messages("오늘은?", extra=TOOL_ROUND, day_notice=notice)
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT, summary=SUMMARY, turns=TURNS, day_notice=notice)
    head = f"{SYSTEM_PROMPT}\n\n## 이전 대화 요약\n\n{SUMMARY.text}\n\n{notice}"
    assert messages[0].content == head
    # 안내 없이 상한에 딱 맞는 요청은 안내를 붙이면 넘어서 가장 오래된 쌍을 뺀다. 빠진 대화 안내는 그 뒤에 붙는다
    output = output_for(count_request_tokens(state_messages("오늘은?", extra=TOOL_ROUND), tool_specs()))
    sent, used = fit_request(messages, previous, tools=tool_specs(), output_tokens=output)
    assert sent == [SystemMessage(content=f"{head}\n\n{OMITTED_HISTORY_NOTICE}"), *messages[3:]]
    assert used == usage(SUMMARY, TURNS[1:], 2)
    # 요약과 쌍을 모두 빼도 안내는 남는다
    without = system_with_history(SYSTEM_PROMPT, None, omitted=True, day_notice=notice)
    assert without == f"{SYSTEM_PROMPT}\n\n{notice}\n\n{OMITTED_HISTORY_NOTICE}"


def test_request_drops_summary_after_all_pairs():
    messages = state_messages("오늘은?")
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT, summary=SUMMARY, turns=TURNS)
    without = [SystemMessage(content=system_with_history(SYSTEM_PROMPT, None, omitted=True)), messages[-1]]
    output = output_for(count_request_tokens(without))
    sent, used = fit_request(messages, previous, tools=None, output_tokens=output)

    assert sent == without and "## 이전 대화 요약" not in sent[0].content
    assert used == HistoryUsage(
        summary_used=False, included_turns=0, excluded_turns=2, history_tokens=0, summary_tokens=0
    )


def test_request_over_limit_without_previous_conversation_raises():
    messages = state_messages("오늘은?", summary=None, turns=())
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT)
    limit = count_request_tokens(messages, tool_specs())
    fitted = fit_request(messages, previous, tools=tool_specs(), output_tokens=output_for(limit))
    assert fitted == (messages, usage(None, (), 0))
    with pytest.raises(InputTooLargeError):
        fit_request(messages, previous, tools=tool_specs(), output_tokens=output_for(limit - 1))


def test_request_fit_does_not_read_design_allocation(monkeypatch):
    # SYSTEM_TOKENS·TOOL_RESULT_RESERVE_TOKENS 는 설계 배분이다. 예전 값과 지금 값에서 예산 검사 결과가 같다
    messages = state_messages("오늘은?", extra=TOOL_ROUND)
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT, summary=SUMMARY, turns=TURNS)
    full = count_request_tokens(messages, tool_specs())
    outcomes = []
    for system, tool_reserve in (
        (3_200, 3_500),
        (3_400, 3_300),
        (4_200, 2_500),
        (4_400, 2_300),
        (4_700, 2_300),
        (4_800, 2_300),
        (5_000, 2_300),
        (5_500, 2_300),
    ):
        monkeypatch.setattr(history_budget, "SYSTEM_TOKENS", system)
        monkeypatch.setattr(history_budget, "TOOL_RESULT_RESERVE_TOKENS", tool_reserve)
        outcomes.append(
            [fit_request(messages, previous, tools=tool_specs(), output_tokens=output_for(full - c)) for c in (0, 1)]
        )
    assert all(outcome == outcomes[0] for outcome in outcomes)
    # 상한에 맞으면 그대로 보내고, 1 넘으면 가장 오래된 쌍부터 뺀다
    [(sent, used), (cut, cut_used)] = outcomes[-1]
    assert sent == messages and used.excluded_turns == 0
    assert cut_used.excluded_turns == 1 and cut_used.summary_used and cut[0].content.endswith(OMITTED_HISTORY_NOTICE)


def test_request_fit_counts_actual_system_prompt_of_each_version():
    # 고정 부분은 프롬프트 버전에 따라 바뀌고, 예산 검사는 호출마다 실제 값을 센다.
    # v1.5 로 상한에 딱 맞는 요청을 시스템 프롬프트가 더 긴 v1.6 으로 보내면 가장 오래된 쌍부터 뺀다
    turns = (digit_turn(2, 400), digit_turn(3, 80))
    chats = [chat for t in turns for chat in (ChatTurn("user", t.question), ChatTurn("assistant", t.answer))]

    def request(version: str) -> tuple[list, PreviousConversation]:
        system = render_system_prompt(load_system_prompt(version), date(2026, 10, 15))
        previous = PreviousConversation(system_prompt=system, summary=SUMMARY, turns=turns)
        return build_messages("오늘은?", SUMMARY.text, chats, system), previous

    old, new = request("v1.5"), request("v1.6")
    output = output_for(count_request_tokens(old[0], tool_specs()))
    assert count_request_tokens(new[0], tool_specs()) > count_request_tokens(old[0], tool_specs())
    sent, used = fit_request(*old, tools=tool_specs(), output_tokens=output)
    assert sent == old[0] and used.excluded_turns == 0
    sent, used = fit_request(*new, tools=tool_specs(), output_tokens=output)
    assert (used.excluded_turns, used.summary_used) == (1, True)
    assert sent[0].content.endswith(OMITTED_HISTORY_NOTICE) and sent[1:3] == new[0][3:5]


def test_input_too_large_error_has_no_conversation_text():
    question = "어제 수면 기록을 보여 줘"
    messages = state_messages(question)
    previous = PreviousConversation(system_prompt=SYSTEM_PROMPT, summary=SUMMARY, turns=TURNS)
    with pytest.raises(InputTooLargeError) as caught:
        fit_request(messages, previous, tools=None, output_tokens=output_for(10))
    text = f"{caught.value} {caught.value.message} {caught.value!r}"
    for secret in (question, SUMMARY.text, TURNS[0].question, TURNS[0].answer, "걸음"):
        assert secret not in text
