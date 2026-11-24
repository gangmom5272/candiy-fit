"""대화 이력 연동(coach_ai.history) 테스트. LLM 을 호출하지 않는다."""

import copy
import dataclasses
import json
import uuid
from datetime import UTC, date, datetime
from typing import get_args, get_type_hints

import pytest

from bench import trend_tokens as tt
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.graph import ChatTurn
from coach_ai.agent.prompts import load_system_prompt, render_system_prompt
from coach_ai.history import budget
from coach_ai.history.summary import (
    SUMMARY_INSTRUCTION,
    generate_summary,
    plan_summary,
    summary_input,
    summary_request_tokens,
)
from coach_ai.history.tokens import (
    MESSAGE_TOKENS,
    REST_RATIO,
    TOKEN_POLICY_VERSION,
    TOOL_STRUCTURE_RATIO,
    count_tokens,
    count_tool_definition_tokens,
    estimate_tokens,
)
from coach_ai.history.types import (
    HistoryTurn,
    HistoryUsage,
    StoredSummary,
    SummaryError,
    SummaryErrorCode,
    SummaryPlan,
    SummaryResult,
)


def ids(question_seq: object, answer_seq: object) -> dict[str, str]:
    """요청·메시지 ID. 테스트 안에서만 고유하면 되므로 순서 번호로 만든다."""
    return {
        "request_id": f"request-{question_seq}",
        "question_message_id": f"message-{question_seq}",
        "answer_message_id": f"message-{answer_seq}",
    }


def turn(question_seq: int, answer_seq: int | None = None, *, question: str = "질문", answer: str = "답변"):
    answer_seq = question_seq + 1 if answer_seq is None else answer_seq
    return HistoryTurn(
        **ids(question_seq, answer_seq),
        question_seq=question_seq,
        answer_seq=answer_seq,
        question=question,
        answer=answer,
    )


def test_count_tokens_counts_digit_as_one_and_rest_by_ratio():
    # 계수·고정분이나 요약 대상 선정 기준을 바꾸면 기대값과 함께 TOKEN_POLICY_VERSION 도 바꾼다
    assert TOKEN_POLICY_VERSION == "estimate-v6"
    assert (MESSAGE_TOKENS, TOOL_STRUCTURE_RATIO) == (8, 0.5)
    assert count_tokens("") == 0
    assert count_tokens("2026") == 4
    assert count_tokens("기록 없음") == 3  # 5글자 × 0.544 = 2.72
    assert count_tokens('{"date": "2026-10-21"}') == 8 + 8  # 숫자 8 + 나머지 14글자 × 0.544 = 7.616
    assert count_tokens("가" * 1000) == 544


def test_bench_estimates_with_shared_function():
    assert tt.estimate_tokens is estimate_tokens and tt.REST_RATIO == REST_RATIO
    text = "지난 7일 평균 7,412걸음이에요."
    assert count_tokens(text) == estimate_tokens(text, rest_ratio=REST_RATIO)


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("😀", 4),  # 이모지는 UTF-8 4바이트
        ("→", 3),
        ("★※", 6),
        ("é", 2),
        ("ㅋㅋ", 6),  # 한글 음절이 아닌 자모
        ("漢", 3),
        ("①", 3),  # ASCII 밖 숫자
        ("１", 3),
        ("좋아요👍", 2 + 4),  # 한글 3글자 × 0.544 = 1.632
        ("👍🏽", 8),  # 피부색 수정자도 코드 포인트마다 센다
        ("👨‍👩‍👧", 4 + 3 + 4 + 3 + 4),  # 결합 문자(ZWJ)는 3바이트
    ],
)
def test_count_tokens_counts_other_characters_by_utf8_bytes(text, tokens):
    assert count_tokens(text) == tokens


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("\ud800", 3),  # 형식이 잘못된 글자 하나
        ("\udc00\ud83d", 3 + 3),  # 형식이 잘못된 글자 두 개
        (chr(0xD83D) + chr(0xDE00), 3 + 3),  # 짝은 맞지만 한 글자로 합쳐지지 않은 두 개
        ("가\udfff가", 1 + 3),  # 앞뒤 한글 2글자 × 0.544 = 1.088
        ("😀", 4),  # 정상 이모지는 그대로 4바이트
    ],
)
def test_count_tokens_counts_each_lone_surrogate_as_three_bytes(text, tokens):
    assert count_tokens(text) == tokens


def test_lone_surrogate_does_not_break_pair_tokens_or_summary_plan():
    broken = turn(1, question="\ud800" * 800, answer="")
    assert broken.tokens == 2400
    plan = plan_summary(None, (broken, turn(3)))
    assert plan.targets == (broken,)
    assert summary_request_tokens(None, plan.targets) > broken.tokens


def test_middle_dot_and_whitespace_stay_at_ratio():
    assert count_tokens("걸음·심박") == 3  # 5글자 × 0.544 = 2.72
    assert count_tokens("가　나\xa0다") == 3  # ASCII 밖 공백도 비율로 센다


def test_emoji_only_question_of_2000_chars_is_not_counted_below_bytes():
    question = "😀" * 2000
    assert count_tokens(question) == len(question.encode("utf-8")) == 8000


def v2_count(text: str) -> int:
    digits = sum(ch.isdigit() for ch in text)
    return digits + round(REST_RATIO * (len(text) - digits))


def test_texts_without_other_characters_keep_v2_counts():
    texts = [
        render_system_prompt(load_system_prompt(), date(2026, 10, 28)),
        json.dumps(tool_specs(), ensure_ascii=False, separators=(",", ":")),
        '{"evidence_ids": ["E1"], "result": {"avg": {"value": 7412.0, "display": "7,412걸음"}}}',
        "최근 7일 동안의 평균 수면 시간은 6시간 36분이에요. 10월 20일은 기록이 없어요.",
    ]
    for text in texts:
        assert count_tokens(text) == v2_count(text)


TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "get_x",
        "description": "기록을 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {"period": {"type": "string", "description": "기간", "enum": ["today", "last_7_days"]}},
            "required": ["period"],
        },
    },
}
# TOOL_SPEC 의 description 을 빈 문자열로 바꾼 JSON. 209글자 중 숫자 1개
TOOL_STRUCTURE = (
    '[{"type":"function","function":{"name":"get_x","description":"","parameters":{"type":"object","properties":'
    '{"period":{"type":"string","description":"","enum":["today","last_7_days"]}},"required":["period"]}}}]'
)


def test_tool_definition_counts_descriptions_as_text_and_structure_by_ratio():
    descriptions = count_tokens("기록을 조회한다.") + count_tokens("기간")
    structure = round(TOOL_STRUCTURE_RATIO * count_tokens(TOOL_STRUCTURE))
    assert (descriptions, count_tokens(TOOL_STRUCTURE), structure) == (6, 114, 57)
    assert count_tool_definition_tokens([TOOL_SPEC]) == 63
    assert count_tool_definition_tokens(()) == round(TOOL_STRUCTURE_RATIO * count_tokens("[]"))


def test_tool_definition_keeps_specs_and_leaves_non_text_description_in_structure():
    # 인자 이름이 description 이면 값이 객체라 설명 문장이 아니다
    spec = copy.deepcopy(TOOL_SPEC)
    params = spec["function"]["parameters"]
    params["properties"]["description"] = {"type": "string", "description": "설명"}
    before = copy.deepcopy(spec)
    blank = copy.deepcopy(spec)
    blank["function"]["description"] = ""
    for prop in blank["function"]["parameters"]["properties"].values():
        prop["description"] = ""
    structure = json.dumps([blank], ensure_ascii=False, separators=(",", ":"))
    expected = sum(map(count_tokens, ("기록을 조회한다.", "기간", "설명")))
    expected += round(TOOL_STRUCTURE_RATIO * count_tokens(structure))
    assert count_tool_definition_tokens([spec]) == expected
    assert spec == before


def test_budget_numbers_match_agreed_allocation():
    allocation = (
        budget.SYSTEM_TOKENS,
        budget.SUMMARY_MAX_TOKENS,
        budget.RECENT_TURNS_MAX_TOKENS,
        budget.TOOL_RESULT_RESERVE_TOKENS,
        budget.OUTPUT_TOKENS,
        budget.SAFETY_MARGIN,
    )
    # 시스템 몫 5,500 은 프로필과 이력 안내를 넣은 고정 부분 이상이고(test_profile_context.py), 배분 합은 12,512 다.
    # 실제 제약은 입력 상한 하나다 (docs/agent-interface.md 4.5)
    assert allocation == (5500, 800, 2400, 2300, 1000, 512) and sum(allocation) == 12_512
    assert budget.CONTEXT_TOKENS == 16_384 and budget.INPUT_TOKENS_LIMIT == 14_872


@pytest.mark.parametrize("version", [None, "v1.8"])
def test_default_prompt_and_tools_fit_system_allocation(version):
    # 설계 배분이 실제 고정 부분보다 작아지면 이 테스트로 알 수 있다. 날짜에 따라 1~2토큰 달라진다
    template = load_system_prompt() if version is None else load_system_prompt(version)
    system = render_system_prompt(template, date(2026, 12, 31))
    fixed = MESSAGE_TOKENS + count_tokens(system) + count_tool_definition_tokens(tool_specs())
    assert fixed <= budget.SYSTEM_TOKENS


def test_turn_has_agreed_fields_and_counts_both_texts():
    names = [f.name for f in dataclasses.fields(HistoryTurn)]
    assert names == [
        "request_id", "question_message_id", "answer_message_id", "question_seq", "answer_seq", "question", "answer",
        "answered_on",
    ]  # fmt: skip
    pair = HistoryTurn(
        request_id="request-5",
        question_message_id="message-5",
        answer_message_id="message-6",
        question_seq=5,
        answer_seq=6,
        question="12",
        answer="345",
    )
    assert (pair.request_id, pair.question_message_id, pair.answer_message_id) == (
        "request-5",
        "message-5",
        "message-6",
    )
    assert (pair.question_seq, pair.answer_seq, pair.question, pair.answer) == (5, 6, "12", "345")
    assert pair.answered_on is None
    assert pair.tokens == 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        pair.answer = "바뀐 답변"  # type: ignore[misc]
    with pytest.raises(TypeError):
        HistoryTurn("request-5", "message-5", "message-6", 5, 6, "질문", "답변")  # type: ignore[misc]


def test_turn_allows_empty_texts():
    # 정상 완료된 요청의 답변이 빈 문자열일 수 있다
    assert turn(1, question="", answer="").tokens == 0


def test_turn_takes_answer_date_without_counting_it():
    dated = dataclasses.replace(turn(5, question="12", answer="345"), answered_on=date(2026, 10, 28))
    assert dated.answered_on == date(2026, 10, 28)
    # 날짜는 요약 입력에만 들어가고 쌍의 토큰 수(원문 예산·요약 시작 기준)에는 들어가지 않는다
    assert dated.tokens == 5


@pytest.mark.parametrize("value", ["2026-10-28", datetime(2026, 10, 28, 9, tzinfo=UTC), 20261028, True])
def test_turn_requires_date_or_none_for_answer_date(value):
    with pytest.raises(ValueError):
        HistoryTurn(**ids(1, 2), question_seq=1, answer_seq=2, question="질문", answer="답변", answered_on=value)


@pytest.mark.parametrize("field", ["request_id", "question_message_id", "answer_message_id"])
@pytest.mark.parametrize("value", ["", "  ", None, 1, uuid.UUID(int=1)])
def test_turn_requires_non_empty_text_ids(field, value):
    kw = {**ids(1, 2), field: value}
    with pytest.raises(ValueError):
        HistoryTurn(**kw, question_seq=1, answer_seq=2, question="질문", answer="답변")


@pytest.mark.parametrize(
    ("question_seq", "answer_seq"),
    [(0, 1), (-1, 2), (3, 3), (4, 3), (True, 2), (1, True), ("1", 2), (1, 2.0), (None, 2)],
)
def test_turn_requires_positive_seqs_with_answer_after_question(question_seq, answer_seq):
    with pytest.raises(ValueError):
        HistoryTurn(
            **ids(question_seq, answer_seq),
            question_seq=question_seq,
            answer_seq=answer_seq,
            question="질문",
            answer="답변",
        )


@pytest.mark.parametrize(("question", "answer"), [(None, "답변"), ("질문", None), (1, "답변"), ("질문", b"x")])
def test_turn_requires_text(question, answer):
    with pytest.raises(ValueError):
        HistoryTurn(**ids(1, 2), question_seq=1, answer_seq=2, question=question, answer=answer)


def test_summary_keeps_text_and_until_seq():
    summary = StoredSummary(text="합성 회원은 걸음 수를 자주 묻는다.", until_seq=4)
    assert (summary.text, summary.until_seq) == ("합성 회원은 걸음 수를 자주 묻는다.", 4)
    with pytest.raises(TypeError):
        StoredSummary("요약", 4)  # type: ignore[misc]


@pytest.mark.parametrize("text", ["", "  \n", None, 3])
def test_summary_requires_text(text):
    with pytest.raises(ValueError):
        StoredSummary(text=text, until_seq=4)


@pytest.mark.parametrize("until_seq", [0, -1, True, 1.5, "4", None])
def test_summary_requires_positive_until_seq(until_seq):
    with pytest.raises(ValueError):
        StoredSummary(text="요약", until_seq=until_seq)


SUMMARY = StoredSummary(text="합성 회원은 걸음 수를 자주 묻는다.", until_seq=4)


def context(**kw) -> CoachContext:
    return CoachContext(user_id="u1", conversation_id="c1", request_id="r1", **kw)


def test_context_has_no_summary_and_turns_by_default():
    ctx = context()
    assert ctx.summary is None and ctx.turns == ()


@pytest.mark.parametrize(
    "kw",
    [
        {"summary": None, "turns": ()},
        {"summary": SUMMARY},
        {"turns": (turn(1), turn(3))},
        {"summary": SUMMARY, "turns": (turn(5), turn(9, 12))},
    ],
)
def test_context_takes_summary_and_turns_after_it(kw):
    ctx = context(**kw)
    assert (ctx.summary, ctx.turns) == (kw.get("summary"), kw.get("turns", ()))


@pytest.mark.parametrize(
    "kw",
    [
        {"summary": "합성 요약 문자열"},
        {"summary": {"text": "요약", "until_seq": 4}},
        {"turns": [turn(1)]},
        {"turns": None},
        {"turns": (ChatTurn("user", "질문"), ChatTurn("assistant", "답변"))},
        {"turns": ({"question_seq": 1, "question": "질문", "answer_seq": 2, "answer": "답변"},)},
    ],
)
def test_context_rejects_wrong_history_types(kw):
    with pytest.raises(ValueError):
        context(**kw)


@pytest.mark.parametrize(
    "turns",
    [
        (turn(3), turn(1)),  # 오래된 순이 아니다
        (turn(1, 4), turn(3, 5)),  # 앞 쌍과 겹친다
        (turn(1, 2), turn(2, 3)),  # 앞 답변과 순서 번호가 같다
        (turn(1), turn(1)),
    ],
)
def test_context_rejects_out_of_order_turns(turns):
    with pytest.raises(ValueError):
        context(turns=turns)


@pytest.mark.parametrize("question_seq", [3, 4])
def test_context_rejects_turns_covered_by_summary(question_seq):
    # 요약에 포함된 메시지(until_seq 까지)는 원문 쌍으로 넘기지 않는다
    with pytest.raises(ValueError):
        context(summary=SUMMARY, turns=(turn(question_seq),))


@pytest.mark.parametrize("field", ["user_id", "conversation_id", "request_id", "tz"])
@pytest.mark.parametrize("value", ["", " ", None])
def test_context_keeps_text_field_validation(field, value):
    kw = {"user_id": "u1", "conversation_id": "c1", "request_id": "r1", field: value}
    with pytest.raises(ValueError):
        CoachContext(**kw)


def test_validation_errors_leave_out_ids():
    distinct = {
        "request_id": "request-7f3a9c2e",
        "question_message_id": "message-51d0b8e4",
        "answer_message_id": "message-9e26c1f7",
    }
    good = HistoryTurn(**distinct, question_seq=5, answer_seq=6, question="질문", answer="답변")
    attempts = [
        lambda: HistoryTurn(**distinct, question_seq=5, answer_seq=5, question="질문", answer="답변"),
        lambda: HistoryTurn(
            **{**distinct, "request_id": 987654321}, question_seq=5, answer_seq=6, question="", answer=""
        ),
        lambda: context(turns=(good, good)),
        lambda: context(summary=StoredSummary(text="요약", until_seq=6), turns=(good,)),
        lambda: SummaryPlan(summary=None, targets=(good,), until_seq=7, unsummarized_tokens=3001),
    ]
    messages = []
    for attempt in attempts:
        with pytest.raises(ValueError) as caught:
            attempt()
        messages.append(str(caught.value))
    for value in [*distinct.values(), "987654321"]:
        assert all(value not in message for message in messages)


COUNTS = {"included_turns": 0, "excluded_turns": 0, "history_tokens": 0, "summary_tokens": 0}


def test_usage_has_agreed_fields():
    names = [f.name for f in dataclasses.fields(HistoryUsage)]
    assert names == [
        "summary_used", "included_turns", "excluded_turns", "history_tokens", "summary_tokens", "token_policy_version",
    ]  # fmt: skip
    usage = HistoryUsage(summary_used=True, included_turns=2, excluded_turns=0, history_tokens=10, summary_tokens=7)
    assert (usage.summary_used, usage.included_turns, usage.excluded_turns) == (True, 2, 0)
    assert (usage.history_tokens, usage.summary_tokens, usage.token_policy_version) == (10, 7, "estimate-v6")


def test_usage_of_counts_included_turns_and_summary_as_sent():
    # 숫자는 한 글자가 1토큰이다. 요약은 앞뒤 공백을 뺀 글이 프롬프트에 들어간다
    summary = StoredSummary(text=" 1234567\n", until_seq=2)
    turns = (turn(3, question="12", answer="345"), turn(5, question="6789", answer="0"))
    assert HistoryUsage.of(summary, turns, received_turns=2) == HistoryUsage(
        summary_used=True, included_turns=2, excluded_turns=0, history_tokens=10, summary_tokens=7
    )
    # 받은 쌍 중 넣지 않은 쌍 수가 excluded_turns 다
    assert HistoryUsage.of(None, turns[1:], received_turns=2) == HistoryUsage(
        summary_used=False, included_turns=1, excluded_turns=1, history_tokens=5, summary_tokens=0
    )
    assert HistoryUsage.of(None, (), received_turns=0) == HistoryUsage(
        summary_used=False, included_turns=0, excluded_turns=0, history_tokens=0, summary_tokens=0
    )


def test_usage_of_rejects_fewer_received_than_included():
    with pytest.raises(ValueError):
        HistoryUsage.of(None, (turn(1),), received_turns=0)


@pytest.mark.parametrize("field", list(COUNTS))
@pytest.mark.parametrize("value", [-1, True, 1.0, None])
def test_usage_requires_non_negative_counts(field, value):
    with pytest.raises(ValueError):
        HistoryUsage(summary_used=False, **{**COUNTS, field: value})


@pytest.mark.parametrize("value", [1, 0, None, "true"])
def test_usage_requires_bool_summary_used(value):
    with pytest.raises(ValueError):
        HistoryUsage(summary_used=value, **COUNTS)


@pytest.mark.parametrize("version", ["", " ", None, 1])
def test_usage_requires_token_policy_version_text(version):
    with pytest.raises(ValueError):
        HistoryUsage(summary_used=False, token_policy_version=version, **COUNTS)


def test_plan_has_agreed_fields():
    names = [f.name for f in dataclasses.fields(SummaryPlan)]
    assert names == ["summary", "targets", "until_seq", "unsummarized_tokens", "policy_version"]
    targets = (turn(1, 2), turn(3, 5))
    plan = SummaryPlan(summary=None, targets=targets, until_seq=5, unsummarized_tokens=2400)
    assert (plan.summary, plan.targets, plan.until_seq) == (None, targets, 5)
    assert (plan.unsummarized_tokens, plan.policy_version) == (2400, "estimate-v6")
    previous = StoredSummary(text="요약", until_seq=6)
    assert SummaryPlan(summary=previous, targets=(turn(7, 8),), until_seq=8, unsummarized_tokens=3001).until_seq == 8


@pytest.mark.parametrize("until_seq", [3, 4, 6])
def test_plan_until_seq_must_be_answer_seq_of_last_target(until_seq):
    with pytest.raises(ValueError):
        SummaryPlan(summary=None, targets=(turn(1, 2), turn(3, 5)), until_seq=until_seq, unsummarized_tokens=2400)


@pytest.mark.parametrize(
    ("summary", "targets", "until_seq"),
    [
        (None, (), 1),  # 대상 쌍이 없다
        (None, [turn(1)], 2),  # 튜플이어야 한다
        (None, (turn(3), turn(1)), 2),
        (StoredSummary(text="요약", until_seq=4), (turn(3),), 4),
        ("요약", (turn(5),), 6),
    ],
)
def test_plan_requires_ordered_targets_after_summary(summary, targets, until_seq):
    with pytest.raises(ValueError):
        SummaryPlan(summary=summary, targets=targets, until_seq=until_seq, unsummarized_tokens=2400)


@pytest.mark.parametrize(
    ("field", "value"),
    [("unsummarized_tokens", -1), ("unsummarized_tokens", True), ("unsummarized_tokens", None)]
    + [("policy_version", v) for v in ("", " ", None, 1)],
)
def test_plan_requires_non_negative_tokens_and_policy_version_text(field, value):
    kw = {"summary": None, "targets": (turn(1),), "until_seq": 2, "unsummarized_tokens": 2400, field: value}
    with pytest.raises(ValueError):
        SummaryPlan(**kw)


def test_result_has_agreed_fields():
    assert [f.name for f in dataclasses.fields(SummaryResult)] == ["text", "until_seq", "tokens", "policy_version"]
    result = SummaryResult(text="새 누적 요약", until_seq=8, tokens=4)
    assert (result.text, result.until_seq) == ("새 누적 요약", 8)
    assert (result.tokens, result.policy_version) == (4, "estimate-v6")


@pytest.mark.parametrize(
    ("field", "value"),
    [("text", ""), ("text", " "), ("text", None), ("until_seq", 0), ("until_seq", True)]
    + [("tokens", v) for v in (-1, True, 1.0, None)]
    + [("policy_version", v) for v in ("", None)],
)
def test_result_requires_text_positive_until_seq_tokens_and_policy_version(field, value):
    kw = {"text": "요약", "until_seq": 8, "tokens": 1, field: value}
    with pytest.raises(ValueError):
        SummaryResult(**kw)


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("llm_unavailable", True),
        ("rate_limited", True),
        ("timeout", True),
        ("truncated", False),
        ("invalid_format", False),
        ("input_too_large", False),
        ("config", False),
    ],
)
def test_summary_error_retryable_only_for_first_three_codes(code, retryable):
    error = SummaryError(code)
    assert isinstance(error, Exception)
    assert (error.code, error.retryable) == (code, retryable)


def test_summary_error_codes_are_agreed_seven():
    assert set(get_args(SummaryErrorCode)) == {
        "llm_unavailable", "rate_limited", "timeout", "truncated", "invalid_format", "input_too_large", "config",
    }  # fmt: skip


@pytest.mark.parametrize("code", ["llm_timeout", "internal", ""])
def test_summary_error_rejects_unknown_code(code):
    with pytest.raises(ValueError):
        SummaryError(code)  # type: ignore[arg-type]


def pairs(*tokens: int, start: int = 1) -> tuple[HistoryTurn, ...]:
    """쌍마다 토큰 수가 tokens 인 쌍 목록. 숫자는 한 글자가 1토큰이다."""
    made = []
    for i, size in enumerate(tokens):
        half = size // 2
        made.append(turn(start + 2 * i, question="1" * half, answer="2" * (size - half)))
    assert [p.tokens for p in made] == list(tokens)
    return tuple(made)


@pytest.mark.parametrize(("tokens", "count"), [((1000, 1000, 399), None), ((1000, 1000, 400), 1)])
def test_plan_starts_at_2400_unsummarized_tokens(tokens, count):
    turns = pairs(*tokens)
    plan = plan_summary(None, turns)
    if count is None:
        assert plan is None
    else:
        # 최신 쌍부터 400 + 1,000 = 1,400 을 원문으로 남기고 앞의 1,000 을 요약한다
        assert plan.targets == turns[:count] and plan.summary is None
        assert (plan.unsummarized_tokens, plan.policy_version) == (2400, TOKEN_POLICY_VERSION)


@pytest.mark.parametrize(
    ("tokens", "count"),
    [
        ((900, 1000, 500), 1),  # 최신 두 쌍 1,500 을 남긴다
        ((899, 1002, 499), 2),  # 최신 두 쌍이 1,501 이라 최신 한 쌍 499 만 남긴다
    ],
)
def test_plan_keeps_latest_turns_up_to_1500_tokens(tokens, count):
    turns = pairs(*tokens)
    plan = plan_summary(None, turns)
    assert plan.targets == turns[:count]
    assert plan.until_seq == turns[count - 1].answer_seq


@pytest.mark.parametrize("latest", [1501, 2399, 2400])
def test_plan_keeps_latest_turn_over_1500_up_to_2400(latest):
    turns = pairs(400, 500, latest)
    plan = plan_summary(None, turns)
    # 최신 한 쌍은 원문 예산(2,400) 안이라 원문으로 남기고 앞의 쌍을 모두 요약한다
    assert plan.targets == turns[:2] and plan.until_seq == turns[1].answer_seq
    assert plan.unsummarized_tokens == 900 + latest


def test_plan_has_no_target_without_turns_before_kept_latest():
    # 요약을 시작하는 합과 원문으로 남기는 최신 한 쌍의 상한이 모두 2,400 이다
    assert plan_summary(None, pairs(2400)) is None


@pytest.mark.parametrize("tokens", [(2401,), (100, 2401), (1000, 1000, 5000)])
def test_plan_includes_latest_turn_over_2400(tokens):
    turns = pairs(*tokens)
    plan = plan_summary(None, turns)
    assert plan.targets == turns and plan.until_seq == turns[-1].answer_seq
    assert plan.unsummarized_tokens == sum(tokens)


def test_plan_carries_existing_summary_and_counts_only_unsummarized_turns():
    summary = StoredSummary(text="1" * 5000, until_seq=10)
    # 요약 토큰은 미요약 원문에 넣지 않는다
    assert plan_summary(summary, pairs(1000, 1000, 399, start=11)) is None

    turns = pairs(1000, 1000, 400, start=11)
    plan = plan_summary(summary, turns)
    assert plan.summary is summary and plan.targets == turns[:1] and plan.until_seq == 12
    assert plan.unsummarized_tokens == 2400


def test_plan_takes_list_and_until_seq_is_answer_seq_of_last_target():
    # 실패한 요청의 메시지(3, 4)가 사이에 있어 다음 질문 번호와 이어지지 않는다
    first = turn(1, 2, question="1" * 1000, answer="")
    second = turn(5, 6, question="1" * 1000, answer="")
    latest = turn(7, 8, question="1" * 400, answer="")
    plan = plan_summary(None, [first, second, latest])
    assert plan.targets == (first,) and plan.until_seq == 2


def test_summary_call_budget_fits_context():
    assert (budget.SUMMARY_INPUT_MAX_TOKENS, budget.SUMMARY_MAX_TOKENS) == (9_800, 800)
    total = budget.SUMMARY_INPUT_MAX_TOKENS + budget.SUMMARY_MAX_TOKENS + budget.SAFETY_MARGIN
    assert total <= budget.CONTEXT_TOKENS


def test_summary_request_counts_instruction_input_and_two_messages():
    summary = StoredSummary(text="걸음 수를 자주 묻는다.", until_seq=2)
    targets = pairs(10, 20, start=3)
    expected = 2 * MESSAGE_TOKENS + count_tokens(SUMMARY_INSTRUCTION) + count_tokens(summary_input(summary, targets))
    assert summary_request_tokens(summary, targets) == expected


def budget_pairs(total: int, day: date | None = None) -> tuple[HistoryTurn, ...]:
    """앞 두 쌍이 요약 대상이고 그 요약 요청 추정이 total 인 세 쌍. 마지막 쌍(1,000)은 원문으로 남는다.

    day 가 있으면 세 쌍 모두 그 날짜를 answered_on 으로 갖고, 요약 입력의 날짜 표시까지 total 에 넣는다.
    """
    first, second, latest = (dataclasses.replace(t, answered_on=day) for t in pairs(4000, 4000, 1000))
    gap = total - summary_request_tokens(None, (first, second))
    # 숫자를 더하면 그 글자 수만큼 토큰이 는다
    second = dataclasses.replace(second, answer=second.answer + "2" * gap)
    return first, second, latest


@pytest.mark.parametrize("day", [None, date(2026, 10, 28)], ids=["no_date", "dated"])
@pytest.mark.parametrize(("extra", "count"), [(0, 2), (1, 1)])
def test_plan_targets_fit_summary_input_budget(extra, count, day):
    turns = budget_pairs(budget.SUMMARY_INPUT_MAX_TOKENS + extra, day)
    assert summary_request_tokens(None, turns[:2]) == budget.SUMMARY_INPUT_MAX_TOKENS + extra
    plan = plan_summary(None, turns)
    # 1,500·2,400 규칙이 고른 앞 두 쌍이 예산을 넘으면 앞부분으로 줄인다
    assert plan.targets == turns[:count] and plan.until_seq == turns[count - 1].answer_seq
    assert plan.unsummarized_tokens == sum(t.tokens for t in turns)


def test_plan_with_backlog_targets_oldest_pairs_within_input_budget():
    # 요약이 오래 실패해 밀린 원문. 1,500 규칙은 최신 1,000 만 남기고 앞의 12쌍(12,000)을 고른다
    turns = pairs(*([1000] * 12), 1000)
    plan = plan_summary(None, turns)
    count = len(plan.targets)
    assert 1 <= count < 12 and plan.targets == turns[:count]
    limit = budget.SUMMARY_INPUT_MAX_TOKENS
    assert summary_request_tokens(None, turns[:count]) <= limit < summary_request_tokens(None, turns[: count + 1])
    assert (plan.until_seq, plan.unsummarized_tokens) == (turns[count - 1].answer_seq, 13_000)
    # 저장한 요약 뒤의 남은 쌍이 다음 요약 대상이 된다
    stored = StoredSummary(text="걸음 수를 자주 묻는다.", until_seq=plan.until_seq)
    assert plan_summary(stored, turns[count:]).targets[0] == turns[count]


def test_long_existing_summary_leaves_less_room_for_targets():
    turns = pairs(3000, 3000, 1000, start=11)
    assert plan_summary(None, turns).targets == turns[:2]
    summary = StoredSummary(text="요약 " + "1" * 4000, until_seq=10)
    plan = plan_summary(summary, turns)
    assert plan.targets == turns[:1] and plan.until_seq == 12
    assert summary_request_tokens(summary, turns[:2]) > budget.SUMMARY_INPUT_MAX_TOKENS


def test_first_target_over_input_budget_is_planned_alone():
    # 한 쌍이 예산을 넘어도 잘라 넣지 않는다. 그 쌍만 대상으로 두고 요약 생성이 input_too_large 로 끝낸다
    turns = pairs(9800, 1000)
    plan = plan_summary(None, turns)
    assert plan.targets == turns[:1]
    assert summary_request_tokens(None, plan.targets) > budget.SUMMARY_INPUT_MAX_TOKENS


@pytest.mark.parametrize(
    ("summary", "turns"),
    [
        (None, pairs(3001, start=5) + pairs(3001)),
        (StoredSummary(text="요약", until_seq=4), pairs(3001)),
        ("요약", pairs(3001, start=5)),
        (None, (ChatTurn("user", "1" * 3001),)),
    ],
)
def test_plan_rejects_history_against_context_rules(summary, turns):
    with pytest.raises(ValueError):
        plan_summary(summary, turns)


def test_generate_summary_has_agreed_shape():
    hints = get_type_hints(generate_summary)
    assert (hints["plan"], hints["return"]) == (SummaryPlan, SummaryResult)
