from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langgraph.runtime import Runtime

from coach_ai.agent.budget import fit_request, reserved_output_tokens
from coach_ai.agent.executor import tool_specs
from coach_ai.agent.rules.drug_answer import find_leak
from coach_ai.agent.rules.effect_hedge import EffectHedge, hedge_text
from coach_ai.agent.rules.exercise_effect import asks_exercise_effect
from coach_ai.agent.rules.internal_markers import MarkerFilter, clean, has_markers
from coach_ai.agent.rules.reused_numbers import HealthValue, reused_values, unsupported_values
from coach_ai.agent.rules.sleep_grounding import (
    fatigue_claim,
    grounded_answer,
    normalize_calls,
    pattern_result,
    signals_answer,
    signals_question,
    sleep_closing,
    sleep_plan_followup,
    unqueried_sleep_clock,
)
from coach_ai.agent.runtime import CoachRuntime
from coach_ai.agent.state import CoachState
from coach_ai.llm.client import LLMEmptyResponseError

logger = logging.getLogger(__name__)

# 도구 왕복 한도에 걸려 도구 없이 보내는 호출에서만 마지막 도구 결과 끝에 덧붙인다. 시스템 메시지를 하나만 받는
# 템플릿 대비로 대화 중간 system 메시지는 쓰지 않는다
TOOL_LIMIT_NOTICE = (
    "도구를 더 호출할 수 없다. 지금까지 조회한 결과로 답하고, 확인하지 못한 내용은 확인할 수 없다고 말한다."
)
# 도구 왕복 한도에 걸린 호출에서 답변 글이 남지 않으면(도구 호출 표기만 온 경우 등) 대신 보내는 답변
TOOL_LIMIT_ANSWER = "질문을 한 번에 확인하지 못했어요. 나눠서 다시 물어봐 주세요."
# 도구 없이 끝난 첫 답이 앞 답의 건강 수치를 다시 써서 되돌릴 때, 다시 보내는 요청의 이번 질문 끝에만 덧붙인다
RECHECK_NOTICE = (
    "이 질문은 시스템 규칙에 맞는 도구로 다시 조회한 뒤 답한다. "
    "이전 답변은 틀릴 수 있으므로 수치뿐 아니라 조회 방식과 원인 설명도 그대로 따르지 않는다."
)
SLEEP_CLOCK_RECHECK_NOTICE = (
    "평균 취침·기상 시각은 아직 조회하지 않았다. 이번 질문의 기간으로 get_sleep_records의 pattern을 먼저 조회한다. "
    "새 수면 계획에 기간 지정이 없으면 period를 생략한다. 이번 결과에 있는 시각만 근거로 답한다."
)
NO_VERIFIED_SLEEP_CLOCK = (
    "수면 기록을 다시 확인하지 못해 취침·기상 시각을 안내하기 어려워요. 잠시 후 다시 질문해 주세요."
)
# 도구 없이 끝난 첫 답이 앞 답에도 질문에도 없는 건강 수치를 말해 되돌릴 때, 같은 자리에 덧붙인다
UNSUPPORTED_NOTICE = "기록 수치는 도구로 조회한 결과만 쓴다."
# 운동이 잠에 준 효과를 물은 질문에 도구 없이 끝난 첫 답을 되돌릴 때, 같은 자리에 덧붙인다
EXERCISE_EFFECT_NOTICE = (
    "운동이 잠에 준 영향은 아직 조회하지 않았다. get_sleep_records의 factors로 생활 요인 비교를 먼저 조회하고 "
    "그 결과로만 답한다. 질문에 기간이 없으면 period를 생략한다."
)


def _with_limit_notice(messages: list[AnyMessage]) -> list[AnyMessage]:
    """마지막 도구 결과 끝에 한도 안내를 덧붙인 요청용 목록을 반환한다.

    상태의 메시지는 바꾸지 않아 안내가 결과 메시지(CoachResult.messages)에 포함되지 않는다.
    한도가 0 이면 마지막 메시지가 도구 결과가 아니어서 그대로 반환한다.
    """
    last = messages[-1] if messages else None
    if not isinstance(last, ToolMessage) or not isinstance(last.content, str):
        return messages
    return [*messages[:-1], last.model_copy(update={"content": f"{last.content}\n\n{TOOL_LIMIT_NOTICE}"})]


def _with_recheck_notice(messages: list[AnyMessage], notice: str = RECHECK_NOTICE) -> list[AnyMessage]:
    """이번 질문(첫 호출의 마지막 메시지) 끝에 다시 조회 안내를 덧붙인 요청용 목록을 반환한다.

    상태의 메시지는 바꾸지 않는다. 기간 보정(tools_node)은 상태의 이번 질문을 읽어 안내의 영향을 받지 않는다.
    """
    last = messages[-1] if messages else None
    if not isinstance(last, HumanMessage) or not isinstance(last.content, str):
        return messages
    return [*messages[:-1], last.model_copy(update={"content": f"{last.content}\n\n{notice}"})]


def _is_empty(message: AIMessage) -> bool:
    text = message.content if isinstance(message.content, str) else ""
    return not clean(text).strip() and not (message.tool_calls or message.invalid_tool_calls)


def _blank_after_cleanup(message: AIMessage) -> bool:
    text = message.content if isinstance(message.content, str) else ""
    return has_markers(text) and _is_empty(message)


def _this_question(messages: Sequence[AnyMessage]) -> str:
    return next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")


def _answer_values(
    rt: CoachRuntime, messages: Sequence[AnyMessage], answer: str
) -> tuple[list[HealthValue], list[HealthValue]]:
    """도구 없이 끝난 답의 건강 수치 가운데 앞 답(이전 대화 쌍의 답과 요약)에 있던 값과, 앞 답에도 질문에도 없는 값.
    사용자가 말한 값은 둘 다에서 뺀다."""
    ctx = rt.ctx
    previous = [turn.answer for turn in ctx.turns] + ([] if ctx.summary is None else [ctx.summary.text])
    questions = [*(turn.question for turn in ctx.turns), _this_question(messages)]
    return reused_values(answer, previous, questions), unsupported_values(answer, previous, questions)


def _rechecks_used(state: CoachState) -> int:
    """앞 왕복까지 이 요청에서 쓴 되돌림 수."""
    return state.get("answer_rechecks", 0) + state.get("unsupported_rechecks", 0) + state.get("exercise_rechecks", 0)


def _log_recheck(rt: CoachRuntime, event: str, values: list[HealthValue]) -> None:
    # 답 원문과 수치는 로그에 넣지 않는다
    logger.warning(
        "coaching agent %s request_id=%s values=%d kinds=%s",
        event,
        rt.ctx.request_id,
        len(values),
        ",".join(sorted({kind for kind, _ in values})),
    )


async def agent_node(state: CoachState, runtime: Runtime[CoachRuntime]) -> dict[str, Any]:
    rt = runtime.context
    over_limit = state["tool_rounds"] >= rt.max_tool_rounds
    first = state["tool_rounds"] == 0
    # 약 일반 지식 질문이면 답 조각을 바로 보내지 않고 모아 두었다가 마지막 호출 뒤 검사한다
    fallback = state.get("answer_fallback")
    question = _this_question(state["messages"])
    claim = fallback is None and fatigue_claim(question)
    signals = fallback is None and signals_question(question)
    # 첫 호출은 답 조각을 모아 두고, 도구 없이 끝난 답이 조회하지 않은 건강 수치나 취침·기상 시각을 말하면 안내를
    # 붙여 한 번 다시 보낸다. 피로 질문, 운동이 잠에 준 효과를 물은 질문, 수면 계획 후속도 여기서 검사한다
    recheck = first and not over_limit and fallback is None
    await rt.events.status("thinking", "질문을 살펴보고 있어요" if first else "확인한 기록을 정리하고 있어요")
    specs = None if over_limit else tool_specs()
    # 표시 없는 도구 호출 표기("call:이름{")는 일반 답변과 구분하기 어려워 도구 없이 보내는 한도 호출에서만 제외한다
    model = rt.model.bind(drop_unmarked_tool_call=True) if over_limit else rt.model.bind_tools(specs)
    request = _with_limit_notice(state["messages"]) if over_limit else state["messages"]
    output = reserved_output_tokens(rt.model)
    hedge = EffectHedge()

    sent: list[str] = []
    held: list[str] = []
    rechecks = 0
    unsupported_rechecks = 0
    exercise_rechecks = 0
    # 내부 표시를 정리한 응답 수
    cleanups = 0

    async def emit(text: str) -> None:
        if not text:
            return
        if not sent:
            await rt.events.status("answering", "답변을 작성하고 있어요")
            await rt.events.evidence(state["evidence"])
        sent.append(text)
        await rt.events.token(text)

    async def call(messages: list[AnyMessage], *, hold: bool) -> AIMessage:
        nonlocal cleanups
        merged: AIMessageChunk | None = None
        tool_seen = False
        received: list[str] = []
        markers = MarkerFilter()
        async for chunk in model.astream(messages):
            merged = chunk if merged is None else merged + chunk
            tool_seen = tool_seen or bool(chunk.tool_call_chunks)
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text and not tool_seen:
                received.append(text)
                if fallback is not None:
                    rt.held.append(text)
                elif hold:
                    held.append(text)
                else:
                    await emit(hedge.feed(markers.feed(text)))
        if fallback is None and not hold:
            await emit(hedge.feed(markers.finish()))
            await emit(hedge.flush())
        if has_markers("".join(received)):
            cleanups += 1
            # 답 원문은 로그에 넣지 않는다
            logger.warning(
                "coaching agent internal markers removed request_id=%s tool_rounds=%d held=%s",
                rt.ctx.request_id,
                state["tool_rounds"],
                fallback is not None or hold,
            )
        if merged is None:
            return AIMessage(content="")
        if merged.usage_metadata:
            rt.events.add_usage(merged.usage_metadata["input_tokens"], merged.usage_metadata["output_tokens"])
        return message_chunk_to_message(merged)

    async def ask(messages: list[AnyMessage], *, hold: bool) -> AIMessage:
        nonlocal rechecks
        messages, usage = fit_request(messages, rt.history, tools=specs, output_tokens=output)
        # 호출이 실패해도 그 요청에 넣은 이전 대화의 양을 결과에 담도록 호출 전에 기록한다
        rt.history_sent.append(usage)
        mark = len(rt.held)
        message = await call(messages, hold=hold)
        used = _rechecks_used(state) + rechecks + unsupported_rechecks + exercise_rechecks
        if not over_limit and _blank_after_cleanup(message) and used == 0:
            # 내부 표시를 정리했더니 빈 답은 이어지는 질문의 다시 조회와 같은 방식으로 한 번 되돌린다.
            # 되돌림을 이미 썼으면 아래 빈 응답 규칙을 따른다
            logger.warning("coaching agent answer rechecked request_id=%s reason=internal_markers", rt.ctx.request_id)
            rechecks = 1
            held.clear()
            del rt.held[mark:]
            return await ask(_with_recheck_notice(state["messages"]), hold=hold)
        attempt = 1
        # 빈 응답은 같은 요청을 한 번 다시 보낸다. 한도 호출은 다시 보내지 않고 아래 안내로 끝낸다
        while not over_limit and _is_empty(message):
            # 앞 왕복에서 도구 호출 앞에 보낸 글은 대부분 "기록을 확인해 볼게요" 같은 머리말이라 화면에 있어도
            # 다시 보낸다. 다시 받은 답변은 그 글 뒤에 이어진다
            resend = attempt == 1
            logger.warning(
                "coaching agent empty response request_id=%s tool_rounds=%d attempt=%d finish_reason=%s resend=%s",
                rt.ctx.request_id,
                state["tool_rounds"],
                attempt,
                message.response_metadata.get("finish_reason"),
                resend,
            )
            if not resend:
                raise LLMEmptyResponseError()
            held.clear()
            del rt.held[mark:]
            rt.history_sent.append(usage)
            attempt += 1
            message = await call(messages, hold=hold)
        return message

    sleep_result = None if fallback is not None else pattern_result(state["messages"], question)
    message = await ask(request, hold=recheck or claim or signals)
    if recheck:
        wants_tools = bool(message.tool_calls or message.invalid_tool_calls)
        answer = clean("".join(held))
        reused, unsupported = ([], []) if wants_tools else _answer_values(rt, state["messages"], answer)
        sleep_clock = not wants_tools and unqueried_sleep_clock(answer, question)
        previous = (
            " ".join((rt.ctx.turns[-1].question, rt.ctx.turns[-1].answer))
            if rt.ctx.turns
            else (rt.ctx.summary.text if rt.ctx.summary is not None else "")
        )
        # 되돌림은 요청마다 한 번이고, 함께 걸리면 앞 답 수치(피로 질문·조회하지 않은 시각 포함) → 앞 답에도 질문에도
        # 없는 수치 → 운동이 잠에 준 효과 → 수면 계획 후속 순서로 하나만 따른다
        if sleep_clock and not (claim or signals) and sleep_closing(question):
            # 다짐에 모델이 덧붙인 미조회 시각은 버리고, 불필요한 조회도 시작하지 않는다.
            answer = "네, 편안한 밤 보내세요."
            held.clear()
            await emit(answer)
            message = message.model_copy(update={"content": "".join(sent)})
        elif sleep_clock and rechecks and not (claim or signals):
            # 내부 표시 정리 등으로 재시도를 이미 썼어도 미조회 시각은 보내지 않는다.
            held.clear()
            await emit(NO_VERIFIED_SLEEP_CLOCK)
            message = message.model_copy(update={"content": "".join(sent)})
        elif not rechecks and (reused or sleep_clock or (claim and not wants_tools)):
            _log_recheck(rt, "answer rechecked", reused)
            rechecks = 1
            held.clear()
            notice = SLEEP_CLOCK_RECHECK_NOTICE if sleep_clock else RECHECK_NOTICE
            message = await ask(_with_recheck_notice(state["messages"], notice), hold=True)
            if not (claim or signals) and not (message.tool_calls or message.invalid_tool_calls):
                answer = clean("".join(held))
                if unqueried_sleep_clock(answer, question):
                    answer = NO_VERIFIED_SLEEP_CLOCK
                for text in held if answer == "".join(held) else [answer]:
                    await emit(hedge.feed(text))
                await emit(hedge.flush())
                message = message.model_copy(update={"content": "".join(sent)})
        elif not rechecks and unsupported:
            _log_recheck(rt, "unsupported numbers rechecked", unsupported)
            unsupported_rechecks = 1
            held.clear()
            message = await ask(_with_recheck_notice(state["messages"], UNSUPPORTED_NOTICE), hold=claim or signals)
        elif not rechecks and not wants_tools and asks_exercise_effect(question, rt.ctx):
            # 수치 없이 조회하지 않은 운동 경향을 말한 답도 생활 요인 비교를 조회하게 한다. 답 원문은 로그에 넣지 않는다
            logger.warning("coaching agent exercise effect rechecked request_id=%s", rt.ctx.request_id)
            exercise_rechecks = 1
            held.clear()
            message = await ask(_with_recheck_notice(state["messages"], EXERCISE_EFFECT_NOTICE), hold=claim or signals)
        elif not rechecks and not wants_tools and sleep_plan_followup(question, previous):
            # 수치 없는 조언도 앞 수면 기록에 이어진 새 계획이면 이번 기록을 확인한다.
            held.clear()
            rechecks = 1
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_sleep_records",
                        "args": {"analysis": "pattern"},
                        "id": f"{rt.ctx.request_id}-sleep-plan",
                        "type": "tool_call",
                    }
                ],
            )
        elif not (claim or signals):
            # 도구 호출 앞의 글이나 검사를 지난 답은 모은 조각 그대로 보낸다. 내부 표시가 있었으면 정리한 답을 보낸다
            for text in held if answer == "".join(held) else [answer]:
                await emit(hedge.feed(text))
            await emit(hedge.flush())

    original_calls = bool(message.tool_calls)
    message = normalize_calls(message, question, messages=state["messages"], today=rt.today)
    if not (message.tool_calls or message.invalid_tool_calls) and (claim or signals):
        if claim:
            answer = grounded_answer(sleep_result)
        elif original_calls:
            # 앞 왕복에서 이미 얻은 signals와 겹치는 호출만 남았던 경우 추가 왕복 없이 종료한다.
            answer = signals_answer(state["messages"])
        else:
            answer = clean("".join(held))
        await emit(hedge.feed(answer))
        await emit(hedge.flush())
        message = message.model_copy(update={"content": "".join(sent)})

    if fallback is not None and (over_limit or not (message.tool_calls or message.invalid_tool_calls)):
        return await _send_checked(
            rt, state, message, fallback, over_limit=over_limit, rechecks=rechecks, cleanups=cleanups
        )
    counts = {
        "answer_rechecks": state.get("answer_rechecks", 0) + rechecks,
        "unsupported_rechecks": state.get("unsupported_rechecks", 0) + unsupported_rechecks,
        "exercise_rechecks": state.get("exercise_rechecks", 0) + exercise_rechecks,
        "hedged_sentences": state.get("hedged_sentences", 0) + hedge.changed,
        "marker_cleanups": state.get("marker_cleanups", 0) + cleanups,
    }
    wants_tools = bool(message.tool_calls or message.invalid_tool_calls)
    if over_limit and not "".join(sent).strip():
        # 한도 호출 뒤에는 왕복이 없어 빈 답변으로 끝나므로 안내를 답변 조각(token)으로 보낸다. 저장되는 답변은
        # token 을 이어 붙인 글이라 안내가 그대로 저장된다. 이미 보낸 공백 조각은 되돌릴 수 없어 그 뒤에 붙인다
        await emit(TOOL_LIMIT_ANSWER)
        message = message.model_copy(update={"content": "".join(sent)})
    elif (hedge.changed or cleanups) and not wants_tools:
        # 끝맺음을 바꾸거나 내부 표시를 정리한 답은 결과 메시지도 보낸 글과 같게 둔다
        message = message.model_copy(update={"content": "".join(sent)})
    update: dict[str, Any] = {"messages": [message], **counts}
    if over_limit or not wants_tools:
        finish = message.response_metadata.get("finish_reason") or "stop"
        update["finish_reason"] = "length" if finish == "length" else ("tool_limit" if over_limit else "stop")
    return update


async def _send_checked(
    rt: CoachRuntime,
    state: CoachState,
    message: AIMessage,
    fallback: str,
    *,
    over_limit: bool,
    rechecks: int,
    cleanups: int,
) -> dict[str, Any]:
    """모아 둔 답을 검사하고 보낸다. 복용 판단이 새는 표현이 하나라도 있으면 그 문장만 빼지 않고 답 전체를 고정
    답으로 바꾼다. 남은 문장의 앞뒤가 맞지 않거나 걸리지 않은 문장에 같은 뜻이 남을 수 있어서다.

    바꾼 답은 경계 확인 안내처럼 끝내고 근거를 보내지 않는다.
    """
    joined = "".join(rt.held)
    answer = clean(joined)
    if answer != joined:
        message = message.model_copy(update={"content": answer})
    leak = None
    hedged = 0
    counts = {
        "answer_rechecks": state.get("answer_rechecks", 0) + rechecks,
        "marker_cleanups": state.get("marker_cleanups", 0) + cleanups,
    }
    if over_limit and not answer.strip():
        # 한도 안내는 LLM 이 만든 글이 아닌 고정 문장이라 검사하지 않는다 ("한 번에"가 횟수 낱말에 걸린다)
        answer = TOOL_LIMIT_ANSWER
        message = message.model_copy(update={"content": answer})
    else:
        leak = find_leak(answer)
    if leak is not None:
        # 답 원문은 로그에 넣지 않는다
        logger.warning(
            "coaching agent drug answer replaced request_id=%s tool_rounds=%d leak=%s",
            rt.ctx.request_id,
            state["tool_rounds"],
            leak,
        )
        await rt.events.status("answering", "안내를 준비하고 있어요")
        await rt.events.token(fallback)
        return {
            "messages": [message.model_copy(update={"content": fallback})],
            "finish_reason": "guard",
            "blocked_by": "medication",
            **counts,
        }
    if answer != TOOL_LIMIT_ANSWER:
        answer, hedged = hedge_text(answer)
        if hedged:
            message = message.model_copy(update={"content": answer})
    await rt.events.status("answering", "답변을 작성하고 있어요")
    await rt.events.evidence(state["evidence"])
    await rt.events.token(answer)
    finish = message.response_metadata.get("finish_reason") or "stop"
    return {
        "messages": [message],
        "finish_reason": "length" if finish == "length" else ("tool_limit" if over_limit else "stop"),
        "hedged_sentences": state.get("hedged_sentences", 0) + hedged,
        **counts,
    }


def route_after_agent(state: CoachState) -> str:
    return "finalize" if state.get("finish_reason") else "tools"
