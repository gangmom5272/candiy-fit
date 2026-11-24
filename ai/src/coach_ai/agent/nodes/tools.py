from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from coach_ai.agent.executor import AddedCalls, ExtendedRun, ToolCall, ToolReply
from coach_ai.agent.rules.exercise_words import exercise_texts, mentions_exercise
from coach_ai.agent.rules.sleep_period import period_texts, with_default_period
from coach_ai.agent.rules.spo2_signals import added_signals
from coach_ai.agent.runtime import CoachRuntime
from coach_ai.agent.state import CoachState

TOOL_STATUS = {
    "get_metric_summary": "기록 요약을 확인하고 있어요",
    "get_sleep_records": "수면 기록을 확인하고 있어요",
    "get_daily_trend": "날짜별 기록을 확인하고 있어요",
    "compare_periods": "기간별 기록을 비교하고 있어요",
    "get_health_overview": "건강 기록을 모아 보고 있어요",
    "get_exercise_sessions": "운동 기록을 확인하고 있어요",
    "get_lifestyle_score": "점수를 확인하고 있어요",
    "get_daily_message": "오늘의 한마디를 확인하고 있어요",
}


def _shown_tool(name: str) -> str | None:
    # 등록되지 않은 도구 이름은 LLM 출력 그대로라 화면 이벤트에 넣지 않는다
    return name if name in TOOL_STATUS else None


def _tool_message(
    original: ToolCall, call: ToolCall, reply: ToolReply, added: tuple[ToolCall, ToolReply] | None
) -> ToolMessage:
    """모델이 낸 호출 하나의 도구 메시지. artifact 는 LLM 에 보내지 않는다.

    인자를 바꿔 실행한 호출은 실행한 호출을 artifact 에 넣는다. 더한 호출을 실행했으면 그 결과 본문을 이 호출의 결과
    본문 뒤 added 목록에 이어 붙여 한 도구 결과로 전달하고, 실행한 호출과 더한 호출을 artifact 에 넣는다.
    처리 함수가 인자의 기간 밖 기간까지 계산했으면 실행한 호출, 그 기간, 더한 호출을 ExtendedRun 으로 넣는다.
    """
    name = reply.name or None
    extra = reply.extra_period
    artifact: ToolCall | AddedCalls | ExtendedRun | None
    if added is None:
        if extra is not None:
            artifact = ExtendedRun(call, extra)
        else:
            artifact = None if call is original else call
        return ToolMessage(reply.content, tool_call_id=reply.call_id, name=name, artifact=artifact)
    added_call, added_reply = added
    # 더한 호출은 이 호출이 검사를 통과했을 때만 실행해 두 본문 모두 게이트웨이가 만든 JSON 객체다
    body = json.loads(reply.content)
    body["added"] = [json.loads(added_reply.content)]
    content = json.dumps(body, ensure_ascii=False)
    artifact = AddedCalls(call, (added_call,)) if extra is None else ExtendedRun(call, extra, (added_call,))
    return ToolMessage(content, tool_call_id=reply.call_id, name=name, artifact=artifact)


async def tools_node(state: CoachState, runtime: Runtime[CoachRuntime]) -> dict[str, Any]:
    rt = runtime.context
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    asked = [ToolCall(call["id"] or call["name"], call["name"], call["args"]) for call in last.tool_calls]
    asked += [ToolCall(bad.get("id") or "invalid", bad.get("name") or "", None) for bad in last.invalid_tool_calls]
    question = next((m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")
    texts = period_texts(question, rt.ctx)
    calls = [with_default_period(call, texts) for call in asked]
    added = added_signals(calls, question)
    asks_exercise = mentions_exercise(exercise_texts(question, rt.ctx))
    shown = [call for call in calls if call.args is not None]
    for call in shown:
        message = TOOL_STATUS.get(call.name, "기록을 확인하고 있어요")
        await rt.events.status("tool_call", message, tool=_shown_tool(call.name))
    replies = await rt.executor.execute(calls, rt.ctx, added=added, asks_exercise=asks_exercise)
    for call in shown:
        await rt.events.status("tool_done", "기록을 확인했어요", tool=_shown_tool(call.name))
    # 실행한 더한 호출의 결과는 calls 의 결과 뒤에 있다
    ran = {r.call_id: r for r in replies[len(calls) :]}
    return {
        "messages": [
            _tool_message(
                original, call, reply, None if extra is None or extra.id not in ran else (extra, ran[extra.id])
            )
            for original, call, extra, reply in zip(asked, calls, added, replies[: len(calls)], strict=True)
        ],
        "evidence": [e for r in replies for e in r.evidence],
        "tool_rounds": state["tool_rounds"] + 1,
    }
