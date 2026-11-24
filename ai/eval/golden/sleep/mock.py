"""수면 코칭 골든셋 --mock 실행의 모의 LLM. 사내 LLM 대신 httpx.MockTransport 가 OpenAI 호환 스트리밍 응답을 만든다.

응답 모양은 코칭 골든셋 모의 응답(eval/golden/mock.py)과 같고, 문항마다 Script 로 왕복별 호출과 답을 정할 수 있다.
- 첫 왕복: Script.first 가 없으면 정답(expected)과 허용 답안 가운데 지금 도구 스키마를 통과하는 첫 호출 묶음을 부른다.
  분석 인자처럼 지금 스키마에 없는 인자는 빼고 부른다. 정답이 no_tool·guard 면 도구 없이 답한다.
- 이어진 왕복: Script.later 의 호출을 차례로 부른다.
- 답: Script.answer 가 없으면 받은 도구 결과의 표시 문자열로 답을 만든다 (eval.golden.mock.answer_from_tools).
- 도구 정의를 넣지 않은 요청(도구 왕복 한도를 넘은 호출)에는 답만 보낸다.
실제 LLM 의 답과 형식이 다르다. 실행·기록 저장·채점이 끝까지 실행되는지와 채점이 틀린 답을 세는지 확인하는 용도다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx

from coach_ai.tools.schemas import TOOL_INPUTS
from eval import tool_selection as ts
from eval.golden.mock import NO_TOOL_ANSWER, answer_from_tools, prompt_tokens, text_response, tool_call_response
from eval.golden.sleep.items import SleepItem


@dataclass(frozen=True, slots=True)
class Script:
    """문항 하나의 모의 응답. first 가 None 이면 정답에서 정하고, answer 가 None 이면 도구 결과로 만든다."""

    first: tuple[dict[str, Any], ...] | None = None
    later: tuple[tuple[dict[str, Any], ...], ...] = ()
    answer: str | None = None


def _in_schema(call: dict[str, Any]) -> dict[str, Any]:
    schema = TOOL_INPUTS.get(call["name"])
    if schema is None:
        return call
    return {**call, "args": {k: v for k, v in call["args"].items() if k in schema.model_fields}}


def default_first_calls(item: SleepItem) -> tuple[dict[str, Any], ...]:
    """정답과 허용 답안 가운데 지금 도구 스키마를 통과하는 첫 호출 묶음. 통과하는 묶음이 없으면 정답 묶음이다.

    분석 인자 기대값은 첫 값으로(목록이면 첫 값, null 이면 빼고) 펼치고, 지금 스키마에 없는 인자는 뺀다.
    """
    if item.expected["type"] != "tool_calls":
        return ()
    candidates = [item.expected_calls, *(a["tool_calls"] for a in item.acceptable if a["type"] == "tool_calls")]
    shaped = [tuple(_in_schema(ts.expected_variants(call)[0]) for call in calls) for calls in candidates]
    return next((calls for calls in shaped if all(ts.gateway_denial(c) is None for c in calls)), shaped[0])


class SleepMockLLM:
    """httpx.MockTransport 의 처리 함수. 마지막 사용자 메시지(이번 질문)로 문항의 모의 응답을 찾는다."""

    def __init__(self, items: Sequence[SleepItem], scripts: Mapping[str, Script] | None = None) -> None:
        scripts = dict(scripts or {})
        if unknown := sorted(set(scripts) - {item.id for item in items}):
            raise ValueError(f"문항에 없는 모의 응답 id: {', '.join(unknown)}")
        self._scripts: dict[str, Script] = {}
        for item in items:
            script = scripts.get(item.id, Script())
            if script.first is None:
                script = replace(script, first=default_first_calls(item))
            if self._scripts.setdefault(item.question, script) != script:
                raise ValueError(f"질문이 같고 모의 응답이 다른 문항이 있어 응답을 정할 수 없다: {item.id}")
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests += 1
        messages = body["messages"]
        tokens = prompt_tokens(body)
        last = max(i for i, m in enumerate(messages) if m["role"] == "user")
        script = self._scripts.get(messages[last]["content"], Script(first=()))
        after = messages[last + 1 :]
        done = sum(1 for m in after if m["role"] == "assistant" and m.get("tool_calls"))
        if done == 0:
            calls = script.first or ()
        else:
            calls = script.later[done - 1] if done - 1 < len(script.later) else ()
        if calls and body.get("tools"):
            return tool_call_response(calls, tokens)
        if script.answer is not None:
            return text_response(script.answer, tokens)
        contents = [m.get("content") or "" for m in after if m["role"] == "tool"]
        return text_response(answer_from_tools(contents) if contents else NO_TOOL_ANSWER, tokens)
