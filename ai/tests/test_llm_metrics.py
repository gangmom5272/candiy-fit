import asyncio
import json
from typing import TypedDict

import httpx
import pytest
import respx
from langgraph.graph import END, START, StateGraph

from coach_ai.llm.client import LLMError, create_chat_model
from coach_ai.llm.metrics import LLMCallRecord, MetricsRecorder, StageRecord, current_request_id, stage
from tests.llm_fakes import CHAT_URL, SSE_HEADERS, USAGE, ScriptedStream, chunk, completion, sse_event

PROMPT = "어제 수면 6시간 12분, 평균 심박 61bpm 이었는데 괜찮아?"


def slow_first_token_stream(delay: float) -> httpx.Response:
    body = ScriptedStream(
        [
            sse_event(chunk({"role": "assistant", "content": ""})),
            delay,
            sse_event(chunk({"content": "괜찮아요."}, finish_reason="stop")),
            sse_event(chunk(None, usage=USAGE)),
            b"data: [DONE]\n\n",
        ]
    )
    return httpx.Response(200, stream=body, headers=SSE_HEADERS)


@respx.mock
async def test_stream_record_fields(settings, tmp_path):
    path = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(path)
    respx.post(CHAT_URL).mock(return_value=slow_first_token_stream(0.1))
    m = create_chat_model(settings, recorder=recorder)

    async for _ in m.astream(PROMPT, config={"metadata": {"request_id": "r-1", "node": "answer"}}):
        pass

    [rec] = recorder.records("llm")
    assert isinstance(rec, LLMCallRecord)
    assert rec.request_id == "r-1"
    assert rec.node == "answer"
    assert rec.stream is True
    assert rec.queue_wait_s is not None and rec.queue_wait_s < 0.05
    # 빈 role 청크를 첫 글자로 셌다면 거의 0초가 나온다. Windows 타이머 해상도(약 15ms)만큼 여유를 둔다
    assert rec.ttft_s is not None and rec.ttft_s >= 0.08
    assert rec.total_s >= rec.ttft_s
    assert (rec.input_tokens, rec.output_tokens, rec.cached_tokens) == (20, 5, 16)
    assert rec.finish_reason == "stop"
    assert rec.error_code is None

    saved = path.read_text(encoding="utf-8")
    assert json.loads(saved.splitlines()[0])["request_id"] == "r-1"
    for secret in ("수면", "61bpm", "괜찮아요"):
        assert secret not in saved


@respx.mock
async def test_non_stream_record(settings):
    recorder = MetricsRecorder()
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion()))
    m = create_chat_model(settings, recorder=recorder).bind(call_kind="summary")

    token = current_request_id.set("r-ctx")
    try:
        await m.ainvoke(PROMPT)
    finally:
        current_request_id.reset(token)

    [rec] = recorder.records("llm")
    assert rec.request_id == "r-ctx"
    assert rec.stream is False and (rec.call_kind, rec.background) == ("summary", True)
    assert rec.ttft_s is None
    assert rec.queue_wait_s is not None
    assert (rec.input_tokens, rec.cached_tokens, rec.finish_reason) == (20, 16, "stop")


@respx.mock
async def test_error_record(settings):
    settings.llm_max_retries = 1
    recorder = MetricsRecorder()
    respx.post(CHAT_URL).mock(return_value=httpx.Response(429, json={"error": {"message": "busy"}}))
    m = create_chat_model(settings, recorder=recorder, retry_base_delay=0.0)

    with pytest.raises(LLMError):
        await m.ainvoke(PROMPT, config={"metadata": {"request_id": "r-2"}})

    [rec] = recorder.records("llm")
    assert rec.error_code == "rate_limited"
    assert rec.attempts == 2
    assert rec.request_id == "r-2"


@respx.mock
async def test_queue_wait_is_recorded_separately(settings):
    settings.llm_max_concurrency = 1
    recorder = MetricsRecorder()

    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=completion())

    respx.post(CHAT_URL).mock(side_effect=slow)
    m = create_chat_model(settings, recorder=recorder)
    await asyncio.gather(m.ainvoke("a"), m.ainvoke("b"))

    first, second = sorted(recorder.records("llm"), key=lambda r: r.queue_wait_s)
    assert first.queue_wait_s < 0.02
    assert second.queue_wait_s >= 0.03
    assert second.total_s < 0.04 + second.queue_wait_s


def test_stage_timer_records_duration_and_error():
    recorder = MetricsRecorder()
    with stage("tool:get_sleep_records", request_id="r-3", node="tools", recorder=recorder):
        pass
    with pytest.raises(RuntimeError), stage("tool:compare_periods", recorder=recorder):
        raise RuntimeError("평균 수면 6시간")

    ok, failed = recorder.records("stage")
    assert isinstance(ok, StageRecord)
    assert (ok.name, ok.request_id, ok.node, ok.error) == ("tool:get_sleep_records", "r-3", "tools", None)
    assert ok.duration_s >= 0
    assert failed.error == "RuntimeError"


async def test_stage_timer_inside_async_code():
    recorder = MetricsRecorder()
    token = current_request_id.set("r-4")
    try:
        with stage("tool:get_metric_summary", recorder=recorder):
            await asyncio.sleep(0.02)
    finally:
        current_request_id.reset(token)
    [rec] = recorder.records("stage")
    assert rec.request_id == "r-4"
    assert rec.duration_s >= 0.015  # Windows 에서는 sleep 이 타이머 해상도만큼 일찍 끝날 수 있다


def test_recorder_keeps_recent_records_only():
    recorder = MetricsRecorder(max_records=2)
    for i in range(3):
        with stage(f"s{i}", recorder=recorder):
            pass
    assert [r.name for r in recorder.records()] == ["s1", "s2"]


@respx.mock
async def test_langgraph_node_is_recorded(settings):
    class State(TypedDict):
        answer: str

    recorder = MetricsRecorder()
    respx.post(CHAT_URL).mock(return_value=slow_first_token_stream(0.01))
    m = create_chat_model(settings, recorder=recorder)

    async def answer(state: State) -> State:
        msg = await m.ainvoke(PROMPT)
        return {"answer": msg.content}

    graph = StateGraph(State)
    graph.add_node("answer", answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    app = graph.compile()

    # 화면 스트리밍처럼 messages 모드로 실행하면 ainvoke 도 스트리밍 경로를 거친다
    async for _ in app.astream({"answer": ""}, config={"metadata": {"request_id": "r-5"}}, stream_mode="messages"):
        pass

    [rec] = recorder.records("llm")
    assert (rec.request_id, rec.node, rec.stream) == ("r-5", "answer", True)
    assert rec.queue_wait_s is not None and rec.ttft_s is not None
    assert rec.input_tokens == 20
