from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from coach_ai.agent.runtime import CoachRuntime
from coach_ai.agent.state import CoachState


async def finalize_node(state: CoachState, runtime: Runtime[CoachRuntime]) -> dict[str, Any]:
    # 답변 조각이 하나도 없던 경우에도 근거는 done 보다 먼저 나가야 한다. 고정 답(경계 확인 안내, 고정 답으로 바꾼
    # 약 답)은 조회한 기록을 쓰지 않아 근거를 보내지 않는다
    if state.get("finish_reason") != "guard":
        await runtime.context.events.evidence(state["evidence"])
    return {"finish_reason": state.get("finish_reason") or "stop"}
