"""대화 API 와 에이전트가 주고받는 이전 대화 타입.

대화 API 는 누적 요약(StoredSummary)과 그 뒤의 정상 완료 질문–답변 쌍(HistoryTurn, 오래된 순)을 넘긴다.
순서 번호는 coaching_message.sequence_no 다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, TypeGuard, get_args

from coach_ai.history.tokens import TOKEN_POLICY_VERSION, count_tokens

SummaryErrorCode = Literal[
    "llm_unavailable", "rate_limited", "timeout", "truncated", "invalid_format", "input_too_large", "config"
]
# 같은 요약 대상으로 다시 시도하면 될 수 있는 실패
RETRYABLE_SUMMARY_ERRORS: frozenset[SummaryErrorCode] = frozenset({"llm_unavailable", "rate_limited", "timeout"})


def _is_int(value: object) -> TypeGuard[int]:
    # bool 은 int 의 하위 타입이지만 순서 번호·개수로 받지 않는다
    return isinstance(value, int) and not isinstance(value, bool)


def _require_positive(name: str, value: object) -> None:
    if not _is_int(value) or value < 1:
        raise ValueError(f"{name}은 양의 정수여야 한다")


def _require_non_negative(name: str, value: object) -> None:
    if not _is_int(value) or value < 0:
        raise ValueError(f"{name}은 0 이상의 정수여야 한다")


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}은 비어 있지 않은 문자열이어야 한다")


def _require_summary(text: object, until_seq: object) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text는 비어 있지 않은 문자열이어야 한다")
    _require_positive("until_seq", until_seq)


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryTurn:
    """정상 완료된 요청 하나의 질문과 답변 원문. 답변은 빈 문자열일 수 있다.

    request_id·question_message_id·answer_message_id 는 coaching_request.id·coaching_message.id 이고, 대화 API 가
    요청·메시지를 찾을 때 쓴다. LLM 요청과 도구 메시지, 근거, HistoryUsage, 예외 문구에는 넣지 않는다.
    answered_on 은 그 요청에서 run_coach 에 넘긴 now 의 KST 날짜다. 백엔드는 사용자가 질문한 시각을 넘긴다.
    요약 입력에는 쌍마다 넣는다. 답변 요청에는 날짜를 넣지 않고, 오늘과 다른 날짜가 있으면 시스템 메시지에 다른 날
    대화 안내(prompts.other_day_notice)만 붙인다. 이벤트, 근거, HistoryUsage 에는 넣지 않으며 tokens 에도 세지 않는다.
    """

    request_id: str
    question_message_id: str
    answer_message_id: str
    question_seq: int
    answer_seq: int
    question: str
    answer: str
    answered_on: date | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "question_message_id", "answer_message_id"):
            _require_text(name, getattr(self, name))
        _require_positive("question_seq", self.question_seq)
        _require_positive("answer_seq", self.answer_seq)
        if self.answer_seq <= self.question_seq:
            raise ValueError("answer_seq는 question_seq보다 커야 한다")
        for name in ("question", "answer"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name}은 문자열이어야 한다")
        # datetime 은 date 의 하위 타입이지만 시간대에 따라 KST 날짜가 달라지므로 받지 않는다
        day = self.answered_on
        if day is not None and (not isinstance(day, date) or isinstance(day, datetime)):
            raise ValueError("answered_on은 date 또는 None이어야 한다")

    @property
    def tokens(self) -> int:
        return count_tokens(self.question) + count_tokens(self.answer)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredSummary:
    """대화 API 가 저장한 누적 요약 (coaching_conversation.summary, summary_until_seq).

    until_seq 는 요약에 포함된 마지막 메시지의 순서 번호다.
    """

    text: str
    until_seq: int

    def __post_init__(self) -> None:
        _require_summary(self.text, self.until_seq)


def validate_history(summary: StoredSummary | None, turns: tuple[HistoryTurn, ...]) -> None:
    """쌍은 요약 뒤에 순서 번호 오름차순으로 오고, 서로 겹치지 않아야 한다. 어긋나면 ValueError 를 발생시킨다."""
    if summary is not None and not isinstance(summary, StoredSummary):
        raise ValueError("summary는 StoredSummary 또는 None이어야 한다")
    if not isinstance(turns, tuple) or not all(isinstance(turn, HistoryTurn) for turn in turns):
        raise ValueError("turns는 HistoryTurn 튜플이어야 한다")
    last = 0 if summary is None else summary.until_seq
    for turn in turns:
        if turn.question_seq <= last:
            raise ValueError("turns는 요약 뒤의 쌍을 순서 번호 오름차순으로 겹치지 않게 담아야 한다")
        last = turn.answer_seq


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryUsage:
    """마지막 LLM 호출에 넣은 이전 대화의 양.

    included_turns·history_tokens 는 넣은 원문 질문–답변 쌍만 센다. 이번 질문과 요약은 세지 않는다.
    excluded_turns 는 받은 쌍 중 넣지 않은 쌍 수다.
    summary_tokens 는 넣은 요약의 토큰 수이고, 요약을 넣지 않았으면 0이다.
    """

    summary_used: bool
    included_turns: int
    excluded_turns: int
    history_tokens: int
    summary_tokens: int
    token_policy_version: str = TOKEN_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.summary_used, bool):
            raise ValueError("summary_used는 bool이어야 한다")
        for name in ("included_turns", "excluded_turns", "history_tokens", "summary_tokens"):
            _require_non_negative(name, getattr(self, name))
        _require_text("token_policy_version", self.token_policy_version)

    @classmethod
    def of(cls, summary: StoredSummary | None, turns: Sequence[HistoryTurn], *, received_turns: int) -> HistoryUsage:
        """프롬프트에 넣는 요약과 쌍의 양. received_turns 는 받은 쌍 수다.

        요약은 프롬프트에 들어가는 대로 앞뒤 공백을 빼고 센다.
        """
        return cls(
            summary_used=summary is not None,
            included_turns=len(turns),
            excluded_turns=received_turns - len(turns),
            history_tokens=sum(turn.tokens for turn in turns),
            summary_tokens=0 if summary is None else count_tokens(summary.text.strip()),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SummaryPlan:
    """요약 대상. 새 누적 요약은 summary(기존 누적 요약)와 targets(대상 쌍, 오래된 순)를 합쳐 만든다.

    until_seq 는 새 요약에 포함되는 마지막 메시지 번호이고 targets 마지막 쌍의 answer_seq 와 같아야 한다.
    unsummarized_tokens 는 대상만이 아니라 받은 미요약 쌍 전체의 토큰 합이다.
    """

    summary: StoredSummary | None
    targets: tuple[HistoryTurn, ...]
    until_seq: int
    unsummarized_tokens: int
    policy_version: str = TOKEN_POLICY_VERSION

    def __post_init__(self) -> None:
        validate_history(self.summary, self.targets)
        if not self.targets:
            raise ValueError("요약 대상 쌍이 하나 이상이어야 한다")
        _require_positive("until_seq", self.until_seq)
        if self.until_seq != self.targets[-1].answer_seq:
            raise ValueError("until_seq는 targets 마지막 쌍의 answer_seq여야 한다")
        _require_non_negative("unsummarized_tokens", self.unsummarized_tokens)
        _require_text("policy_version", self.policy_version)


@dataclass(frozen=True, slots=True, kw_only=True)
class SummaryResult:
    """새 누적 요약. 대화 API 가 coaching_conversation.summary·summary_until_seq 로 저장한다.

    until_seq 는 SummaryPlan.until_seq 이고, LLM 출력의 숫자를 쓰지 않는다.
    tokens 는 policy_version 방식으로 센 text 의 토큰 수다.
    """

    text: str
    until_seq: int
    tokens: int
    policy_version: str = TOKEN_POLICY_VERSION

    def __post_init__(self) -> None:
        _require_summary(self.text, self.until_seq)
        _require_non_negative("tokens", self.tokens)
        _require_text("policy_version", self.policy_version)


class SummaryError(Exception):
    """요약 생성 실패. retryable 이면 같은 요약 대상으로 다시 시도하면 될 수 있다.

    예외 문구는 code 뿐이고 요약·대화 원문을 담지 않는다.
    """

    def __init__(self, code: SummaryErrorCode) -> None:
        if code not in get_args(SummaryErrorCode):
            raise ValueError(f"알 수 없는 요약 오류 코드: {code!r}")
        super().__init__(code)
        self.code: SummaryErrorCode = code
        self.retryable = code in RETRYABLE_SUMMARY_ERRORS
