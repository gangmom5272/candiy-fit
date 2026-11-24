import json

import httpx
import respx

from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder, allowed_headers
from tests.llm_fakes import CHAT_URL, SSE_HEADERS, USAGE, chunk, completion, sse

SECRET_COOKIE = "session=very-secret-value"


def stream_response(*, fingerprint: str | None = None, headers: dict | None = None) -> httpx.Response:
    last = chunk({}, finish_reason="stop")
    if fingerprint:
        last["system_fingerprint"] = fingerprint
    body = sse(
        chunk({"role": "assistant", "content": ""}), chunk({"content": "좋아요"}), last, chunk(None, usage=USAGE)
    )
    return httpx.Response(200, content=body, headers={**SSE_HEADERS, **(headers or {})})


def test_allowed_headers_drops_sensitive_and_normalizes():
    assert allowed_headers([" X-Backend ", "Set-Cookie", "authorization", "x-backend", ""]) == ("x-backend",)


@respx.mock
async def test_fingerprint_recorded_for_stream_and_non_stream(settings):
    recorder = MetricsRecorder()
    respx.post(CHAT_URL).mock(
        side_effect=[
            stream_response(fingerprint="fp-a"),
            httpx.Response(200, json={**completion(), "system_fingerprint": "fp-b"}),
        ]
    )
    m = create_chat_model(settings, recorder=recorder)
    async for _ in m.astream("질문"):
        pass
    await m.ainvoke("질문")

    assert [r.system_fingerprint for r in recorder.records("llm")] == ["fp-a", "fp-b"]


@respx.mock
async def test_headers_are_not_requested_without_allowlist(settings):
    recorder = MetricsRecorder()
    respx.post(CHAT_URL).mock(return_value=stream_response(headers={"x-backend": "b1"}))
    m = create_chat_model(settings, recorder=recorder)
    assert m.include_response_headers is False
    async for _ in m.astream("질문"):
        pass
    assert recorder.records("llm")[0].instance is None


@respx.mock
async def test_only_allowlisted_safe_headers_are_recorded(settings, tmp_path):
    settings.llm_instance_headers = "X-Backend, set-cookie"
    path = tmp_path / "calls.jsonl"
    recorder = MetricsRecorder(path)
    headers = {"x-backend": "b1", "set-cookie": SECRET_COOKIE, "x-request-id": "req-123"}
    respx.post(CHAT_URL).mock(
        side_effect=[stream_response(headers=headers), httpx.Response(200, json=completion(), headers=headers)]
    )
    m = create_chat_model(settings, recorder=recorder)
    assert m.include_response_headers is True

    async for _ in m.astream("질문"):
        pass
    await m.ainvoke("질문")

    assert [r.instance for r in recorder.records("llm")] == [{"x-backend": "b1"}, {"x-backend": "b1"}]
    saved = path.read_text(encoding="utf-8")
    assert json.loads(saved.splitlines()[0])["instance"] == {"x-backend": "b1"}
    assert SECRET_COOKIE not in saved
    assert "req-123" not in saved


@respx.mock
async def test_missing_allowlisted_header_leaves_instance_empty(settings):
    settings.llm_instance_headers = "x-backend"
    recorder = MetricsRecorder()
    respx.post(CHAT_URL).mock(return_value=stream_response())
    m = create_chat_model(settings, recorder=recorder)
    async for _ in m.astream("질문"):
        pass
    assert recorder.records("llm")[0].instance is None
