from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from coach_ai.agent.budget import PreviousConversation
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import EventEmitter, EventSink
from coach_ai.agent.executor import ToolExecutor, ToolHandler
from coach_ai.agent.nodes.finalize import finalize_node
from coach_ai.agent.nodes.guard import guard_node, route_after_guard
from coach_ai.agent.nodes.model import agent_node, route_after_agent
from coach_ai.agent.nodes.tools import tools_node
from coach_ai.agent.prompts import (
    DEFAULT_PROMPT_VERSION,
    load_system_prompt,
    other_day_notice,
    render_system_prompt,
    system_with_history,
)
from coach_ai.agent.rules.guard import GuardCategory
from coach_ai.agent.runtime import MAX_TOOL_ROUNDS, CoachRuntime
from coach_ai.agent.state import CoachState, Evidence, FinishReason
from coach_ai.history.budget import InputTooLargeError
from coach_ai.history.types import HistoryUsage
from coach_ai.llm.client import LLMError
from coach_ai.llm.metrics import current_request_id
from coach_ai.ports import HealthDataError, HealthDataPort
from coach_ai.security.audit import AuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.security.tracing import ensure_tracing_disabled
from coach_ai.tools.periods import local_today
from coach_ai.tools.schemas import PublicMetric, public_metrics

INTERNAL_MESSAGE = "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."


def build_graph():
    graph = StateGraph(CoachState, context_schema=CoachRuntime)
    graph.add_node("guard", guard_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", route_after_guard, ["agent", "finalize"])
    graph.add_conditional_edges("agent", route_after_agent, ["tools", "finalize"])
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile()


GRAPH = build_graph()


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """최근 대화 한 턴. 요약 이후의 원문 메시지다."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class CoachResult:
    """요청 하나의 결과. answer 는 token 으로 내보낸 글이다 (실패하면 그때까지 보낸 부분 답변).

    실패하면 error_code 가 error 이벤트의 code 이고 evidence 는 비어 있다.
    """

    answer: str
    evidence: list[Evidence]
    finish_reason: FinishReason
    blocked_by: GuardCategory | None = None
    error_code: str | None = None
    tool_rounds: int = 0
    # 마지막 LLM 호출에 넣은 이전 대화의 양. LLM 을 호출하지 않았으면 None 이다
    history_usage: HistoryUsage | None = None
    messages: list[AnyMessage] = field(default_factory=list)
    # 실패 원인. 사용자에게 보내지 않는다 (문구에 회원 ID·건강 수치가 섞일 수 있음)
    exception: Exception | None = field(default=None, repr=False)
    # 앞 답의 건강 수치를 도구 없이 다시 쓴 첫 답을 보내지 않고 다시 조회하게 한 횟수 (0 또는 1)
    answer_rechecks: int = 0
    # 효과 단정 끝맺음을 완곡한 말로 바꿔 보낸 문장 수
    hedged_sentences: int = 0
    # 답 글의 내부 표시(사고 채널 표시 등)를 정리한 응답 수
    marker_cleanups: int = 0
    # 앞 답에도 질문에도 없는 건강 수치를 도구 없이 말한 첫 답을 보내지 않고 다시 조회하게 한 횟수 (0 또는 1).
    # answer_rechecks 와 합쳐도 요청마다 많아야 1 이다
    unsupported_rechecks: int = 0
    # 운동이 잠에 준 효과를 물은 질문에 도구 없이 끝난 첫 답을 보내지 않고 생활 요인 비교를 조회하게 한 횟수
    # (0 또는 1). answer_rechecks·unsupported_rechecks 와 합쳐도 요청마다 많아야 1 이다
    exercise_rechecks: int = 0


def build_messages(
    question: str,
    summary: str | None,
    history: Sequence[ChatTurn],
    system_prompt: str,
    *,
    day_notice: str | None = None,
) -> list[AnyMessage]:
    """시스템 프롬프트 → 요약 → 다른 날 대화 안내(day_notice) → 최근 대화 → 이번 질문 순서로 조립한다.

    요약은 시스템 메시지 끝에 붙인다 (시스템 메시지를 하나만 받는 템플릿 대비, 프리픽스 캐시 재사용).
    """
    system = system_with_history(system_prompt, summary, day_notice=day_notice)
    messages: list[AnyMessage] = [SystemMessage(content=system)]
    for turn in history:
        messages.append(HumanMessage(content=turn.content) if turn.role == "user" else AIMessage(content=turn.content))
    messages.append(HumanMessage(content=question))
    return messages


def _previous_conversation(
    ctx: CoachContext, system_prompt: str, today: date
) -> tuple[str | None, Sequence[ChatTurn], PreviousConversation]:
    """build_messages 에 넣을 요약·대화 목록과, LLM 호출마다 예산을 검사할 이전 대화."""
    turns = [
        chat for turn in ctx.turns for chat in (ChatTurn("user", turn.question), ChatTurn("assistant", turn.answer))
    ]
    text = None if ctx.summary is None else ctx.summary.text
    notice = other_day_notice((turn.answered_on for turn in ctx.turns), today)
    previous = PreviousConversation(
        system_prompt=system_prompt, summary=ctx.summary, turns=ctx.turns, day_notice=notice
    )
    return text, turns, previous


def _last_sent(runtime: CoachRuntime | None) -> HistoryUsage | None:
    return runtime.history_sent[-1] if runtime is not None and runtime.history_sent else None


# 예외 문구는 이벤트에 넣지 않는다
def _error_fields(e: Exception) -> tuple[str, str, bool, list[PublicMetric]]:
    if isinstance(e, HealthDataError):
        return e.code, e.message, e.retryable, public_metrics(e.metrics)
    if isinstance(e, LLMError | InputTooLargeError):
        return e.code, e.message, e.retryable, []
    return "internal", INTERNAL_MESSAGE, True, []


async def run_coach(
    ctx: CoachContext,
    question: str,
    *,
    port: HealthDataPort,
    model: BaseChatModel,
    sink: EventSink,
    now: datetime | None = None,
    message_id: str | None = None,
    executor: ToolExecutor | None = None,
    handlers: dict[str, ToolHandler] | None = None,
    audit: AuditSink | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> CoachResult:
    """질문 하나를 처리해 이벤트를 sink 로 내보낸다. 예외를 발생시키지 않고 done 또는 error 하나로 끝낸다.

    now(없으면 현재 시각)의 ctx.tz 날짜로 도구 기간을 계산하고 시스템 프롬프트의 날짜 자리를 채운다.
    now 는 시간대 정보가 있어야 한다. 없으면 LLM 요청 없이 internal 오류로 끝난다.
    이전 대화는 ctx.summary·ctx.turns 에서 LLM 호출마다 예산(history.budget)에 맞는 만큼 넣는다. 이전 대화를 모두
    빼도 넘으면 그 호출을 보내지 않고 input_too_large 오류로 끝난다.
    호출 쪽이 취소하면(asyncio.CancelledError) 종료 이벤트 없이 취소가 그대로 전파된다.
    """
    events = EventEmitter(ctx, sink)
    token = current_request_id.set(ctx.request_id)
    runtime: CoachRuntime | None = None
    try:
        # 그래프 실행이 시작되면 LangChain 이 추적 콜백을 붙이므로 그 전에 검사한다
        ensure_tracing_disabled()
        today = local_today(now or datetime.now(UTC), ctx.tz)
        system_prompt = render_system_prompt(load_system_prompt(prompt_version), today, ctx.profile)
        summary, history, previous = _previous_conversation(ctx, system_prompt, today)
        runtime = CoachRuntime(
            today=today,
            ctx=ctx,
            model=model,
            executor=executor or ToolGateway(port, today, handlers=handlers, audit=audit),
            events=events,
            max_tool_rounds=max_tool_rounds,
            history=previous,
        )
        state: CoachState = {
            "messages": build_messages(question, summary, history, system_prompt, day_notice=previous.day_notice),
            "evidence": [],
            "tool_rounds": 0,
            "finish_reason": None,
            "blocked_by": None,
            "answer_fallback": None,
            "answer_rechecks": 0,
            "unsupported_rechecks": 0,
            "exercise_rechecks": 0,
            "hedged_sentences": 0,
            "marker_cleanups": 0,
        }
        final = await GRAPH.ainvoke(state, context=runtime, config={"metadata": {"request_id": ctx.request_id}})
        reason: FinishReason = final["finish_reason"] or "stop"
        await events.done(message_id, "length" if reason == "length" else "stop")
        return CoachResult(
            answer=events.answer,
            # 고정 답에는 근거가 없다. 약 답을 고정 답으로 바꾸기 전에 조회한 기록도 근거로 저장하지 않는다
            evidence=[] if reason == "guard" else final["evidence"],
            finish_reason=reason,
            blocked_by=final["blocked_by"],
            tool_rounds=final["tool_rounds"],
            history_usage=_last_sent(runtime),
            messages=final["messages"],
            answer_rechecks=final["answer_rechecks"],
            hedged_sentences=final["hedged_sentences"],
            marker_cleanups=final["marker_cleanups"],
            unsupported_rechecks=final["unsupported_rechecks"],
            exercise_rechecks=final["exercise_rechecks"],
        )
    except Exception as e:
        code, message, retryable, metrics = _error_fields(e)
        # done 을 내보내다 sink 가 실패했으면 이미 종료한 것이라 error 를 더 내보내지 않는다
        if not events.finished:
            # sink 가 실패하면 더 보낼 곳이 없다. 그래도 예외를 밖으로 발생시키지 않는다
            with suppress(Exception):
                await events.error(code, message, retryable, metrics)
        return CoachResult(
            answer=events.answer,
            evidence=[],
            finish_reason="error",
            error_code=code,
            history_usage=_last_sent(runtime),
            exception=e,
        )
    finally:
        current_request_id.reset(token)
