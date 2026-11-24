"""사내 LLM 응답 원문(SSE)을 래퍼와 에이전트에 재생한다.

data/llm_replay/ 의 파일은 원문 캡처(ai/eval/results/*_llm_raw_capture.json, git 제외)에 기록된 마지막 호출의
응답 본문(raw_text)을 UTF-8 로 저장한 것이다. 두 호출 모두 도구 왕복 한도에 걸려 도구 없이 보낸 요청이다.
69e434c6 이 도구 호출 표기를 보낸 기록은 없어, 그 형태(as_69e434c6)는 두 원문을 합쳐 만든 가정 입력이다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.graph import run_coach
from coach_ai.agent.nodes.model import TOOL_LIMIT_ANSWER
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.fakes import FakeHealthData
from tests.llm_fakes import CHAT_URL, tool_call_response

DATA = Path(__file__).parent / "data" / "llm_replay"
# (파일, 바이트 수). 바이트 수는 캡처에 기록된 응답 조각 크기(read_bytes)의 합과 같다
# 69e434c6: 표시 없는 thought 줄과 답변이 content 이벤트 1개로 온다
THOUGHT_LINE = ("20261028-124123-69e434c6.sse", 1536)
# 5e590392: 사고 채널 표시 뒤에 도구 호출 표기가 토큰마다 content 이벤트로 온다
TOOL_CALL_TEXT = ("20261028-171300-5e590392.sse", 6804)

NOW = datetime(2026, 10, 28, 8, 13, tzinfo=UTC)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")


@pytest.fixture
def recorder() -> MetricsRecorder:
    return MetricsRecorder()


@pytest.fixture
def model(settings, recorder):
    return create_chat_model(settings, recorder=recorder, retry_base_delay=0.0)


SSE_HEADERS = {"content-type": "text/event-stream; charset=utf-8"}
# 69e434c6 이 사고 채널 표시처럼 지울 것으로 가정한 표시
MARKERS = ("<|channel>", "<channel|>", "<|tool_call>", "<tool_call|>", '<|"|>')


def replay(name: str) -> httpx.Response:
    return httpx.Response(200, content=(DATA / name).read_bytes(), headers=SSE_HEADERS)


def sse_contents(name: str) -> list[str]:
    """래퍼를 거치지 않고 응답 원문에서 읽은 content 조각. 빈 조각은 뺀다."""
    contents = []
    for line in (DATA / name).read_text(encoding="utf-8").splitlines():
        if line.startswith("data: {"):
            choices = json.loads(line.removeprefix("data: "))["choices"]
            contents += [c["delta"]["content"] for c in choices if c["delta"].get("content")]
    return contents


def unmarked_contents() -> list[str]:
    """5e590392 원문의 content 조각에서 표시를 지운 것. 지운 뒤 빈 조각은 뺀다."""
    parts = []
    for content in sse_contents(TOOL_CALL_TEXT[0]):
        for marker in MARKERS:
            content = content.replace(marker, "")
        if content:
            parts.append(content)
    return parts


def as_69e434c6(*parts: str) -> httpx.Response:
    """69e434c6 원문의 content 이벤트 하나를 parts 조각 수만큼의 content 이벤트로 바꾼 응답."""
    role, content, *rest = (DATA / THOUGHT_LINE[0]).read_text(encoding="utf-8").split("\n\n")
    event = json.loads(content.removeprefix("data: "))
    events = [role]
    for part in parts:
        event["choices"][0]["delta"]["content"] = part
        events.append("data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    return httpx.Response(200, content="\n\n".join([*events, *rest]).encode(), headers=SSE_HEADERS)


async def run_tool_limit(model, last: httpx.Response):
    """도구 왕복 3회 뒤 도구 없이 보내는 한도 호출에 last 를 응답으로 재생한다."""
    periods = ("last_7_days", "last_14_days", "last_30_days")
    calls = [("get_metric_summary", {"metric": "oxygen_saturation", "period": p}) for p in periods]
    route = respx.post(CHAT_URL).mock(side_effect=[*(tool_call_response(c) for c in calls), last])
    sink = ListSink()
    question = "지난주 산소포화도 그 전 주보다 낮아졌어?"
    result = await run_coach(CTX, question, port=FakeHealthData(), model=model, sink=sink, now=NOW)
    assert route.call_count == 4 and "tools" not in json.loads(route.calls.last.request.content)
    return result, sink.events


@pytest.mark.parametrize(("name", "size"), [THOUGHT_LINE, TOOL_CALL_TEXT])
def test_replay_files_are_captured_bodies(name, size):
    raw = (DATA / name).read_bytes()
    assert len(raw) == size
    assert b"\r" not in raw and raw.endswith(b"data: [DONE]\n\n")


@respx.mock
async def test_thought_line_response_keeps_answer_in_one_chunk(model, recorder):
    name, _ = THOUGHT_LINE
    [content] = sse_contents(name)
    assert content.startswith("thought\n")
    respx.post(CHAT_URL).mock(return_value=replay(name))
    chunks = [c.content async for c in model.astream("질문")]

    assert [c for c in chunks if c] == [content.removeprefix("thought\n")]
    [rec] = recorder.records("llm")
    assert (rec.thought_chars, rec.tool_call_text_chars) == (8, 0)


@respx.mock
async def test_tool_call_text_response_leaves_no_answer(model, recorder):
    name, _ = TOOL_CALL_TEXT
    contents = sse_contents(name)
    # 표기는 사고 채널 표시 바로 뒤에 여는 표시로 시작하고 닫는 표시로 끝난다. 그 뒤에는 글이 없다
    assert contents[:5] == ["<|channel>", "thought", "\n", "<channel|>", "<|tool_call>"]
    assert contents[-1] == "<tool_call|>"
    respx.post(CHAT_URL).mock(return_value=replay(name))
    chunks = [c.content async for c in model.astream("질문")]

    assert "".join(chunks) == ""
    [rec] = recorder.records("llm")
    assert (rec.thought_chars, rec.tool_call_text_chars) == (8, len("".join(contents[4:]))) == (8, 113)
    # 표기를 첫 글자로 세지 않는다
    assert rec.ttft_s is None


@respx.mock
async def test_thought_line_response_in_tool_limit_path(model):
    name, _ = THOUGHT_LINE
    result, events = await run_tool_limit(model, replay(name))

    answer = sse_contents(name)[0].removeprefix("thought\n")
    assert [e["delta"] for e in events if e["type"] == "token"] == [answer]
    assert result.answer == answer and result.finish_reason == "tool_limit"
    assert (events[-1]["type"], events[-1]["finish_reason"]) == ("done", "stop")


@respx.mock
async def test_tool_call_text_response_in_tool_limit_path_ends_with_tool_limit_answer(model):
    name, _ = TOOL_CALL_TEXT
    result, events = await run_tool_limit(model, replay(name))

    assert [e["delta"] for e in events if e["type"] == "token"] == [TOOL_LIMIT_ANSWER]
    assert result.answer == result.messages[-1].content == TOOL_LIMIT_ANSWER
    assert result.finish_reason == "tool_limit"
    assert [e["type"] for e in events if e["type"] in ("done", "error")] == ["done"]
    assert events[-1]["finish_reason"] == "stop"


def test_unmarked_input_keeps_69e434c6_events():
    parts = unmarked_contents()
    assert "".join(parts) == "thought\ncall:get_health_metrics{metric:oxygen_saturation,period:last_14_days}"
    raw = as_69e434c6(*parts).content.decode()
    # 원문과 같이 role 이벤트로 시작하고 system_fingerprint 가 든 종료·usage 이벤트와 [DONE] 으로 끝난다
    assert raw.count("data: ") == len(parts) + 4 and raw.endswith("data: [DONE]\n\n")
    assert raw.count('"system_fingerprint":"vllm-0.24.0-50bccfa0"') == 2


@pytest.mark.parametrize("split", [False, True])
@respx.mock
async def test_unmarked_tool_call_in_tool_limit_path_ends_with_tool_limit_answer(model, recorder, split):
    parts = unmarked_contents()
    result, events = await run_tool_limit(model, as_69e434c6(*(parts if split else ["".join(parts)])))

    assert [e["delta"] for e in events if e["type"] == "token"] == [TOOL_LIMIT_ANSWER]
    assert result.answer == result.messages[-1].content == TOOL_LIMIT_ANSWER
    assert (result.finish_reason, events[-1]["type"], events[-1]["finish_reason"]) == ("tool_limit", "done", "stop")
    last = recorder.records("llm")[-1]
    assert (last.thought_chars, last.tool_call_text_chars) == (8, len("".join(parts)) - 8)


@pytest.mark.parametrize(
    "parts", [["thought\ncall 센터에 문의해 보세요."], ["thought", "\n", "call", " 센터에 문의해 보세요."]]
)
@respx.mock
async def test_answer_starting_with_call_in_tool_limit_path_is_kept(model, recorder, parts):
    result, events = await run_tool_limit(model, as_69e434c6(*parts))

    assert "".join(e["delta"] for e in events if e["type"] == "token") == "call 센터에 문의해 보세요."
    assert result.answer == "call 센터에 문의해 보세요." and result.finish_reason == "tool_limit"
    assert recorder.records("llm")[-1].tool_call_text_chars == 0


@respx.mock
async def test_unmarked_tool_call_outside_tool_limit_path_is_kept(model, recorder):
    # 도구를 묶은 첫 호출의 답변은 한도에 걸리지 않은 호출이라 표기로 보지 않는다
    respx.post(CHAT_URL).mock(return_value=as_69e434c6("".join(unmarked_contents())))
    question = "지난주 산소포화도 그 전 주보다 낮아졌어?"
    result = await run_coach(CTX, question, port=FakeHealthData(), model=model, sink=ListSink(), now=NOW)

    answer = "call:get_health_metrics{metric:oxygen_saturation,period:last_14_days}"
    assert (result.answer, result.finish_reason, result.tool_rounds) == (answer, "stop", 0)
    assert recorder.records("llm")[-1].tool_call_text_chars == 0
