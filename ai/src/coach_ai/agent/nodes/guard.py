from __future__ import annotations

from itertools import islice
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from coach_ai.agent.rules.guard import check, general_drug_fallback
from coach_ai.agent.runtime import CoachRuntime
from coach_ai.agent.state import CoachState


def _questions(state: CoachState) -> tuple[str, str | None]:
    """이번 질문과 바로 앞 사용자 질문. 앞 질문이 요약에만 있으면 메시지에 없어 None 이다."""
    recent = list(islice((m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), 2))
    if not recent:
        return "", None
    return recent[0], recent[1] if len(recent) == 2 else None


async def guard_node(state: CoachState, runtime: Runtime[CoachRuntime]) -> dict[str, Any]:
    question, previous = _questions(state)
    result = check(question, previous)
    if result is None:
        # 약 일반 지식 질문은 통과시키고, 답을 다 받은 뒤 검사해 보낸다 (nodes/model.py)
        fallback = general_drug_fallback(question, previous)
        return {} if fallback is None else {"answer_fallback": fallback}
    events = runtime.context.events
    await events.status("answering", "안내를 준비하고 있어요")
    await events.token(result.answer)
    return {
        "messages": [AIMessage(content=result.answer)],
        "finish_reason": "guard",
        "blocked_by": result.category,
    }


def route_after_guard(state: CoachState) -> str:
    return "finalize" if state.get("finish_reason") == "guard" else "agent"
