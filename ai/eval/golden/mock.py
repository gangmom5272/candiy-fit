"""--mock 실행의 모의 LLM. 사내 LLM 대신 httpx.MockTransport 가 OpenAI 호환 스트리밍 응답을 만든다.

운영과 같은 create_chat_model·run_coach 경로를 거치고 요청만 네트워크로 보내지 않는다.
- 질문이 마지막 메시지인 호출에는 그 문항의 expected_tools 를 도구 호출로 반환한다.
  기대 도구가 없으면 정해진 문장으로 답한다.
- 도구 결과가 마지막 메시지인 호출에는 결과의 표시 문자열(display, coverage, 결측 표시, 안내 문구 등)을
  이어 붙여 답한다.
실제 LLM 의 답변과 형식이 다르다. 실행·기록 저장·채점이 끝까지 실행되는지 확인하는 용도다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx

from coach_ai.config import Settings
from coach_ai.history.tokens import MESSAGE_TOKENS, count_tokens, count_tool_definition_tokens
from eval.golden.items import GoldenItem

MOCK_BASE_URL = "https://llm.mock.invalid/v1"
MOCK_MODEL = "mock"
NO_TOOL_ANSWER = "모의 응답이에요. 기록을 조회하지 않고 답해요."
FAILED_TOOL = "기록을 조회하지 못했어요"
_SSE_HEADERS = {"content-type": "text/event-stream"}
# 결과 안내 문구 칸. 문구를 그대로 답변에 넣는다
_NOTE_KEYS = ("reliable_note", "in_progress_note", "not_counted_note", "distance_note", "no_record")


def mock_settings() -> Settings:
    """.env 를 읽지 않는 모의 실행 설정. 키와 주소는 실제 값이 아니다."""
    return Settings(_env_file=None, LLM_API_KEY="mock", LLM_BASE_URL=MOCK_BASE_URL, LLM_CHAT_MODEL=MOCK_MODEL)


def _chunk(
    delta: dict[str, Any] | None = None, *, finish_reason: str | None = None, usage: dict | None = None
) -> bytes:
    body: dict[str, Any] = {"id": "mock", "object": "chat.completion.chunk", "created": 0, "model": MOCK_MODEL}
    body["choices"] = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    if usage is not None:
        body["usage"] = usage
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()


def _stream(parts: Sequence[bytes], prompt_tokens: int, completion_tokens: int, finish_reason: str) -> httpx.Response:
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    body = b"".join([*parts, _chunk({}, finish_reason=finish_reason), _chunk(None, usage=usage), b"data: [DONE]\n\n"])
    return httpx.Response(200, content=body, headers=_SSE_HEADERS)


def text_response(text: str, prompt_tokens: int) -> httpx.Response:
    parts = [_chunk({"role": "assistant", "content": ""}), _chunk({"content": text})]
    return _stream(parts, prompt_tokens, count_tokens(text), "stop")


def tool_call_response(calls: Sequence[dict[str, Any]], prompt_tokens: int) -> httpx.Response:
    parts = [_chunk({"role": "assistant", "content": None})]
    arguments = []
    for index, call in enumerate(calls):
        args = json.dumps(call["args"], ensure_ascii=False)
        arguments.append(args)
        function = {"name": call["name"], "arguments": args}
        delta = {"tool_calls": [{"index": index, "id": f"call_{index + 1}", "type": "function", "function": function}]}
        parts.append(_chunk(delta))
    return _stream(parts, prompt_tokens, sum(map(count_tokens, arguments)), "tool_calls")


def prompt_tokens(body: dict[str, Any]) -> int:
    """요청 본문의 입력 토큰 추정. 대화 예산 계산과 같은 추정 방식(history.tokens)이다."""
    total = count_tool_definition_tokens(body.get("tools") or [])
    for message in body.get("messages", []):
        text = message.get("content") or ""
        calls = json.dumps(message.get("tool_calls") or [], ensure_ascii=False) if message.get("tool_calls") else ""
        total += (
            MESSAGE_TOKENS + count_tokens(text if isinstance(text, str) else json.dumps(text)) + count_tokens(calls)
        )
    return total


def _phrases(node: Any) -> Iterator[str]:
    """도구 결과에서 답변에 넣을 표시 문자열을 결과 칸 순서대로 꺼낸다."""
    if isinstance(node, list):
        for value in node:
            yield from _phrases(value)
        return
    if not isinstance(node, dict):
        return
    day = node.get("date")
    if isinstance(node.get("display"), str):
        yield f"{day} {node['display']}" if day else node["display"]
    if isinstance(node.get("missing"), str):
        yield f"{day} {node['missing']}"
    # 밤별 기록(SleepNight)의 취침·기상은 시각 문자열이고, 수면 요약의 취침·기상은 시각 범위 객체다
    if isinstance(node.get("bedtime"), str) and isinstance(node.get("wake"), str):
        sleep = f"{day} {node['bedtime']}에 잠들어 {node['wake']}에 깸"
        if node.get("total"):
            sleep += f", 실제 수면 {node['total']}"
        if node.get("deep_min") is not None:
            sleep += f", 깊은 잠 {node['deep_min']}분"
        yield sleep
    if "count" in node and isinstance(node.get("duration"), str):
        yield f"{node.get('type') or '운동'} {node['count']}회 {node['duration']}"
    for key, value in node.items():
        if key == "coverage" and isinstance(value, str):
            yield f"{value} 기준"
        elif key == "missing_days" and isinstance(value, list):
            yield from (f"{d} 기록 없음" for d in value)
        elif key == "unsynced_days" and isinstance(value, list):
            yield from (f"{d} 동기화 전" for d in value)
        elif key in _NOTE_KEYS and isinstance(value, str):
            yield value
        elif isinstance(value, dict | list):
            yield from _phrases(value)


def answer_from_tools(contents: Sequence[str]) -> str:
    """도구 메시지 본문들로 모의 답변을 만든다. 오류·거부 결과는 조회하지 못했다고 쓴다."""
    parts: list[str] = []
    for content in contents:
        try:
            body = json.loads(content)
        except ValueError:
            body = None
        if not isinstance(body, dict) or "result" not in body:
            parts.append(FAILED_TOOL)
            continue
        parts += list(dict.fromkeys(_phrases(body["result"])))
    return "모의 응답이에요. " + ". ".join(parts) + "." if parts else NO_TOOL_ANSWER


class MockLLM:
    """httpx.MockTransport 의 처리 함수. 질문 문장으로 문항의 기대 도구를 찾는다."""

    def __init__(self, items: Sequence[GoldenItem]) -> None:
        self._tools: dict[str, tuple[dict[str, Any], ...]] = {}
        for item in items:
            known = self._tools.setdefault(item.question, item.expected_tools)
            if known != item.expected_tools:
                raise ValueError(f"질문이 같고 기대 도구가 다른 문항이 있어 모의 응답을 정할 수 없다: {item.id}")
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests += 1
        messages = body["messages"]
        tokens = prompt_tokens(body)
        if messages[-1]["role"] == "tool":
            contents = []
            for message in reversed(messages):
                if message["role"] != "tool":
                    break
                contents.append(message.get("content") or "")
            return text_response(answer_from_tools(contents[::-1]), tokens)
        calls = self._tools.get(messages[-1]["content"], ())
        return tool_call_response(calls, tokens) if calls else text_response(NO_TOOL_ANSWER, tokens)


def mock_http_client(llm: MockLLM) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(llm))
