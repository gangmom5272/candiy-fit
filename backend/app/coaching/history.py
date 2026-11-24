"""내부 요약 저장 계약과 저장 전 범위 검증을 정의한다.

토큰 계수·대상 선정·요약 생성 정책은 여기서 구현하지 않는다.
summary_adapter에서 coach_ai.history와 이 불변 값을 변환한다.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """정상 완료한 문답·서버 식별자와 질문 접수일(KST)을 보존한다."""

    request_id: str
    question_message_id: str
    answer_message_id: str
    question_seq: int
    answer_seq: int
    question: str
    answer: str
    answered_on: date | None = None


@dataclass(frozen=True, slots=True)
class StoredSummary:
    """저장된 누적 요약과 마지막으로 포함된 답변 번호를 보관한다."""

    text: str
    until_seq: int


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """동일 읽기 기준의 요약·전체 완료 쌍과 미종결 경계를 보관한다."""

    summary: StoredSummary | None
    turns: tuple[HistoryTurn, ...]
    before_sequence_no: int
    pending_sequence_no: int | None = None


@dataclass(frozen=True, slots=True)
class SummaryPlan:
    """AI가 선택한 오래된 완료 쌍의 앞부분과 정책 버전을 고정한다."""

    targets: tuple[HistoryTurn, ...]
    until_seq: int
    unsummarized_tokens: int
    policy_version: str


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """AI가 검증한 요약문·토큰 수와 고정 계획의 범위를 전달한다."""

    text: str
    until_seq: int
    tokens: int
    policy_version: str


class SummaryValidationError(ValueError):
    """비어 있거나 위조된 계획·결과를 저장 전에 거부한다."""


def _positive_int(value: object) -> bool:
    """bool을 제외한 양의 정수만 서버 순번으로 허용한다."""
    return type(value) is int and value > 0


def validate_snapshot(snapshot: HistorySnapshot) -> None:
    """요약 경계 이후의 완전한 쌍·시간순·고유 식별자를 확인한다."""
    if not _positive_int(snapshot.before_sequence_no):
        raise SummaryValidationError("invalid history cutoff")
    previous = 0
    if snapshot.summary is not None:
        if (
            not isinstance(snapshot.summary, StoredSummary)
            or not isinstance(snapshot.summary.text, str)
            or not snapshot.summary.text.strip()
            or not _positive_int(snapshot.summary.until_seq)
            or snapshot.summary.until_seq >= snapshot.before_sequence_no
        ):
            raise SummaryValidationError("invalid stored summary")
        previous = snapshot.summary.until_seq
    if snapshot.pending_sequence_no is not None and (
        not _positive_int(snapshot.pending_sequence_no)
        or not previous < snapshot.pending_sequence_no < snapshot.before_sequence_no
    ):
        raise SummaryValidationError("invalid pending boundary")
    requests: set[str] = set()
    messages: set[str] = set()
    for turn in snapshot.turns:
        if (
            not isinstance(turn, HistoryTurn)
            or not _positive_int(turn.question_seq)
            or not _positive_int(turn.answer_seq)
            or not previous
            < turn.question_seq
            < turn.answer_seq
            < snapshot.before_sequence_no
            or not all(
                isinstance(value, str) and value
                for value in (
                    turn.request_id,
                    turn.question_message_id,
                    turn.answer_message_id,
                )
            )
            or not isinstance(turn.question, str)
            or not isinstance(turn.answer, str)
            or (turn.answered_on is not None and type(turn.answered_on) is not date)
            or turn.request_id in requests
            or turn.question_message_id in messages
            or turn.answer_message_id in messages
            or turn.question_message_id == turn.answer_message_id
        ):
            raise SummaryValidationError("invalid completed pair")
        requests.add(turn.request_id)
        messages.update((turn.question_message_id, turn.answer_message_id))
        previous = turn.answer_seq


def validate_plan(
    snapshot: HistorySnapshot, plan: SummaryPlan, policy_version: str
) -> None:
    """계획이 원본과 동일한 오래된 완료 쌍의 비어 있지 않은 앞부분인지 확인한다."""
    validate_snapshot(snapshot)
    if (
        not isinstance(plan, SummaryPlan)
        or type(plan.targets) is not tuple
        or not plan.targets
        or plan.targets != snapshot.turns[: len(plan.targets)]
        or not _positive_int(plan.until_seq)
        or plan.until_seq != plan.targets[-1].answer_seq
        or type(plan.unsummarized_tokens) is not int
        or plan.unsummarized_tokens < 0
        or not isinstance(policy_version, str)
        or not policy_version.strip()
        or plan.policy_version != policy_version
        or (
            snapshot.pending_sequence_no is not None
            and plan.until_seq >= snapshot.pending_sequence_no
        )
    ):
        raise SummaryValidationError("invalid summary plan")
    # dataclass 동등성의 True == 1, 2.0 == 2 우회를 막도록 대상 자체의 타입도 확인한다.
    validate_snapshot(
        HistorySnapshot(snapshot.summary, plan.targets, snapshot.before_sequence_no)
    )


def validate_result(plan: SummaryPlan, result: SummaryResult) -> None:
    """고정 계획의 범위·정책과 비어 있지 않은 800토큰 이하 결과인지 확인한다."""
    if (
        not isinstance(result, SummaryResult)
        or not isinstance(result.text, str)
        or not result.text.strip()
        or not _positive_int(result.until_seq)
        or result.until_seq != plan.until_seq
        or result.policy_version != plan.policy_version
        or type(result.tokens) is not int
        or not 0 <= result.tokens <= 800
    ):
        raise SummaryValidationError("invalid summary result")
