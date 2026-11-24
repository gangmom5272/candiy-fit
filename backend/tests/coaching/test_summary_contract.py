"""합성 완료 쌍으로 요약 계획·결과의 서버 검증 경계를 검사한다."""

from dataclasses import replace

import pytest

from app.coaching.history import (
    HistorySnapshot,
    HistoryTurn,
    SummaryPlan,
    SummaryResult,
    SummaryValidationError,
    validate_plan,
    validate_result,
)


def turn(sequence: int = 1) -> HistoryTurn:
    """식별자와 원문이 서로 구분되는 합성 질문·답변 쌍을 만든다."""
    return HistoryTurn(
        f"request-{sequence}",
        f"question-{sequence}",
        f"answer-{sequence}",
        sequence,
        sequence + 1,
        f"합성 질문 {sequence}",
        f"합성 답변 {sequence}",
    )


def test_plan_allows_failed_sequence_gap_but_requires_oldest_prefix() -> None:
    """실패 번호 공백은 허용하되 최신 쌍만 선택한 계획은 거부한다."""
    first, last = turn(), turn(5)
    snapshot = HistorySnapshot(None, (first, last), 7)
    plan = SummaryPlan((first, last), 6, 2500, "synthetic-v1")
    validate_plan(snapshot, plan, "synthetic-v1")
    with pytest.raises(SummaryValidationError):
        validate_plan(snapshot, replace(plan, targets=(last,)), "synthetic-v1")


@pytest.mark.parametrize("tokens", [True, -1, 801, 1.5, "10"])
def test_result_rejects_invalid_token_counts(tokens: object) -> None:
    """bool·음수·상한 초과·잘못된 타입의 토큰 수는 저장하지 않는다."""
    plan = SummaryPlan((turn(),), 2, 2500, "synthetic-v1")
    result = SummaryResult("합성 요약", 2, tokens, "synthetic-v1")
    with pytest.raises(SummaryValidationError):
        validate_result(plan, result)


def test_plan_cannot_cross_unfinished_request() -> None:
    """미종결 요청 다음 완료 쌍까지 요약하여 기준점을 전진시키지 않는다."""
    first, last = turn(), turn(5)
    snapshot = HistorySnapshot(None, (first, last), 7, pending_sequence_no=3)
    with pytest.raises(SummaryValidationError):
        validate_plan(snapshot, SummaryPlan((first, last), 6, 2500, "v1"), "v1")


@pytest.mark.parametrize(
    "changes",
    [
        {"targets": ()},
        {"targets": (turn(3),)},
        {"targets": (replace(turn(), answer="위조한 합성 답변"),)},
        {"targets": (replace(turn(), question_seq=True),)},
        {"targets": (replace(turn(), answer_seq=2.0),)},
        {"until_seq": True},
        {"until_seq": 4},
        {"unsummarized_tokens": True},
        {"unsummarized_tokens": -1},
        {"policy_version": "other"},
    ],
)
def test_plan_rejects_forged_fields(changes: dict[str, object]) -> None:
    """빈 계획·대상 변경·위조 본문·번호·정책을 생성 호출 전에 거부한다."""
    source = HistorySnapshot(None, (turn(),), 3)
    plan = replace(SummaryPlan((turn(),), 2, 2500, "v1"), **changes)
    with pytest.raises(SummaryValidationError):
        validate_plan(source, plan, "v1")
