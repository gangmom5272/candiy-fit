"""누적 요약 생성(generate_summary) 테스트. 사내 LLM 은 모의 응답으로 대체한다."""

import json
from dataclasses import asdict, replace
from datetime import date

import httpx
import pytest
import respx
from langchain_core.language_models import BaseChatModel

from coach_ai.history.budget import SUMMARY_INPUT_MAX_TOKENS, SUMMARY_MAX_TOKENS
from coach_ai.history.summary import (
    SUMMARY_HEADINGS,
    SUMMARY_INSTRUCTION,
    generate_summary,
    summary_input,
    summary_request_tokens,
)
from coach_ai.history.tokens import MESSAGE_TOKENS, TOKEN_POLICY_VERSION, count_tokens
from coach_ai.history.types import HistoryTurn, StoredSummary, SummaryError, SummaryPlan
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder, SummaryRecord
from tests.llm_fakes import CHAT_URL


def turn(question_seq: int, question: str, answer: str) -> HistoryTurn:
    return HistoryTurn(
        request_id=f"request-{question_seq}",
        question_message_id=f"message-{question_seq}",
        answer_message_id=f"message-{question_seq + 1}",
        question_seq=question_seq,
        answer_seq=question_seq + 1,
        question=question,
        answer=answer,
    )


SUMMARY = StoredSummary(text="대화 주제:\n- 걸음 수\n사용자가 직접 밝힌 목표·선호 및 정정:\n- 없음", until_seq=2)
TARGETS = (
    turn(3, "하루 걸음 목표를 8,000보로 잡을래", "좋아요. 하루 8,000걸음을 목표로 볼게요."),
    turn(5, "아니 10,000보로 바꿀게", "알겠어요. 목표를 하루 10,000걸음으로 바꿨어요."),
)
PLAN = SummaryPlan(summary=SUMMARY, targets=TARGETS, until_seq=6, unsummarized_tokens=2500)
VALID = "\n".join(
    [
        "대화 주제:",
        "- 하루 걸음 목표",
        "사용자가 직접 밝힌 목표·선호 및 정정:",
        "- 하루 걸음 목표 10,000걸음 (8,000걸음에서 정정)",
        "주요 논의 내용:",
        "- 목표 걸음 수를 정했다",
        "아직 해결하지 못한 질문:",
        "- 없음",
    ]
)
SECRETS = ("8,000보", "10,000보", "걸음 목표")


def completion(content: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "c-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gemma4-31b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 80, "total_tokens": 680},
    }


@pytest.fixture
def recorder() -> MetricsRecorder:
    return MetricsRecorder()


@pytest.fixture
def model(settings, recorder):
    return create_chat_model(settings, recorder=recorder, retry_base_delay=0.0)


def respond(content: str, finish_reason: str = "stop"):
    return respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion(content, finish_reason)))


@respx.mock
async def test_summary_result_takes_until_seq_from_plan(model):
    # 출력에 다른 번호가 있어도 until_seq 는 plan 의 값이다
    respond(VALID + "\n- 질문 번호 99 까지 요약함")
    result = await generate_summary(PLAN, model=model)

    assert result.text == VALID + "\n- 질문 번호 99 까지 요약함"
    assert (result.until_seq, result.policy_version) == (PLAN.until_seq, TOKEN_POLICY_VERSION)
    assert result.tokens == count_tokens(result.text)


@respx.mock
async def test_summary_request_has_instruction_input_and_max_output_only(model):
    route = respond(VALID)
    await generate_summary(PLAN, model=model)

    [call] = route.calls
    body = json.loads(call.request.content)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == SUMMARY_INSTRUCTION
    assert body["messages"][1]["content"] == summary_input(SUMMARY, TARGETS)
    assert (body["max_tokens"], body["stream"]) == (SUMMARY_MAX_TOKENS, False)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    for key in ("tools", "tool_choice", "temperature", "stream_options"):
        assert key not in body


def test_summary_input_marks_existing_summary_and_turns_in_order():
    text = summary_input(SUMMARY, TARGETS)
    assert text.startswith(f"<기존 요약>\n{SUMMARY.text}\n</기존 요약>\n\n<새 대화>\n")
    assert text.index("[질문 1]") < text.index("[답변 1]") < text.index("[질문 2]") < text.index("[답변 2]")
    assert text.endswith(f"[답변 2]\n{TARGETS[1].answer}\n</새 대화>")
    assert summary_input(None, TARGETS).startswith("<기존 요약>\n없음\n</기존 요약>")


def test_summary_input_puts_answer_date_next_to_question_number_only_when_given():
    dated = (replace(TARGETS[0], answered_on=date(2026, 10, 28)), TARGETS[1])
    assert summary_input(None, dated) == "\n".join(
        [
            "<기존 요약>",
            "없음",
            "</기존 요약>",
            "",
            "<새 대화>",
            "[질문 1] 2026-10-28 (수)",
            "하루 걸음 목표를 8,000보로 잡을래",
            "[답변 1]",
            "좋아요. 하루 8,000걸음을 목표로 볼게요.",
            "[질문 2]",
            "아니 10,000보로 바꿀게",
            "[답변 2]",
            "알겠어요. 목표를 하루 10,000걸음으로 바꿨어요.",
            "</새 대화>",
        ]
    )
    # 날짜가 없는 쌍만 있으면 날짜를 넣기 전과 같은 형식이다
    plain = summary_input(None, TARGETS).splitlines()
    assert [line for line in plain if line.startswith("[")] == ["[질문 1]", "[답변 1]", "[질문 2]", "[답변 2]"]


def test_summary_input_marks_each_pair_with_its_own_date():
    days = (date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28))
    targets = tuple(
        replace(turn(seq, "어제 몇 시간 잤어?", "6시간 10분이에요."), answered_on=day)
        for seq, day in zip((3, 5, 7), days, strict=True)
    )
    lines = summary_input(SUMMARY, targets).splitlines()
    assert [line for line in lines if line.startswith("[")] == [
        "[질문 1] 2026-10-26 (월)", "[답변 1]", "[질문 2] 2026-10-27 (화)", "[답변 2]",
        "[질문 3] 2026-10-28 (수)", "[답변 3]",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("day", "weekday"),
    [(date(2026, 10, 26 + i), "월화수목금토"[i]) for i in range(6)] + [(date(2026, 11, 1), "일")],
)
def test_question_label_weekday_follows_date(day, weekday):
    text = summary_input(None, (replace(TARGETS[0], answered_on=day),))
    assert f"[질문 1] {day.isoformat()} ({weekday})\n" in text


def test_summary_instruction_turns_relative_dates_into_dates_of_each_pair():
    lines = SUMMARY_INSTRUCTION.splitlines()
    rule = (
        "- 어제, 오늘, 지난주, 지난 7일 같은 말은 그 쌍의 날짜를 기준으로 실제 날짜나 기간으로 바꿔 쓴다. "
        "날짜가 없는 쌍은 상대 날짜를 그대로 둔다."
    )
    assert rule in lines
    assert lines[lines.index(rule) - 1].startswith("- 건강 수치는 이어질 대화에 필요한 것만")


def test_summary_request_counts_answer_date_labels():
    dated = tuple(replace(t, answered_on=date(2026, 10, 28)) for t in TARGETS)
    expected = 2 * MESSAGE_TOKENS + count_tokens(SUMMARY_INSTRUCTION) + count_tokens(summary_input(SUMMARY, dated))
    assert summary_request_tokens(SUMMARY, dated) == expected > summary_request_tokens(SUMMARY, TARGETS)


@respx.mock
async def test_summary_request_sends_answer_dates_in_input(model):
    route = respond(VALID)
    dated = tuple(replace(t, answered_on=date(2026, 10, 27 + i)) for i, t in enumerate(TARGETS))
    await generate_summary(replace(PLAN, targets=dated), model=model)

    body = json.loads(route.calls[0].request.content)
    assert body["messages"][1]["content"] == summary_input(SUMMARY, dated)
    assert "[질문 1] 2026-10-27 (화)" in body["messages"][1]["content"]
    assert "[질문 2] 2026-10-28 (수)" in body["messages"][1]["content"]


def test_summary_instruction_lists_four_headings_last():
    assert SUMMARY_INSTRUCTION.endswith("\n".join(SUMMARY_HEADINGS))
    assert SUMMARY_HEADINGS == (
        "대화 주제:",
        "사용자가 직접 밝힌 목표·선호 및 정정:",
        "주요 논의 내용:",
        "아직 해결하지 못한 질문:",
    )


@pytest.mark.parametrize(
    "content",
    [
        "thought\n" + VALID,
        "<|channel>thought\n<channel|>" + VALID,
        VALID + '<|tool_call>call:get_metric_summary{metric:<|"|>steps<|"|>}<tool_call|>',
    ],
)
@respx.mock
async def test_thought_and_tool_call_text_are_dropped(model, recorder, content):
    respond(content)
    result = await generate_summary(PLAN, model=model)
    assert result.text == VALID


@respx.mock
async def test_output_cut_at_max_tokens_is_truncated(model):
    respond(VALID, finish_reason="length")
    with pytest.raises(SummaryError) as caught:
        await generate_summary(PLAN, model=model)
    assert (caught.value.code, caught.value.retryable) == ("truncated", False)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "  \n ",
        VALID.replace("주요 논의 내용:", "논의:"),
        "\n".join(VALID.splitlines()[2:4] + VALID.splitlines()[:2] + VALID.splitlines()[4:]),
        "요약입니다.\n" + VALID,
        VALID.replace("대화 주제:", "**대화 주제:**"),
    ],
    ids=["empty", "blank", "missing", "order", "preamble", "bold"],
)
@respx.mock
async def test_headings_missing_or_out_of_order_are_invalid_format(model, content):
    respond(content)
    with pytest.raises(SummaryError) as caught:
        await generate_summary(PLAN, model=model)
    assert (caught.value.code, caught.value.retryable) == ("invalid_format", False)


@pytest.mark.parametrize(("extra", "ok"), [(0, True), (1, False)])
@respx.mock
async def test_summary_over_800_tokens_is_invalid_format(model, extra, ok):
    # 숫자는 한 글자가 1토큰이라 마지막 항목 길이로 토큰 수를 맞춘다
    filler = "1" * (SUMMARY_MAX_TOKENS - count_tokens(VALID + "\n- ") + extra)
    text = VALID + "\n- " + filler
    assert count_tokens(text) == SUMMARY_MAX_TOKENS + extra
    respond(text)
    if ok:
        assert (await generate_summary(PLAN, model=model)).tokens == SUMMARY_MAX_TOKENS
    else:
        with pytest.raises(SummaryError) as caught:
            await generate_summary(PLAN, model=model)
        assert caught.value.code == "invalid_format"


def error(status: int, message: str = "error") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message}})


@pytest.mark.parametrize(
    ("side_effect", "code", "retryable"),
    [
        (error(503), "llm_unavailable", True),
        (httpx.ConnectError("refused"), "llm_unavailable", True),
        (error(429), "rate_limited", True),
        (httpx.ReadTimeout("slow"), "timeout", True),
        (error(401), "config", False),
        (error(404, "model not found"), "config", False),
        (error(400, "maximum context length is 16384 tokens"), "input_too_large", False),
        (error(400, "bad request"), "config", False),
    ],
    ids=["5xx", "connect", "429", "timeout", "401", "404", "context", "400"],
)
@respx.mock
async def test_llm_errors_map_to_summary_error_codes(model, side_effect, code, retryable):
    respx.post(CHAT_URL).mock(side_effect=[side_effect] * 3)
    with pytest.raises(SummaryError) as caught:
        await generate_summary(PLAN, model=model)
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    # 예외 문구는 코드뿐이고 원인 예외는 이어 둔다
    assert str(caught.value) == code and caught.value.__cause__ is not None


@respx.mock
async def test_summary_failure_sends_one_request_marked_as_summary(model, recorder):
    route = respx.post(CHAT_URL).mock(return_value=error(503))
    with pytest.raises(SummaryError) as caught:
        await generate_summary(PLAN, model=model)

    # 다시 시도할지는 대화 API 의 작업 관리가 정한다
    assert (caught.value.code, caught.value.retryable, route.call_count) == ("llm_unavailable", True, 1)
    [rec] = recorder.records("llm")
    assert (rec.call_kind, rec.attempts, rec.node) == ("summary", 1, "summary")


@respx.mock
async def test_input_over_summary_budget_ends_without_llm_call(model):
    big = (turn(3, "1" * SUMMARY_INPUT_MAX_TOKENS, ""),)
    plan = SummaryPlan(summary=None, targets=big, until_seq=4, unsummarized_tokens=SUMMARY_INPUT_MAX_TOKENS)
    assert summary_request_tokens(None, big) > SUMMARY_INPUT_MAX_TOKENS
    route = respond(VALID)
    with pytest.raises(SummaryError) as caught:
        await generate_summary(plan, model=model)
    assert (caught.value.code, caught.value.retryable, route.call_count) == ("input_too_large", False, 0)


class BrokenModel(BaseChatModel):
    """LLM 호출과 관계없는 오류를 발생시키는 모델."""

    @property
    def _llm_type(self) -> str:
        return "broken"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise KeyError("코드 오류")


async def test_errors_unrelated_to_llm_call_are_not_wrapped():
    with pytest.raises(KeyError):
        await generate_summary(PLAN, model=BrokenModel())


@respx.mock
async def test_records_have_kind_tokens_and_code_but_no_text(model, recorder):
    respond(VALID)
    result = await generate_summary(PLAN, model=model)
    respond("요약을 쓰지 않았어요")
    with pytest.raises(SummaryError):
        await generate_summary(PLAN, model=model)

    ok, bad = recorder.records("summary")
    assert isinstance(ok, SummaryRecord) and (ok.code, bad.code) == ("ok", "invalid_format")
    assert ok.input_tokens == bad.input_tokens == summary_request_tokens(SUMMARY, TARGETS)
    assert (ok.output_tokens, bad.output_tokens) == (result.tokens, None)
    assert [r.node for r in recorder.records("llm")] == ["summary", "summary"]
    saved = json.dumps([asdict(r) for r in recorder.records()], ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in saved
