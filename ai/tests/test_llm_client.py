import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
import respx

from coach_ai.llm.client import CallKind, ConcurrencyLimiter, LLMContextLengthError, LLMError, create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from tests.llm_fakes import (
    CHAT_URL,
    SSE_HEADERS,
    USAGE,
    ScriptedStream,
    chunk,
    completion,
    sse_event,
    text_response,
    text_stream_response,
)


@pytest.fixture
def recorder() -> MetricsRecorder:
    return MetricsRecorder()


def model(settings, recorder, **kw):
    # 테스트가 백오프만큼 기다리지 않게 지연을 0으로 둔다
    return create_chat_model(settings, recorder=recorder, retry_base_delay=0.0, **kw)


class InFlight:
    """모의 서버에 동시에 열려 있는 요청 수를 센다."""

    def __init__(self) -> None:
        self.now = 0
        self.peak = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(0.02)
            return httpx.Response(200, json=completion())
        finally:
            self.now -= 1


class HeldServer:
    """release 가 설정될 때까지 응답하지 않는 모의 서버. 동시에 열려 있는 요청 수의 최댓값을 센다."""

    def __init__(self, progress: asyncio.Event) -> None:
        self.progress = progress
        self.release = asyncio.Event()
        self.arrived = 0
        self.now = 0
        self.peak = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.arrived += 1
        self.now += 1
        self.peak = max(self.peak, self.now)
        self.progress.set()
        try:
            await self.release.wait()
            return httpx.Response(200, json=completion())
        finally:
            self.now -= 1


class SlotCounter(ConcurrencyLimiter):
    """슬롯을 요청한 호출 수와 슬롯을 얻은 호출 수를 센다. 요약 호출이 얻은 슬롯은 따로 센다."""

    def __init__(self, progress: asyncio.Event) -> None:
        super().__init__()
        self.progress = progress
        self.requested = 0
        self.acquired = 0
        self.summary_acquired = 0

    @asynccontextmanager
    async def acquire(self, limit: int, *, kind: CallKind) -> AsyncIterator[None]:
        self.requested += 1
        self.progress.set()
        async with super().acquire(limit, kind=kind):
            self.acquired += 1
            if kind == "summary":
                self.summary_acquired += 1
            self.progress.set()
            yield


async def settle(calls: asyncio.Future, progress: asyncio.Event, ready: Callable[[], bool]) -> None:
    """ready 가 참이 될 때까지 progress 신호마다 다시 확인한다.

    HeldServer 는 release 전까지 응답하지 않으므로 그 전에 calls 가 끝나는 경우는 호출 실패다. 이때 그 예외를 올린다.
    """
    calls.add_done_callback(lambda _: progress.set())
    while not calls.done() and not ready():
        progress.clear()
        await progress.wait()
    if calls.done():
        await calls


@respx.mock
async def test_request_body_defaults(settings, recorder):
    route = respx.post(CHAT_URL).mock(return_value=text_stream_response())
    chunks = [c async for c in model(settings, recorder).astream("잠을 잘 자는 법")]

    assert "".join(c.content for c in chunks) == "잠은 규칙적으로."
    body = json.loads(route.calls.last.request.content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] == 1000
    assert "max_completion_tokens" not in body
    assert body["stream_options"] == {"include_usage": True}
    assert body["model"] == "gemma4-31b"


@respx.mock
async def test_extra_body_override_keeps_thinking_off(settings, recorder):
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion()))
    m = model(settings, recorder, extra_body={"top_k": 20}, max_tokens=300)
    await m.ainvoke("질문")
    body = json.loads(route.calls.last.request.content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["top_k"] == 20
    assert body["max_tokens"] == 300


@respx.mock
async def test_concurrency_capped_by_setting(settings, recorder):
    settings.llm_max_concurrency = 3
    progress = asyncio.Event()
    server, slots = HeldServer(progress), SlotCounter(progress)
    respx.post(CHAT_URL).mock(side_effect=server)
    m = model(settings, recorder, limiter=slots)

    calls = asyncio.gather(*(m.ainvoke(f"질문 {i}") for i in range(5)))
    # 다섯 호출이 모두 슬롯을 요청하고 슬롯을 얻은 호출이 모두 서버에 도착하면 release 전에는 더 진행되지 않는다
    await settle(calls, progress, lambda: slots.requested == 5 and server.arrived == slots.acquired)

    assert (server.now, slots.acquired) == (3, 3)  # 한도를 넘은 2건은 슬롯을 기다린다
    server.release.set()
    await calls

    assert server.peak == 3
    assert server.arrived == 5


@respx.mock
async def test_summary_calls_use_at_most_one_slot(settings, recorder):
    settings.llm_max_concurrency = 3
    server = InFlight()
    respx.post(CHAT_URL).mock(side_effect=server)
    summary = model(settings, recorder).bind(call_kind="summary")

    await asyncio.gather(*(summary.ainvoke(f"요약 {i}") for i in range(3)))

    assert server.peak == 1


@respx.mock
async def test_summary_leaves_slots_for_answers(settings, recorder):
    settings.llm_max_concurrency = 3
    progress = asyncio.Event()
    server, slots = HeldServer(progress), SlotCounter(progress)
    respx.post(CHAT_URL).mock(side_effect=server)
    answer = model(settings, recorder, limiter=slots)
    summary = answer.bind(call_kind="summary")

    # 요약 호출이 얻을 수 있는 슬롯을 모두 얻은 뒤에 답변 호출을 시작한다. 이 순서에서도 답변 슬롯이 남아야 한다
    summary_calls = asyncio.gather(*(summary.ainvoke("요약") for _ in range(3)))
    await settle(summary_calls, progress, lambda: slots.requested == 3 and server.arrived == slots.acquired)
    calls = asyncio.gather(summary_calls, *(answer.ainvoke("질문") for _ in range(5)))
    await settle(calls, progress, lambda: slots.requested == 8 and server.arrived == slots.acquired)

    assert (server.now, slots.summary_acquired) == (3, 1)  # 요약은 슬롯 1개만 쓰고 2개는 답변 호출이 쓴다
    server.release.set()
    await calls

    assert server.peak == 3
    assert server.arrived == 8


async def use_slot(limiter: ConcurrencyLimiter, kind: CallKind, name: str, order: list[str], hold=None) -> None:
    async with limiter.acquire(1, kind=kind):
        order.append(name)
        if hold is not None:
            await hold.wait()


async def test_waiting_answer_gets_free_slot_before_waiting_summary():
    limiter, order, hold = ConcurrencyLimiter(), [], asyncio.Event()
    first = asyncio.create_task(use_slot(limiter, "answer", "first", order, hold))
    await asyncio.sleep(0.01)
    # 요약이 먼저 기다리기 시작해도 나중에 온 답변이 비는 슬롯을 먼저 얻는다
    summary = asyncio.create_task(use_slot(limiter, "summary", "summary", order))
    await asyncio.sleep(0.01)
    answers = [asyncio.create_task(use_slot(limiter, "answer", f"answer {i}", order)) for i in range(2)]
    await asyncio.sleep(0.01)
    assert order == ["first"]
    hold.set()
    await asyncio.gather(first, summary, *answers)

    assert order == ["first", "answer 0", "answer 1", "summary"]


async def test_cancelled_waiter_does_not_keep_slot():
    limiter, order, hold = ConcurrencyLimiter(), [], asyncio.Event()
    first = asyncio.create_task(use_slot(limiter, "answer", "first", order, hold))
    await asyncio.sleep(0.01)
    gone = asyncio.create_task(use_slot(limiter, "answer", "gone", order))
    summary = asyncio.create_task(use_slot(limiter, "summary", "summary", order))
    await asyncio.sleep(0.01)
    gone.cancel()
    hold.set()
    await asyncio.gather(first, summary)
    # 모든 호출이 끝나면 슬롯이 다시 비어 바로 얻는다
    await asyncio.wait_for(use_slot(limiter, "answer", "after", order), timeout=0.5)

    assert order == ["first", "summary", "after"] and gone.cancelled()


@respx.mock
async def test_running_summary_is_not_interrupted_by_answer(settings, recorder):
    settings.llm_max_concurrency = 1
    progress = asyncio.Event()
    server = HeldServer(progress)
    respx.post(CHAT_URL).mock(side_effect=server)
    m = model(settings, recorder)

    summary = asyncio.ensure_future(m.bind(call_kind="summary").ainvoke("요약"))
    await settle(summary, progress, lambda: server.arrived == 1)
    answer = asyncio.ensure_future(m.ainvoke("질문"))
    await asyncio.sleep(0.01)
    # 실행 중인 요약은 슬롯을 내주지 않고, 답변은 요약이 끝날 때까지 기다린다
    assert (server.arrived, summary.done(), answer.done()) == (1, False, False)
    server.release.set()
    assert (await summary).content == "확인" and (await answer).content == "확인"
    assert server.peak == 1


@respx.mock
async def test_summary_call_is_sent_once_and_answer_retries_as_before(settings, recorder):
    assert settings.llm_max_retries == 2
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(503, json={"error": {"message": "down"}}))
    m = model(settings, recorder)

    with pytest.raises(LLMError):
        await m.bind(call_kind="summary").ainvoke("요약")
    assert route.call_count == 1
    with pytest.raises(LLMError):
        await m.ainvoke("질문")
    assert route.call_count == 1 + 3
    summary_record, answer_record = recorder.records("llm")
    assert (summary_record.call_kind, summary_record.attempts) == ("summary", 1)
    assert (answer_record.call_kind, answer_record.attempts) == ("answer", 3)


@respx.mock
async def test_call_kind_is_not_sent_in_request_body(settings, recorder):
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion()))
    await model(settings, recorder).bind(call_kind="summary").ainvoke("요약")
    assert "call_kind" not in json.loads(route.calls.last.request.content)


def test_sdk_retries_stay_off(settings, recorder):
    # 재시도는 래퍼에서만 한다. 요약 호출은 래퍼도 다시 보내지 않으므로 SDK 안에서 다시 보내면 안 된다
    assert model(settings, recorder).max_retries == 0
    with pytest.raises(ValueError):
        model(settings, recorder, max_retries=2)


async def test_unknown_call_kind_is_rejected(settings, recorder):
    with pytest.raises(ValueError):
        await model(settings, recorder).bind(call_kind="batch").ainvoke("질문")


async def test_sync_call_is_blocked(settings, recorder):
    with pytest.raises(NotImplementedError):
        model(settings, recorder).invoke("질문")


@respx.mock
async def test_retry_after_429_then_success(settings, recorder):
    route = respx.post(CHAT_URL).mock(
        side_effect=[httpx.Response(429, json={"error": {"message": "busy"}}), text_stream_response()]
    )
    chunks = [c async for c in model(settings, recorder).astream("질문")]

    assert route.call_count == 2
    assert "".join(c.content for c in chunks) == "잠은 규칙적으로."
    assert recorder.records("llm")[0].attempts == 2


@respx.mock
async def test_retry_on_5xx_and_connect_error_non_stream(settings, recorder):
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(503, json={"error": {"message": "down"}}),
            httpx.ConnectError("refused"),
            httpx.Response(200, json=completion("완료")),
        ]
    )
    result = await model(settings, recorder).ainvoke("질문")
    assert result.content == "완료"
    assert route.call_count == 3


@respx.mock
async def test_retry_gives_up_after_limit(settings, recorder):
    settings.llm_max_retries = 2
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "busy"}}))
    with pytest.raises(LLMError) as exc:
        await model(settings, recorder).ainvoke("질문")
    assert exc.value.code == "rate_limited"
    assert exc.value.retryable is True
    assert route.call_count == 3


@respx.mock
async def test_no_retry_after_stream_started(settings, recorder):
    body = ScriptedStream(
        [
            sse_event(chunk({"role": "assistant", "content": ""})),
            sse_event(chunk({"content": "잠은 "})),
            httpx.RemoteProtocolError("peer closed connection"),
        ]
    )
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, stream=body, headers=SSE_HEADERS))

    received = []
    with pytest.raises(LLMError) as exc:
        async for c in model(settings, recorder).astream("질문"):
            received.append(c.content)

    assert route.call_count == 1
    assert "잠은 " in received
    assert exc.value.code == "llm_unavailable"
    assert exc.value.stream_started is True
    assert recorder.records("llm")[0].error_code == "llm_unavailable"


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(429, json={"error": {"message": "busy"}}), "rate_limited", True),
        (httpx.Response(500, json={"error": {"message": "boom"}}), "llm_unavailable", True),
        (httpx.Response(401, json={"error": {"message": "bad key"}}), "llm_unavailable", False),
        (httpx.Response(404, json={"error": {"message": "no model"}}), "llm_unavailable", False),
        (httpx.Response(400, json={"error": {"message": "bad tool schema"}}), "invalid_request", False),
        (httpx.Response(422, json={"error": {"message": "bad field"}}), "invalid_request", False),
        (httpx.ConnectError("refused"), "llm_unavailable", True),
        (httpx.ReadTimeout("slow"), "llm_timeout", True),
    ],
)
@respx.mock
async def test_error_codes(settings, recorder, response, code, retryable):
    settings.llm_max_retries = 0
    respx.post(CHAT_URL).mock(side_effect=[response])
    with pytest.raises(LLMError) as exc:
        await model(settings, recorder).ainvoke("질문")
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    assert exc.value.to_ws() == {"code": code, "message": exc.value.message, "retryable": retryable}
    assert "bad key" not in exc.value.message


@respx.mock
async def test_context_length_is_distinguished(settings, recorder):
    message = (
        "This model's maximum context length is 16384 tokens. However, you requested 17000 tokens "
        "(16000 in the messages, 1000 in the completion)."
    )
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(400, json={"object": "error", "message": message, "code": 400})
    )
    with pytest.raises(LLMContextLengthError) as exc:
        await model(settings, recorder).ainvoke("질문")
    assert exc.value.code == "invalid_request"
    assert exc.value.retryable is False
    assert exc.value.reason == "context_length_exceeded"
    assert route.call_count == 1


@respx.mock
async def test_chunk_timeout_maps_to_llm_timeout(settings, recorder):
    settings.llm_chunk_timeout = 0.05
    body = ScriptedStream([sse_event(chunk({"role": "assistant", "content": ""})), 0.5])
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, stream=body, headers=SSE_HEADERS))
    with pytest.raises(LLMError) as exc:
        async for _ in model(settings, recorder).astream("질문"):
            pass
    assert exc.value.code == "llm_timeout"


async def test_unrelated_errors_are_not_wrapped(settings, recorder):
    m = model(settings, recorder)
    with pytest.raises(ValueError):
        await m.ainvoke(object())


@pytest.mark.parametrize(
    ("parts", "thought_chars"),
    [
        # 표시가 한 청크로 올 때
        (["<|channel>thought\n<channel|>", "평균 ", "7,412걸음이에요."], 8),
        # 실제 실행처럼 표시가 여러 청크로 쪼개져 올 때
        (["<|channel>", "thought", "\n", "<channel|>", "평균 ", "7,412걸음이에요."], 8),
        (["<|chan", "nel>thou", "ght\n<chan", "nel|>평균 ", "7,412걸음이에요."], 8),
        # 표시 사이에 내용이 있을 때
        (["<|channel>thought\n", "걸음 수를 정리한다.", "<channel|>평균 7,412걸음이에요."], 19),
        # 표시 없이 머리글 줄과 답변이 한 청크로 올 때. 일부 서버가 도구 없는 요청에 보내는 형태다
        (["thought\n평균 7,412걸음이에요."], 8),
        # thought 와 줄바꿈이 다른 청크로 오거나 앞에 공백이 있을 때
        (["thought", "\n", "평균 ", "7,412걸음이에요."], 8),
        (["", "thou", "ght", "\n평균 7,412걸음이에요."], 8),
        ([" \n", "thought\n", "평균 7,412걸음이에요."], 8),
        # 표시가 없을 때
        (["평균 ", "7,412걸음이에요."], 0),
    ],
)
@respx.mock
async def test_stream_drops_thought_channel(settings, recorder, parts, thought_chars):
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    chunks = [c async for c in model(settings, recorder).astream("질문")]

    text = "".join(c.content for c in chunks)
    assert text == "평균 7,412걸음이에요."
    assert recorder.records("llm")[0].thought_chars == thought_chars


@respx.mock
async def test_stream_keeps_text_that_only_looks_like_marker_start(settings, recorder):
    respx.post(CHAT_URL).mock(return_value=text_response("<", "3 응원해요"))
    chunks = [c async for c in model(settings, recorder).astream("질문")]
    assert "".join(c.content for c in chunks) == "<3 응원해요"


@respx.mock
async def test_stream_partial_marker_at_end_is_kept(settings, recorder):
    respx.post(CHAT_URL).mock(return_value=text_response("<|chan"))
    chunks = [c async for c in model(settings, recorder).astream("질문")]
    assert "".join(c.content for c in chunks) == "<|chan"
    assert recorder.records("llm")[0].thought_chars == 0


@respx.mock
async def test_stream_unclosed_thought_is_dropped(settings, recorder):
    respx.post(CHAT_URL).mock(return_value=text_response("<|channel>thought\n", "생각만 하다 끝남"))
    chunks = [c async for c in model(settings, recorder).astream("질문")]
    assert "".join(c.content for c in chunks) == ""
    assert recorder.records("llm")[0].thought_chars == len("thought\n생각만 하다 끝남")


@respx.mock
async def test_stream_marker_in_middle_is_left_alone(settings, recorder):
    respx.post(CHAT_URL).mock(return_value=text_response("표시 예시: ", "<|channel>"))
    chunks = [c async for c in model(settings, recorder).astream("질문")]
    assert "".join(c.content for c in chunks) == "표시 예시: <|channel>"
    assert recorder.records("llm")[0].thought_chars == 0


@pytest.mark.parametrize(
    "parts",
    [
        # thought 로 시작하지만 그 줄이 thought 하나가 아닐 때
        ["thoughtful ", "답변이에요."],
        ["thought", "ful 답변이에요."],
        ["thought 정리\n", "답변이에요."],
        # 답변 중간에 thought 줄이 있을 때
        ["평균 7,412걸음이에요.\n", "thought\n", "끝."],
        # thought 뒤에 줄바꿈 없이 응답이 끝날 때
        ["thought"],
    ],
)
@respx.mock
async def test_stream_keeps_thought_word_that_is_not_head_line(settings, recorder, parts):
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    chunks = [c async for c in model(settings, recorder).astream("질문")]
    assert "".join(c.content for c in chunks) == "".join(parts)
    assert recorder.records("llm")[0].thought_chars == 0


@pytest.mark.parametrize(
    ("content", "body", "thought_chars"),
    [
        ("<|channel>thought\n<channel|>평균 7,412걸음이에요.", "평균 7,412걸음이에요.", 8),
        ("<|channel>thought\n정리<channel|>평균 7,412걸음이에요.", "평균 7,412걸음이에요.", 10),
        ("thought\n평균 7,412걸음이에요.", "평균 7,412걸음이에요.", 8),
        ("thoughtful 답변이에요.", "thoughtful 답변이에요.", 0),
        ("평균 7,412걸음이에요.", "평균 7,412걸음이에요.", 0),
    ],
)
@respx.mock
async def test_non_stream_drops_thought_channel(settings, recorder, content, body, thought_chars):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion(content)))
    result = await model(settings, recorder).ainvoke("질문")
    assert result.content == body
    assert result.text == body
    assert recorder.records("llm")[0].thought_chars == thought_chars


@respx.mock
async def test_ttft_counts_body_not_marker(settings, recorder):
    body = ScriptedStream(
        [
            sse_event(chunk({"role": "assistant", "content": ""})),
            sse_event(chunk({"content": "<|channel>"})),
            sse_event(chunk({"content": "thought\n"})),
            sse_event(chunk({"content": "<channel|>"})),
            0.1,
            sse_event(chunk({"content": "괜찮아요."}, finish_reason="stop")),
            sse_event(chunk(None, usage=USAGE)),
            b"data: [DONE]\n\n",
        ]
    )
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, stream=body, headers=SSE_HEADERS))
    chunks = [c async for c in model(settings, recorder).astream("질문")]

    assert "".join(c.content for c in chunks) == "괜찮아요."
    [rec] = recorder.records("llm")
    # 표시를 첫 글자로 셌다면 거의 0초가 나온다. Windows 타이머 해상도(약 15ms)만큼 여유를 둔다
    assert rec.ttft_s is not None and rec.ttft_s >= 0.08
    assert rec.thought_chars == 8


# 도구 없이 보낸 호출에 5e590392 가 본문으로 보낸 표기 (2026-10-28 캡처)
TOOL_CALL_TEXT = (
    '<|tool_call>call:get_health_metrics{metric:<|"|>oxygen_saturation<|"|>,period:<|"|>last_14_days<|"|>}<tool_call|>'
)


@pytest.mark.parametrize(
    ("parts", "body"),
    [
        # 답변 전체가 표기일 때
        ([TOOL_CALL_TEXT], ""),
        # 표기가 여러 조각으로 나뉘어 올 때. 여는 표시도 쪼개져 온다
        (["<|tool", "_call>call:get_health_metrics{}", "<tool_call|>"], ""),
        (["<", "|", "tool_", "call>", "call", ":get_health_metrics{}", "<tool_call|>"], ""),
        # 답변 뒤에 표기가 붙을 때. 표기 앞의 답변 글은 그대로 둔다
        (["평균 ", "7,412걸음이에요.", TOOL_CALL_TEXT], "평균 7,412걸음이에요."),
        (["평균 7,412걸음이에요.<|tool", "_call>call:get_health_metrics{}", "<tool_call|>"], "평균 7,412걸음이에요."),
        # 닫는 표시 뒤에 글이 더 와도 표기부터 응답 끝까지 제외한다
        (["평균 7,412걸음이에요.", TOOL_CALL_TEXT, "다시 확인해 볼게요."], "평균 7,412걸음이에요."),
    ],
)
@respx.mock
async def test_stream_drops_tool_call_text(settings, recorder, parts, body):
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    chunks = [c async for c in model(settings, recorder).astream("질문")]

    assert "".join(c.content for c in chunks) == body
    [rec] = recorder.records("llm")
    assert rec.tool_call_text_chars == len("".join(parts)) - len(body)
    assert rec.thought_chars == 0


@pytest.mark.parametrize(
    "parts",
    [
        ["평균 ", "7,412걸음이에요."],
        # 여는 표시의 앞부분처럼 보이지만 표시가 아닐 때
        ["기준 <", "|범위|> 안이에요."],
        ["tool_call 은 ", "도구 호출이에요."],
        # 응답이 여는 표시의 앞부분에서 끝날 때
        ["평균 7,412걸음이에요. <|tool_ca"],
    ],
)
@respx.mock
async def test_stream_keeps_answer_without_tool_call_text(settings, recorder, parts):
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    chunks = [c async for c in model(settings, recorder).astream("질문")]

    assert "".join(c.content for c in chunks) == "".join(parts)
    assert recorder.records("llm")[0].tool_call_text_chars == 0


@pytest.mark.parametrize(
    ("content", "body", "thought_chars", "tool_call_text_chars"),
    [
        (TOOL_CALL_TEXT, "", 0, len(TOOL_CALL_TEXT)),
        ("<|channel>thought\n<channel|>" + TOOL_CALL_TEXT, "", 8, len(TOOL_CALL_TEXT)),
        ("평균 7,412걸음이에요." + TOOL_CALL_TEXT, "평균 7,412걸음이에요.", 0, len(TOOL_CALL_TEXT)),
        ("평균 7,412걸음이에요.", "평균 7,412걸음이에요.", 0, 0),
    ],
)
@respx.mock
async def test_non_stream_drops_tool_call_text(settings, recorder, content, body, thought_chars, tool_call_text_chars):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion(content)))
    result = await model(settings, recorder).ainvoke("질문")

    assert result.content == result.text == body
    [rec] = recorder.records("llm")
    assert (rec.thought_chars, rec.tool_call_text_chars) == (thought_chars, tool_call_text_chars)


# 사고 채널 표시를 지워 보내는 서버(69e434c6)가 도구 호출 표시도 지웠을 때를 가정한 표기
UNMARKED_TOOL_CALL = "call:get_health_metrics{metric:oxygen_saturation,period:last_14_days}"


@pytest.mark.parametrize(
    ("parts", "body"),
    [
        (["thought\n" + UNMARKED_TOOL_CALL], ""),
        # 표기가 여러 조각으로 나뉘어 올 때. "call:" 과 도구 이름도 쪼개져 온다
        (["thought", "\n", "call", ":", "get", "_health_metrics", "{", "metric:oxygen_saturation", "}"], ""),
        (["thought\nca", "ll:get_health", "_metrics{metric:oxygen_saturation}"], ""),
        # 표기 앞의 공백은 답변 글로 남는다
        (["thought\n", "\n" + UNMARKED_TOOL_CALL], "\n"),
    ],
)
@respx.mock
async def test_stream_drops_unmarked_tool_call_when_asked(settings, recorder, parts, body):
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    chunks = [c async for c in model(settings, recorder).astream("질문", drop_unmarked_tool_call=True)]

    assert "".join(c.content for c in chunks) == body
    [rec] = recorder.records("llm")
    assert rec.thought_chars == 8
    assert rec.tool_call_text_chars == len("".join(parts)) - len("thought\n") - len(body)


@pytest.mark.parametrize(
    "parts",
    [
        # "call" 로 시작하지만 도구 이름과 "{" 가 이어지지 않을 때
        ["thought\ncall 센터에 문의해 보세요."],
        ["thought\ncall", " 센터에 문의해 보세요."],
        ["thought\ncall:", " 상담 전화를 해 보세요."],
        ["thought\ncall:get_health_metrics 는 없는 도구예요."],
        ["thought\ncallback 알림을 켜 보세요."],
        # 응답이 표기의 앞부분에서 끝날 때
        ["thought\ncall:get_health"],
        # 답변 중간의 표기
        ["thought\n기록을 보면 ", UNMARKED_TOOL_CALL],
    ],
)
@respx.mock
async def test_stream_keeps_answer_that_does_not_start_with_unmarked_tool_call(settings, recorder, parts):
    respx.post(CHAT_URL).mock(return_value=text_response(*parts))
    chunks = [c async for c in model(settings, recorder).astream("질문", drop_unmarked_tool_call=True)]

    assert "".join(c.content for c in chunks) == "".join(parts).removeprefix("thought\n")
    assert recorder.records("llm")[0].tool_call_text_chars == 0


@respx.mock
async def test_stream_keeps_unmarked_tool_call_unless_asked(settings, recorder):
    respx.post(CHAT_URL).mock(return_value=text_response("thought\n", UNMARKED_TOOL_CALL))
    chunks = [c async for c in model(settings, recorder).astream("질문")]

    assert "".join(c.content for c in chunks) == UNMARKED_TOOL_CALL
    assert recorder.records("llm")[0].tool_call_text_chars == 0


@pytest.mark.parametrize(("asked", "body"), [(True, ""), (False, UNMARKED_TOOL_CALL)])
@respx.mock
async def test_non_stream_drops_unmarked_tool_call_only_when_asked(settings, recorder, asked, body):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("thought\n" + UNMARKED_TOOL_CALL)))
    result = await model(settings, recorder).ainvoke("질문", drop_unmarked_tool_call=asked)

    assert result.content == body
    assert recorder.records("llm")[0].tool_call_text_chars == len(UNMARKED_TOOL_CALL) - len(body)


@respx.mock
async def test_unmarked_tool_call_option_is_not_sent(settings, recorder):
    def reply(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            return text_response("네.")
        return httpx.Response(200, json=completion("네."))

    route = respx.post(CHAT_URL).mock(side_effect=reply)
    m = model(settings, recorder)
    for option in ({}, {"drop_unmarked_tool_call": False}, {"drop_unmarked_tool_call": True}):
        _ = [c async for c in m.astream("질문", **option)]
        await m.ainvoke("질문", **option)

    # 옵션은 요청 본문에 들어가지 않아 스트리밍·비스트리밍 요청 본문이 옵션과 관계없이 같다
    sent = [call.request.content for call in route.calls]
    assert sent[0::2] == [sent[0]] * 3 and sent[1::2] == [sent[1]] * 3
