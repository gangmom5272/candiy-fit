import json
from datetime import date
from typing import get_args

import pytest

from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import EvidenceDraft, ToolCall, ToolOutcome
from coach_ai.ports import (
    HealthDataError,
    HealthDataLimitError,
    HealthDataNotReadyError,
    HealthDataTargetNotFoundError,
    HealthDataUnavailableError,
)
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DataStatus, ToolGateway
from coach_ai.tools.outcomes import (
    DETAIL_FAILED_MESSAGE,
    STATE_FACTS,
    detail_failed,
    limit_exceeded,
    lookup_failure,
    state_fact,
    target_not_found,
)
from coach_ai.tools.results import PeriodSpan
from tests.fakes import FakeHealthData

TODAY = date(2026, 10, 15)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
SPAN = PeriodSpan(period="last_7_days", start=date(2026, 10, 9), end=TODAY, basis="수면 종료일(KST)")
SLEEP_RECORDS = ToolCall("c1", "get_sleep_records", {"period": "last_7_days"})
SLEEP_LIMIT = HealthDataLimitError(
    "SLEEP_SESSION_LIMIT_EXCEEDED", source="sleep_sessions", can_narrow=True, limit=1000, limit_unit="세션"
)
SAMPLE_NOT_FOUND = HealthDataTargetNotFoundError("EXERCISE_RECORD_NOT_FOUND", source="exercise_samples")
NARROW = "조회 범위의 기록이 한도를 넘었다. 기간을 줄이면 조회할 수 있다"
NOT_POSSIBLE = "기록이 많아 이 조회는 할 수 없다"
NOT_FOUND = "조회할 기록을 찾지 못했다"


def base_outcome() -> ToolOutcome:
    draft = EvidenceDraft("get_sleep_records", "sleeps", SPAN, "지난 7일 평균 수면 6시간", {"metric": "sleeps"})
    return ToolOutcome("ok", {"metric": "sleep", "nights": []}, (draft,))


async def run_one(handler, call: ToolCall = SLEEP_RECORDS):
    audit = MemoryAuditSink()
    gateway = ToolGateway(FakeHealthData(), TODAY, handlers={call.name: handler}, audit=audit)
    replies = await gateway.execute([call], CTX)
    return replies[0], audit.records()[0]


def test_limit_error_keeps_backend_code_and_lookup_facts():
    error = HealthDataLimitError("MEASUREMENT_RECORD_LIMIT_EXCEEDED", source="measurement_records", can_narrow=False)

    assert isinstance(error, HealthDataError)
    assert (error.code, error.source, error.can_narrow, error.limit, error.limit_unit) == (
        "MEASUREMENT_RECORD_LIMIT_EXCEEDED",
        "measurement_records",
        False,
        None,
        None,
    )
    assert error.retryable is False
    assert error.metrics == ()
    assert str(error) == "MEASUREMENT_RECORD_LIMIT_EXCEEDED"


def test_limit_error_takes_lookup_facts_by_keyword_only():
    assert (SLEEP_LIMIT.limit, SLEEP_LIMIT.limit_unit) == (1000, "세션")
    with pytest.raises(TypeError):
        HealthDataLimitError("SLEEP_SESSION_LIMIT_EXCEEDED", "sleep_sessions", True)


@pytest.mark.parametrize(("limit", "unit"), [(None, "세션"), (0, None), (-1, None), (True, None)])
def test_limit_error_rejects_unit_without_limit_and_non_positive_limit(limit, unit):
    with pytest.raises(ValueError):
        HealthDataLimitError("SLEEP_SESSION_LIMIT_EXCEEDED", source="sleep_sessions", can_narrow=True, limit=limit,
                             limit_unit=unit)  # fmt: skip


def test_target_not_found_error_keeps_backend_code_and_source():
    assert isinstance(SAMPLE_NOT_FOUND, HealthDataError)
    assert (SAMPLE_NOT_FOUND.code, SAMPLE_NOT_FOUND.source) == ("EXERCISE_RECORD_NOT_FOUND", "exercise_samples")
    assert SAMPLE_NOT_FOUND.retryable is False
    assert str(SAMPLE_NOT_FOUND) == "EXERCISE_RECORD_NOT_FOUND"
    with pytest.raises(TypeError):
        HealthDataTargetNotFoundError("EXERCISE_RECORD_NOT_FOUND", "exercise_samples")


def test_limit_that_can_narrow_says_so_with_known_limit_and_unit():
    outcome = limit_exceeded("get_sleep_records", SLEEP_LIMIT)

    assert outcome.status == "limit_exceeded"
    assert outcome.evidence == ()
    assert outcome.to_message([]) == {
        "error": "limit_exceeded",
        "tool": "get_sleep_records",
        "message": "조회 범위의 기록이 한도(1,000세션)를 넘었다. 기간을 줄이면 조회할 수 있다",
    }


def test_limit_unit_code_with_label_reads_as_korean_count():
    # 포트의 limit_unit 은 연동 쪽 코드 값("sessions") 그대로이고, 결과 문장에서만 이름표로 쓴다
    sessions = HealthDataLimitError(
        "SLEEP_SESSION_LIMIT_EXCEEDED", source="sleep_sessions", can_narrow=True, limit=1000, limit_unit="sessions"
    )
    assert sessions.limit_unit == "sessions"
    assert limit_exceeded("get_sleep_records", sessions).payload["message"] == (
        "조회 범위의 기록이 한도(수면 기록 1,000개)를 넘었다. 기간을 줄이면 조회할 수 있다"
    )
    # 이름표는 조회 종류와 단위가 함께 맞을 때만 쓴다
    other = HealthDataLimitError("X", source="exercise_sessions", can_narrow=True, limit=1000, limit_unit="sessions")
    assert limit_exceeded("get_exercise_sessions", other).payload["message"] == (
        "조회 범위의 기록이 한도(1,000sessions)를 넘었다. 기간을 줄이면 조회할 수 있다"
    )


def test_limit_that_can_narrow_without_known_limit_is_the_plain_fact():
    error = HealthDataLimitError("STEP_RECORD_LIMIT_EXCEEDED", source="step_records", can_narrow=True)
    assert limit_exceeded("get_step_records", error).payload["message"] == NARROW
    no_unit = HealthDataLimitError("STEP_RECORD_LIMIT_EXCEEDED", source="step_records", can_narrow=True, limit=1000)
    assert limit_exceeded("get_step_records", no_unit).payload["message"] == (
        "조회 범위의 기록이 한도(1,000)를 넘었다. 기간을 줄이면 조회할 수 있다"
    )


def test_limit_that_cannot_narrow_says_the_lookup_is_not_possible():
    error = HealthDataLimitError(
        "EXERCISE_SAMPLE_LIMIT_EXCEEDED", source="exercise_samples", can_narrow=False, limit=20000, limit_unit="개"
    )
    assert limit_exceeded("get_exercise_samples", error).payload == {
        "tool": "get_exercise_samples",
        "message": NOT_POSSIBLE,
    }


def test_target_not_found_does_not_tell_missing_from_other_members_records():
    outcome = target_not_found("get_exercise_samples")

    assert outcome.status == "target_not_found"
    assert outcome.evidence == ()
    assert outcome.to_message([]) == {"error": "target_not_found", "tool": "get_exercise_samples", "message": NOT_FOUND}


def test_lookup_failure_picks_the_outcome_by_error_kind():
    assert lookup_failure("get_sleep_records", SLEEP_LIMIT) == limit_exceeded("get_sleep_records", SLEEP_LIMIT)
    assert lookup_failure("get_exercise_samples", SAMPLE_NOT_FOUND) == target_not_found("get_exercise_samples")


def test_detail_failed_keeps_base_result_and_evidence():
    base = base_outcome()
    outcome = detail_failed(base)

    assert outcome.status == "detail_failed"
    assert outcome.payload is base.payload
    assert outcome.evidence == base.evidence
    assert outcome.to_message(["E1"]) == {
        "evidence_ids": ["E1"],
        "result": {"metric": "sleep", "nights": []},
        "detail": {"error": "detail_failed", "message": "상세를 불러오지 못했다"},
    }


def test_detail_failed_is_written_differently_from_no_record():
    no_record = state_fact("기록 없음")
    assert DETAIL_FAILED_MESSAGE == "상세를 불러오지 못했다"
    assert DETAIL_FAILED_MESSAGE != no_record.fact
    assert detail_failed(base_outcome()).status != no_record.status


@pytest.mark.parametrize("status", ["unavailable_period", "invalid_periods", "limit_exceeded", "detail_failed"])
def test_detail_failed_needs_an_ok_base(status):
    with pytest.raises(ValueError):
        detail_failed(ToolOutcome(status, {"tool": "get_sleep_records"}))


def test_ok_outcome_message_is_unchanged_without_detail():
    assert base_outcome().to_message(["E1"]) == {"evidence_ids": ["E1"], "result": {"metric": "sleep", "nights": []}}


def test_states_map_to_result_status_and_fact():
    assert {state: (f.status, f.fact) for state, f in STATE_FACTS.items()} == {
        "기록 없음": ("no_record", "기록이 없다"),
        "실제 0": ("ok", "0으로 기록되어 있다. 기록이 없다는 뜻이 아니다"),
        "동기화 전": ("unsynced", "아직 동기화되지 않아 기록이 있는지 알 수 없다"),
        "준비 중": ("data_not_ready", "건강 기록을 아직 준비하고 있다"),
        "연동 전": ("before_link", "연동하기 전이라 받은 기록이 없다"),
        "조회 실패": ("data_unavailable", "기록을 불러오지 못했다"),
    }


def test_state_statuses_are_the_codes_already_in_the_call_record():
    assert state_fact("준비 중").status == HealthDataNotReadyError.code
    assert state_fact("조회 실패").status == HealthDataUnavailableError.code
    assert {state_fact(s).status for s in ("기록 없음", "동기화 전", "연동 전", "실제 0")} <= set(get_args(DataStatus))


def test_new_sentences_state_facts_without_instructions():
    sentences = [f.fact for f in STATE_FACTS.values()]
    sentences += [NARROW, NOT_POSSIBLE, NOT_FOUND, DETAIL_FAILED_MESSAGE]
    sentences.append(limit_exceeded("get_sleep_records", SLEEP_LIMIT).payload["message"])
    for sentence in sentences:
        assert not any(word in sentence for word in ("안내", "답한다", "말한다", "제안", "하세요", "주세요")), sentence


@pytest.mark.parametrize(
    "outcome",
    [
        limit_exceeded("get_sleep_records", SLEEP_LIMIT),
        target_not_found("get_sleep_records"),
        detail_failed(base_outcome()),
    ],
    ids=["limit_exceeded", "target_not_found", "detail_failed"],
)
async def test_new_statuses_go_to_result_status_as_they_are(outcome):
    async def handler(inp, req):
        return outcome

    reply, record = await run_one(handler)

    assert (record.decision, record.reason, record.result_status) == ("allow", outcome.status, outcome.status)
    assert json.loads(reply.content) == outcome.to_message([e.ref for e in reply.evidence])
    assert record.evidence_ids == tuple(e.id for e in reply.evidence)
    assert len(record.evidence_ids) == len(outcome.evidence)


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (SLEEP_LIMIT, "limit_exceeded", "조회 범위의 기록이 한도(1,000세션)를 넘었다. 기간을 줄이면 조회할 수 있다"),
        (SAMPLE_NOT_FOUND, "target_not_found", NOT_FOUND),
    ],
    ids=["limit", "target"],
)
async def test_uncaught_limit_and_target_errors_become_tool_results(error, status, message):
    async def handler(inp, req):
        raise error

    reply, record = await run_one(handler)

    assert json.loads(reply.content) == {"error": status, "tool": "get_sleep_records", "message": message}
    assert reply.evidence == ()
    assert (record.reason, record.result_status, record.result_count, record.evidence_ids) == (status, status, 0, ())


async def test_detail_error_not_caught_by_the_handler_fails_the_whole_call():
    async def handler(inp, req):
        await req.port.get_metric_result(req.user_id, "sleep", SPAN.start, SPAN.end)
        raise SLEEP_LIMIT

    reply, record = await run_one(handler)

    assert json.loads(reply.content)["error"] == "limit_exceeded"
    assert (record.result_status, record.result_count) == ("limit_exceeded", 0)
