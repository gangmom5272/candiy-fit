"""실제 AI 이력·계획·생성을 백엔드 저장 및 작업 관리 타입에 연결한다."""

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, get_args

from langchain_core.language_models import BaseChatModel

from app.coaching.history import HistoryTurn, StoredSummary, SummaryPlan, SummaryResult
from coach_ai.history.summary import generate_summary as ai_generate_summary
from coach_ai.history.summary import plan_summary as ai_plan_summary
from coach_ai.history.tokens import TOKEN_POLICY_VERSION
from coach_ai.history.types import HistoryTurn as AIHistoryTurn
from coach_ai.history.types import StoredSummary as AIStoredSummary
from coach_ai.history.types import SummaryError as AISummaryError
from coach_ai.history.types import SummaryPlan as AISummaryPlan
from coach_ai.history.types import SummaryResult as AISummaryResult

SummaryErrorCode = Literal[
    "llm_unavailable",
    "rate_limited",
    "timeout",
    "truncated",
    "invalid_format",
    "input_too_large",
    "config",
]


class SummaryError(Exception):
    """AI 오류의 안전한 분류와 재시도 가능 여부만 작업 관리에 전달한다."""

    def __init__(
        self,
        code: SummaryErrorCode,
        *,
        retryable: bool,
        retry_after: float | None = None,
    ) -> None:
        """일시적 오류 세 종류만 재시도를 허용하고 대기 값의 유효성을 확인한다."""
        if (
            code not in get_args(SummaryErrorCode)
            or type(retryable) is not bool
            or (
                retryable and code not in {"llm_unavailable", "rate_limited", "timeout"}
            )
            or (
                retry_after is not None
                and (
                    type(retry_after) not in {int, float}
                    or not math.isfinite(retry_after)
                    or retry_after < 0
                )
            )
        ):
            raise ValueError("invalid summary error classification")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class SummaryFunctions:
    """정책 버전·계획 함수·비동기 생성 함수를 명시적으로 주입한다."""

    policy_version: str
    plan_summary: Callable[
        [StoredSummary | None, Sequence[HistoryTurn]], SummaryPlan | None
    ]
    generate_summary: Callable[
        [StoredSummary | None, SummaryPlan], Awaitable[SummaryResult]
    ]


class GenerateSummary(Protocol):
    """AI의 키워드 전용 공유 모델 인자를 포함하는 생성 함수 계약이다."""

    async def __call__(
        self, plan: AISummaryPlan, *, model: BaseChatModel
    ) -> AISummaryResult:
        """기존 누적 요약이 포함된 계획과 답변 모델로 생성 결과를 반환한다."""
        ...


def to_ai_summary(summary: StoredSummary | None) -> AIStoredSummary | None:
    """빈 요약은 None으로, 저장된 원문과 기준점은 보정 없이 AI 타입으로 옮긴다."""
    if summary is None:
        return None
    return AIStoredSummary(text=summary.text, until_seq=summary.until_seq)


def to_ai_turn(turn: HistoryTurn) -> AIHistoryTurn:
    """완료 쌍의 서버 식별자·순번·원문·접수일을 손실 없이 AI 타입으로 옮긴다."""
    return AIHistoryTurn(
        request_id=turn.request_id,
        question_message_id=turn.question_message_id,
        answer_message_id=turn.answer_message_id,
        question_seq=turn.question_seq,
        answer_seq=turn.answer_seq,
        question=turn.question,
        answer=turn.answer,
        answered_on=turn.answered_on,
    )


def ai_summary_functions(
    *,
    model_provider: Callable[[], BaseChatModel],
    generate: GenerateSummary = ai_generate_summary,
) -> SummaryFunctions:
    """실제 계획·생성 함수를 연결하고 호출 시 준비된 답변 모델을 공유한다."""

    def plan_summary(
        summary: StoredSummary | None, turns: Sequence[HistoryTurn]
    ) -> SummaryPlan | None:
        """AI가 정한 대상·토큰 수·정책을 백엔드 범위 검증용 계획으로 옮긴다."""
        plan = ai_plan_summary(
            to_ai_summary(summary), tuple(to_ai_turn(turn) for turn in turns)
        )
        if plan is None:
            return None
        return SummaryPlan(
            targets=tuple(
                HistoryTurn(
                    request_id=turn.request_id,
                    question_message_id=turn.question_message_id,
                    answer_message_id=turn.answer_message_id,
                    question_seq=turn.question_seq,
                    answer_seq=turn.answer_seq,
                    question=turn.question,
                    answer=turn.answer,
                    answered_on=turn.answered_on,
                )
                for turn in plan.targets
            ),
            until_seq=plan.until_seq,
            unsummarized_tokens=plan.unsummarized_tokens,
            policy_version=plan.policy_version,
        )

    async def generate_summary(
        summary: StoredSummary | None, plan: SummaryPlan
    ) -> SummaryResult:
        """고정 계획에 기존 요약과 같은 답변 모델을 연결하고 AI 오류만 안전하게 변환한다."""
        ai_plan = AISummaryPlan(
            summary=to_ai_summary(summary),
            targets=tuple(to_ai_turn(turn) for turn in plan.targets),
            until_seq=plan.until_seq,
            unsummarized_tokens=plan.unsummarized_tokens,
            policy_version=plan.policy_version,
        )
        try:
            result = await generate(ai_plan, model=model_provider())
        except AISummaryError as error:
            raise SummaryError(error.code, retryable=error.retryable) from error
        return SummaryResult(
            text=result.text,
            until_seq=result.until_seq,
            tokens=result.tokens,
            policy_version=result.policy_version,
        )

    return SummaryFunctions(TOKEN_POLICY_VERSION, plan_summary, generate_summary)


def default_summary_functions(
    *, model_provider: Callable[[], BaseChatModel]
) -> SummaryFunctions:
    """설정·네트워크 접근 없이 실제 AI 요약을 기본 활성화한다."""
    return ai_summary_functions(model_provider=model_provider)
