from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

BASE = "https://llm.test/v1"
CHAT_URL = f"{BASE}/chat/completions"
SSE_HEADERS = {"content-type": "text/event-stream"}


def chunk(delta: dict | None = None, *, finish_reason: str | None = None, usage: dict | None = None) -> dict:
    body: dict = {"id": "c-1", "object": "chat.completion.chunk", "created": 0, "model": "gemma4-31b"}
    body["choices"] = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    if usage is not None:
        body["usage"] = usage
    return body


def sse_event(c: dict) -> bytes:
    return f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode()


USAGE = {
    "prompt_tokens": 20,
    "completion_tokens": 5,
    "total_tokens": 25,
    "prompt_tokens_details": {"cached_tokens": 16},
}

TEXT_CHUNKS = [
    chunk({"role": "assistant", "content": ""}),  # role 만 있는 첫 청크
    chunk({"content": "잠은 "}),
    chunk({"content": "규칙적으로."}),
    chunk({}, finish_reason="stop"),
    chunk(None, usage=USAGE),
]


def sse(*chunks: dict) -> bytes:
    return b"".join(sse_event(c) for c in chunks) + b"data: [DONE]\n\n"


def text_stream_response() -> httpx.Response:
    return httpx.Response(200, content=sse(*TEXT_CHUNKS), headers=SSE_HEADERS)


def completion(content: str = "확인", usage: dict | None = None) -> dict:
    return {
        "id": "c-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gemma4-31b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": usage or USAGE,
    }


class ScriptedStream(httpx.AsyncByteStream):
    """조각 사이에 지연을 두거나 도중에 연결을 끊는 SSE 본문."""

    def __init__(self, parts: list[bytes | float | BaseException]) -> None:
        self.parts = parts

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for part in self.parts:
            if isinstance(part, BaseException):
                raise part
            if isinstance(part, float):
                await asyncio.sleep(part)
                continue
            yield part

    async def aclose(self) -> None:
        return None


def tool_call_response(
    *calls: tuple[str, dict], call_ids: list[str] | None = None, text: tuple[str, ...] = ()
) -> httpx.Response:
    """도구 호출 스트리밍 응답. text 는 도구 호출 앞에 오는 본문 조각이다. 인자는 두 조각으로 나눠 보낸다."""
    chunks = [chunk({"role": "assistant", "content": None})]
    chunks += [chunk({"content": p}) for p in text]
    for i, (name, args) in enumerate(calls):
        call_id = (call_ids or [f"call_{i + 1}" for _ in calls])[i]
        args_json = json.dumps(args, ensure_ascii=False)
        half = len(args_json) // 2
        chunks.append(
            chunk({"tool_calls": [{"index": i, "id": call_id, "type": "function",
                                   "function": {"name": name, "arguments": args_json[:half]}}]})
        )  # fmt: skip
        chunks.append(chunk({"tool_calls": [{"index": i, "function": {"arguments": args_json[half:]}}]}))
    chunks.append(chunk({}, finish_reason="tool_calls"))
    chunks.append(chunk(None, usage={"prompt_tokens": 300, "completion_tokens": 20, "total_tokens": 320}))
    return httpx.Response(200, content=sse(*chunks), headers=SSE_HEADERS)


def text_response(*parts: str, finish_reason: str = "stop") -> httpx.Response:
    """답변 스트리밍 응답. role 만 있는 첫 청크 뒤에 parts 를 조각으로 보낸다."""
    chunks = [chunk({"role": "assistant", "content": ""})]
    chunks += [chunk({"content": p}) for p in parts]
    chunks.append(chunk({}, finish_reason=finish_reason))
    chunks.append(chunk(None, usage={"prompt_tokens": 500, "completion_tokens": 40, "total_tokens": 540}))
    return httpx.Response(200, content=sse(*chunks), headers=SSE_HEADERS)
