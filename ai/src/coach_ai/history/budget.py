"""LLM 호출 하나의 토큰 예산. 값은 대화 API 와 합의한 배분이다.

컨텍스트 상한은 입력과 출력의 합이다. 호출마다 추정 입력 + OUTPUT_TOKENS + SAFETY_MARGIN 이 CONTEXT_TOKENS 를
넘지 않아야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from coach_ai.history.tokens import MESSAGE_TOKENS
from coach_ai.history.types import HistoryTurn

CONTEXT_TOKENS = 16_384
# 답변 몫. 답변 요청의 최대 출력(max_tokens)이 있으면 그 값을 예약하고(agent.budget.reserved_output_tokens),
# 없을 때만 이 값을 쓴다. 최대 출력이 없으면 서버가 남은 컨텍스트만큼 생성할 수 있어 합의한 답변 몫을 남긴다
OUTPUT_TOKENS = 1_000
# 보정한 추정으로도 실제보다 적게 셀 수 있는 몫. 근거는 docs/benchmarks/2026-10-28-token-estimate.md 에 있다
SAFETY_MARGIN = 512


def input_tokens_limit(output_tokens: int) -> int:
    """출력 예약이 output_tokens 일 때 호출 하나의 추정 입력 상한."""
    return CONTEXT_TOKENS - output_tokens - SAFETY_MARGIN


# 출력 예약이 OUTPUT_TOKENS 일 때의 추정 입력 상한 (14,872)
INPUT_TOKENS_LIMIT = input_tokens_limit(OUTPUT_TOKENS)

# 기본 배분. 배분 합(12,512)과 CONTEXT_TOKENS 사이의 여유는 이번 질문과, 도구 결과가 TOOL_RESULT_RESERVE_TOKENS 를
# 넘을 때 쓴다. SYSTEM_TOKENS 와 TOOL_RESULT_RESERVE_TOKENS 는 설계 배분이라 예산 검사에 쓰지 않고, 검사는 호출마다
# 실제 시스템 프롬프트·도구 정의·도구 결과를 센다 (agent/budget.py 의 fit_request)
SYSTEM_TOKENS = 5_500  # 시스템 프롬프트·도구 정의·채팅 템플릿. 버전별 실제 값은 docs/agent-interface.md 4.5
SUMMARY_MAX_TOKENS = 800
RECENT_TURNS_MAX_TOKENS = 2_400
TOOL_RESULT_RESERVE_TOKENS = 2_300

# 요약 호출 하나의 추정 입력 상한 (요약 지시·입력 형식, 기존 요약, 대상 원문). 출력은 SUMMARY_MAX_TOKENS 를 요청에
# 넣고, SAFETY_MARGIN 과 더해도 CONTEXT_TOKENS 안이다
SUMMARY_INPUT_MAX_TOKENS = 9_800

INPUT_TOO_LARGE_MESSAGE = "질문과 조회한 기록이 너무 길어 답변을 만들 수 없어요. 질문을 나눠서 다시 물어봐 주세요."


class InputTooLargeError(Exception):
    """이전 대화를 모두 빼도 LLM 호출 하나의 추정 입력이 추정 입력 상한(limit)을 넘는다.

    다시 보내도 같은 결과라 재시도할 수 없다. 예외 문구는 토큰 수뿐이고 대화 원문을 담지 않는다.
    """

    code = "input_too_large"
    retryable = False
    message = INPUT_TOO_LARGE_MESSAGE

    def __init__(self, tokens: int, limit: int) -> None:
        super().__init__(f"input_too_large: 추정 입력 {tokens} > {limit}")
        self.tokens = tokens
        self.limit = limit


@dataclass(frozen=True, slots=True)
class HistoryFit:
    """호출 하나에 넣을 이전 대화. 쌍은 turns[first_turn:] 을 넣고, summary_used 이면 요약을 넣는다.

    omitted 는 받은 쌍이나 요약 중 넣지 않은 것이 있는지다. tokens 는 고른 구성의 추정 입력이다.
    """

    first_turn: int
    summary_used: bool
    omitted: bool
    tokens: int


def fit_history(
    turns: Sequence[HistoryTurn],
    *,
    rest_tokens: int,
    system_tokens: Callable[[bool, bool], int],
    has_summary: bool,
    output_tokens: int = OUTPUT_TOKENS,
) -> HistoryFit:
    """호출 하나에 넣을 이전 대화를 고른다. 추정 입력이 출력 예약에 맞춘 상한 이하가 되는 첫 구성을 반환한다.

    쌍은 최신 쌍부터 본문 토큰 합이 RECENT_TURNS_MAX_TOKENS 를 넘지 않는 데까지만 넣는다. 추정 입력이 넘으면
    오래된 쌍부터 질문과 답변을 함께 빼고, 쌍을 모두 빼도 넘으면 요약을 뺀다. 그래도 넘으면 InputTooLargeError 를
    발생시킨다.
    system_tokens(summary_used, omitted) 는 요약과 뺀 이전 대화 안내를 붙인 시스템 메시지의 토큰 수다.
    rest_tokens 는 시스템 메시지와 이전 대화 쌍을 뺀 나머지(이번 질문, 도구 왕복 메시지, 도구 정의)의 토큰 수다.
    output_tokens 는 그 호출의 출력 예약이고, 상한은 input_tokens_limit(output_tokens) 다.
    """
    limit = input_tokens_limit(output_tokens)
    start = len(turns)
    kept = 0
    for i in range(len(turns) - 1, -1, -1):
        kept += turns[i].tokens
        if kept > RECENT_TURNS_MAX_TOKENS:
            break
        start = i

    def total(first_turn: int, summary_used: bool) -> HistoryFit:
        omitted = first_turn > 0 or (has_summary and not summary_used)
        pairs = sum(turn.tokens + 2 * MESSAGE_TOKENS for turn in turns[first_turn:])
        return HistoryFit(first_turn, summary_used, omitted, system_tokens(summary_used, omitted) + pairs + rest_tokens)

    order = [(first_turn, has_summary) for first_turn in range(start, len(turns) + 1)]
    if has_summary:
        order.append((len(turns), False))
    for first_turn, summary_used in order:
        fit = total(first_turn, summary_used)
        if fit.tokens <= limit:
            return fit
    raise InputTooLargeError(fit.tokens, limit)
