"""LLM 호출마다 이전 대화를 예산(history.budget)에 맞춰 요청에 넣는다."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage

from coach_ai.agent.prompts import system_with_history
from coach_ai.history.budget import OUTPUT_TOKENS, fit_history
from coach_ai.history.tokens import MESSAGE_TOKENS, count_tokens, count_tool_definition_tokens
from coach_ai.history.types import HistoryTurn, HistoryUsage, StoredSummary


@dataclass(frozen=True, slots=True, kw_only=True)
class PreviousConversation:
    """CoachContext 로 받은 이전 대화와, 요약을 붙이기 전의 시스템 프롬프트.

    요청 메시지는 시스템 메시지, turns 의 질문·답변, 이번 질문 순서이고 그 뒤에 도구 왕복 메시지가 붙는다.
    day_notice 는 받은 쌍에 오늘과 다른 날 질문한 쌍이 있을 때 요약 뒤에 붙이는 안내다 (prompts.other_day_notice).
    """

    system_prompt: str
    summary: StoredSummary | None = None
    turns: tuple[HistoryTurn, ...] = ()
    day_notice: str | None = None


def count_message_tokens(message: AnyMessage) -> int:
    """메시지 하나의 추정 토큰 수. 채팅 템플릿 표시, 본문, 도구 호출 이름과 인자를 센다."""
    tokens = MESSAGE_TOKENS + (count_tokens(message.content) if isinstance(message.content, str) else 0)
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            tokens += count_tokens(call["name"] + json.dumps(call["args"], ensure_ascii=False))
        for bad in message.invalid_tool_calls:
            tokens += count_tokens((bad.get("name") or "") + (bad.get("args") or ""))
    return tokens


def reserved_output_tokens(model: Any) -> int:
    """답변 요청의 출력 예약. 모델이 요청에 넣는 최대 출력(max_tokens)과 같고, 넣지 않으면 OUTPUT_TOKENS 다."""
    max_tokens = getattr(model, "max_tokens", None)
    return max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else OUTPUT_TOKENS


def count_request_tokens(messages: Sequence[AnyMessage], tools: Sequence[Mapping[str, Any]] | None = None) -> int:
    """LLM 요청 하나의 추정 입력 토큰 수. tools 는 요청에 묶는 도구 정의이고, 없으면 세지 않는다."""
    tokens = sum(map(count_message_tokens, messages))
    if tools:
        tokens += count_tool_definition_tokens(tools)
    return tokens


def fit_request(
    messages: Sequence[AnyMessage],
    previous: PreviousConversation,
    *,
    tools: Sequence[Mapping[str, Any]] | None,
    output_tokens: int = OUTPUT_TOKENS,
) -> tuple[list[AnyMessage], HistoryUsage]:
    """예산에 맞춰 이전 대화를 줄인 요청 메시지와, 그 요청에 넣은 이전 대화의 양을 반환한다.

    output_tokens 는 그 요청의 출력 예약(reserved_output_tokens)이다.
    줄일 것이 없으면 messages 를 그대로 반환한다. 줄이면 시스템 메시지를 요약 유무와 안내에 맞게 다시 만든다.
    이전 대화를 모두 빼도 넘으면 InputTooLargeError 를 발생시킨다.
    """
    n = len(previous.turns)
    history, rest = list(messages[1 : 1 + 2 * n]), list(messages[1 + 2 * n :])
    summary = None if previous.summary is None else previous.summary.text

    def system(summary_used: bool, omitted: bool) -> str:
        used = summary if summary_used else None
        return system_with_history(previous.system_prompt, used, omitted=omitted, day_notice=previous.day_notice)

    fit = fit_history(
        previous.turns,
        rest_tokens=count_request_tokens(rest, tools),
        system_tokens=lambda used, omitted: MESSAGE_TOKENS + count_tokens(system(used, omitted)),
        has_summary=summary is not None,
        output_tokens=output_tokens,
    )
    usage = HistoryUsage.of(
        previous.summary if fit.summary_used else None, previous.turns[fit.first_turn :], received_turns=n
    )
    if not fit.omitted:
        return list(messages), usage
    head = SystemMessage(content=system(fit.summary_used, True))
    return [head, *history[2 * fit.first_turn :], *rest], usage
