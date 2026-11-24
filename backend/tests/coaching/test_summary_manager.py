"""합성 함수로 요약 작업의 성공·실패·경쟁·종료를 검사한다."""

import asyncio
import logging
import math
import time
from dataclasses import replace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from asyncpg.exceptions import CannotConnectNowError
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.coaching.history import StoredSummary, SummaryResult
from app.coaching.summary import SummaryManager
from app.coaching.summary_adapter import SummaryError
from tests.coaching.fakes import NOW, FakeSessionFactory, fixed_now
from tests.coaching.summary_fakes import (
    SummaryFakeStore,
    SummarySessionFactory,
    SyntheticSummary,
    TestClock,
)
from tests.coaching.test_coaching_runner import Harness, bounded_test


class SummaryHarness:
    """소유 회원·완료 쌍·가짜 저장소와 시간 제어를 한데 준비한다."""

    def __init__(self, *, attempt_seconds: float = 1.0, max_pairs: int = 100) -> None:
        """합성 완료 대화와 주입 가능한 요약 관리자를 구성한다."""
        self.user_id = uuid4()
        self.store = SummaryFakeStore()
        self.factory = SummarySessionFactory(self.store)
        self.script = SyntheticSummary(max_pairs=max_pairs)
        self.clock = TestClock()
        self.request = self.seed()
        self.manager = SummaryManager(
            session_factory=self.factory,
            functions=self.script.functions(),
            store=self.store,
            clock=fixed_now,
            monotonic=self.clock.now,
            sleep=self.clock.sleep,
            attempt_seconds=attempt_seconds,
        )

    def seed(self, conversation_id: UUID | None = None):
        """실제 접수 규칙으로 질문을 만들고 합성 정상 답변을 완료 상태로 둔다."""
        request = self.store.seed_accepted(
            user_id=self.user_id, conversation_id=conversation_id
        )
        self.store._finish(
            request.id,
            content="합성 정상 답변",
            now=NOW,
            error_code=None,
            error_context=None,
        )
        return self.store.state(request.id)

    def submit(self, conversation_id: UUID | None = None):
        """기본 또는 지정 대화의 요약 작업을 예약한다."""
        return self.manager.submit(
            user_id=self.user_id,
            conversation_id=conversation_id or self.request.conversation_id,
        )


@bounded_test
async def test_summary_success_replans_backlog_without_holding_session() -> None:
    """성공 후 새 기준점으로 다음 구간을 처리하고 LLM 대기 중 세션을 닫는다."""
    harness = SummaryHarness(max_pairs=1)
    harness.seed(harness.request.conversation_id)
    original = harness.store.requests.copy()

    def observe() -> None:
        """각 생성 호출에서 열린 DB 세션이 없음을 확인한다."""
        assert harness.factory.active == 0

    harness.script.observe = observe
    await harness.submit()
    assert len(harness.script.calls) == 2
    assert harness.script.calls[1][0] == StoredSummary("합성 누적 요약 2", 2)
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 4
    assert harness.store.requests == original
    assert harness.factory.commits == 2
    assert harness.factory.active == 0


@bounded_test
async def test_duplicate_reservations_and_global_generation_limit() -> None:
    """같은 대화는 하나만 예약하고 다른 대화도 실제 생성은 하나씩 실행한다."""
    harness = SummaryHarness()
    second = harness.seed()
    harness.script.gate = asyncio.Event()
    first_task = harness.submit()
    assert harness.submit() is None
    second_task = harness.submit(second.conversation_id)
    await harness.script.entered.wait()
    assert len(harness.manager.tasks) == 2
    assert len(harness.script.calls) == 1
    assert harness.factory.active == 0
    harness.script.gate.set()
    await asyncio.gather(first_task, second_task)
    assert harness.script.maximum == 1
    assert len(harness.script.calls) == 2


@bounded_test
async def test_transient_failure_preserves_plan_and_obeys_retry_after() -> None:
    """일시 오류만 고정 계획으로 총 세 번 시도하고 더 긴 Retry-After를 따른다."""
    harness = SummaryHarness()
    harness.script.errors = [
        SummaryError("rate_limited", retryable=True, retry_after=45),
        SummaryError("llm_unavailable", retryable=True),
    ]

    def observe_wait() -> None:
        """재시도 대기에서 생성 슬롯과 세션이 모두 반환됐음을 확인한다."""
        assert harness.factory.active == 0
        assert harness.script.active == 0

    harness.clock.observe = observe_wait
    await harness.submit()
    assert harness.clock.delays == [45, 120]
    assert len(harness.script.calls) == 3
    assert all(call[1] is harness.script.calls[0][1] for call in harness.script.calls)
    assert harness.store.saves == 1


@bounded_test
async def test_retry_exhaustion_cooldown_does_not_reset_on_new_questions() -> None:
    """세 번 소진 후에는 중복 질문도 횟수를 초기화하지 않고 5분 후에만 재판단한다."""
    harness = SummaryHarness()
    harness.script.errors = [SummaryError("timeout", retryable=True) for _ in range(3)]
    await harness.submit()
    assert harness.clock.delays == [30, 120]
    assert len(harness.script.calls) == 3
    assert harness.store.summaries == {}
    assert harness.submit() is None
    harness.clock.value += 299
    assert harness.submit() is None
    harness.clock.value += 1
    await harness.submit()
    assert len(harness.script.calls) == 4
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2


NONRETRYABLE_CODES = ["truncated", "invalid_format", "input_too_large", "config"]


def outcome_lines(caplog: pytest.LogCaptureFixture, kind: str) -> list[str]:
    """캡처한 로그에서 지정한 outcome 줄의 메시지만 순서대로 반환한다."""
    return [
        record.getMessage()
        for record in caplog.records
        if f"outcome={kind}" in record.getMessage()
    ]


@pytest.mark.parametrize("code", NONRETRYABLE_CODES)
@bounded_test
async def test_failed_scope_expires_after_cooldown_and_success_clears_it(
    code: str,
) -> None:
    """만료 시각(300초)부터 같은 범위를 다시 생성하고 성공하면 기록을 지운다."""
    harness = SummaryHarness()
    harness.script.errors = [SummaryError(code, retryable=False)]
    await harness.submit()
    harness.clock.value += 300
    await harness.submit()
    assert len(harness.script.calls) == 2
    assert harness.script.calls[1][1] == harness.script.calls[0][1]
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2
    assert harness.manager._failed_scopes == {}


@bounded_test
async def test_failed_scope_is_rerecorded_with_new_expiry_after_second_failure() -> (
    None
):
    """만료 뒤 다시 재시도 불가 실패하면 새 만료로 기록해 299초 생략, 300초 생성한다."""
    harness = SummaryHarness()
    harness.script.errors = [
        SummaryError("invalid_format", retryable=False),
        SummaryError("truncated", retryable=False),
    ]
    await harness.submit()
    harness.clock.value += 300
    await harness.submit()
    assert len(harness.script.calls) == 2
    assert harness.store.summaries == {}
    harness.clock.value += 299
    await harness.submit()
    assert len(harness.script.calls) == 2
    harness.clock.value += 1
    await harness.submit()
    assert len(harness.script.calls) == 3
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2


@bounded_test
async def test_input_limit_scope_is_skipped_before_expiry_and_retried_after() -> None:
    """상한 때문에 새 답변이 와도 범위가 같으면 만료 전 생략하고 만료 후 생성한다."""
    harness = SummaryHarness(max_pairs=1)
    harness.script.errors = [SummaryError("input_too_large", retryable=False)]
    await harness.submit()
    harness.seed(harness.request.conversation_id)
    harness.clock.value += 299
    await harness.submit()
    assert len(harness.script.calls) == 1
    harness.seed(harness.request.conversation_id)
    harness.clock.value += 1
    await harness.submit()
    assert len(harness.script.calls) >= 2  # 성공 뒤 밀린 구간도 이어서 처리한다.
    assert harness.script.calls[1][1] == harness.script.calls[0][1]
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 6


@bounded_test
async def test_submit_prunes_failed_scope_after_retention_of_other_conversation() -> (
    None
):
    """대화 X의 실패 기록은 재시도 시각+보관 기간 전에는 남고 → 그 시각에 정리된다."""
    harness = SummaryHarness()
    other = harness.seed()
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    await harness.submit()
    assert set(harness.manager._failed_scopes) == {harness.request.conversation_id}
    harness.clock.value += 300
    await harness.submit(other.conversation_id)
    assert set(harness.manager._failed_scopes) == {harness.request.conversation_id}
    harness.clock.value += 86399
    await harness.submit(other.conversation_id)
    assert set(harness.manager._failed_scopes) == {harness.request.conversation_id}
    harness.clock.value += 1
    await harness.submit(other.conversation_id)
    assert harness.manager._failed_scopes == {}


@bounded_test
async def test_exhausted_scope_is_never_pruned() -> None:
    """정지 기록은 보관 기간이 한참 지나도 → 정리되지 않는다."""
    harness = SummaryHarness()
    other = harness.seed()
    await exhaust_scope(harness)
    harness.clock.value += 10 * 86400
    await harness.submit(other.conversation_id)
    scope = harness.manager._failed_scopes[harness.request.conversation_id]
    assert scope.retry_at == math.inf


async def exhaust_scope(harness: SummaryHarness, codes=None) -> None:
    """300초 간격으로 같은 범위를 지정한 형식 오류 코드 순서대로 실패시킨다."""
    codes = codes or ["invalid_format"] * 3
    harness.script.errors = [SummaryError(code, retryable=False) for code in codes]
    for index in range(len(codes)):
        if index:
            harness.clock.value += 300
        await harness.submit()


@bounded_test
async def test_same_scope_format_failures_stop_after_three(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """같은 범위 invalid_format이 세 번 나면 → 정지 로그 한 줄 뒤 이후 생성하지 않는다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    await exhaust_scope(harness)
    assert len(harness.script.calls) == 3
    assert outcome_lines(caplog, "failed_scope_exhausted") == [
        "coaching summary outcome=failed_scope_exhausted code=invalid_format"
    ]
    for elapsed in (300, 300, 3 * 86400):
        harness.clock.value += elapsed
        await harness.submit()
    assert len(harness.script.calls) == 3
    assert len(outcome_lines(caplog, "skipped_failed_scope")) == 3
    assert len(outcome_lines(caplog, "failed_scope_exhausted")) == 1


@bounded_test
async def test_mixed_format_codes_count_together(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """invalid_format → truncated → invalid_format → 세 번째에 정지하고 code는 마지막 것이다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    await exhaust_scope(harness, ["invalid_format", "truncated", "invalid_format"])
    assert outcome_lines(caplog, "failed_scope_exhausted") == [
        "coaching summary outcome=failed_scope_exhausted code=invalid_format"
    ]
    harness.clock.value += 300
    await harness.submit()
    assert len(harness.script.calls) == 3


@pytest.mark.parametrize("code", ["config", "input_too_large"])
@bounded_test
async def test_non_format_failures_never_exhaust_scope(
    code: str, caplog: pytest.LogCaptureFixture
) -> None:
    """config·input_too_large가 네 번 나도 → 횟수를 세지 않아 계속 재시도한다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    await exhaust_scope(harness, [code] * 4)
    assert len(harness.script.calls) == 4
    assert outcome_lines(caplog, "failed_scope_exhausted") == []
    assert (
        harness.manager._failed_scopes[harness.request.conversation_id].retry_at
        != math.inf
    )


@bounded_test
async def test_other_failure_between_format_failures_keeps_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """invalid_format 2번 사이에 config가 끼어도 → 횟수를 잇고 네 번째 호출 뒤 정지한다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    await exhaust_scope(
        harness, ["invalid_format", "invalid_format", "config", "invalid_format"]
    )
    assert len(harness.script.calls) == 4
    assert len(outcome_lines(caplog, "failed_scope_exhausted")) == 1
    harness.clock.value += 300
    await harness.submit()
    assert len(harness.script.calls) == 4


@bounded_test
async def test_success_after_format_failures_clears_scope() -> None:
    """형식 오류 2번 뒤 300초에 성공 → 저장하고 실패 기록을 지운다."""
    harness = SummaryHarness()
    await exhaust_scope(harness, ["invalid_format", "truncated"])
    harness.clock.value += 300
    await harness.submit()
    assert len(harness.script.calls) == 3
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2
    assert harness.manager._failed_scopes == {}


@bounded_test
async def test_changed_scope_after_exhaustion_is_generated_with_fresh_count() -> None:
    """정지 뒤 범위가 바뀌면 → 바로 생성하고 그 범위의 형식 오류는 횟수 1로 시작한다."""
    harness = SummaryHarness()
    conversation_id = harness.request.conversation_id
    await exhaust_scope(harness)
    harness.store.summaries[conversation_id] = StoredSummary("다른 합성 요약", 2)
    harness.seed(conversation_id)
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    await harness.submit()
    assert len(harness.script.calls) == 4
    scope = harness.manager._failed_scopes[conversation_id]
    assert scope.format_failures == 1
    assert scope.retry_at != math.inf


@bounded_test
async def test_run_ignores_expired_failed_scope_without_submit_prune() -> None:
    """정리 없이 _run에 닿은 실패 범위도 299초는 생략하고 → 만료 시각과 같으면 다시 생성한다."""
    harness = SummaryHarness()
    conversation_id = harness.request.conversation_id
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    await harness.submit()
    assert len(harness.script.calls) == 1
    harness.clock.value += 299
    await harness.manager._run(harness.user_id, conversation_id)
    assert len(harness.script.calls) == 1
    assert conversation_id in harness.manager._failed_scopes
    harness.clock.value += 1
    assert conversation_id in harness.manager._failed_scopes
    await harness.manager._run(harness.user_id, conversation_id)
    assert len(harness.script.calls) == 2
    assert harness.store.summaries[conversation_id].until_seq == 2


@bounded_test
async def test_invalid_format_logs_one_failed_line_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """invalid_format 실패 → code·retryable·attempt 한 줄만 남기고 원문은 싣지 않는다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    await harness.submit()
    assert outcome_lines(caplog, "failed") == [
        "coaching summary outcome=failed code=invalid_format retryable=False attempt=1"
    ]
    assert "합성 정상 답변" not in caplog.text
    assert str(harness.request.conversation_id) not in caplog.text
    assert str(harness.user_id) not in caplog.text


@bounded_test
async def test_timeout_exhaustion_logs_attempts_in_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """timeout 세 번 소진 → attempt 1,2,3이 code=timeout retryable=True로 순서대로 남는다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    harness.script.errors = [SummaryError("timeout", retryable=True) for _ in range(3)]
    await harness.submit()
    assert outcome_lines(caplog, "failed") == [
        f"coaching summary outcome=failed code=timeout retryable=True attempt={n}"
        for n in (1, 2, 3)
    ]


@bounded_test
async def test_attempt_deadline_logs_attempt_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """생성이 시도 기한을 넘기면 code=attempt_timeout retryable=True로 남는다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness(attempt_seconds=0.01)
    harness.script.gate = asyncio.Event()
    await harness.submit()
    assert outcome_lines(caplog, "failed")[0] == (
        "coaching summary outcome=failed code=attempt_timeout retryable=True attempt=1"
    )


@bounded_test
async def test_transient_storage_error_logs_storage_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB 일시 장애 → code=storage_unavailable retryable=True attempt=1로 남는다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    read = harness.store.find_history_context
    harness.store.find_history_context = Mock(side_effect=[ConnectionError("합성")])

    def restore_read() -> None:
        """재시도 대기에서 읽기 장애를 복구한다."""
        harness.store.find_history_context = read

    harness.clock.observe = restore_read
    await harness.submit()
    assert outcome_lines(caplog, "failed") == [
        "coaching summary outcome=failed code=storage_unavailable "
        "retryable=True attempt=1"
    ]
    read_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("coaching summary read failed: ")
    ]
    assert len(read_logs) == 1
    assert "ConnectionError at " in read_logs[0]


@bounded_test
async def test_unexpected_error_logs_internal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """예상 밖 예외 → code=internal retryable=False attempt=1 한 줄이 남는다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    harness.script.errors = [RuntimeError("합성 예상 밖 오류")]
    await harness.submit()
    assert outcome_lines(caplog, "failed") == [
        "coaching summary outcome=failed code=internal retryable=False attempt=1"
    ]


@pytest.mark.parametrize("code", NONRETRYABLE_CODES)
@bounded_test
async def test_nonretryable_failure_skips_same_scope_and_replans_new_scope(
    code: str, caplog: pytest.LogCaptureFixture
) -> None:
    """재시도 불가 실패 뒤 299초는 같은 범위를 생략하고 새 범위는 바로 재계획한다."""
    caplog.set_level(logging.INFO, logger="app.coaching.summary")
    harness = SummaryHarness()
    harness.script.errors = [SummaryError(code, retryable=False)]
    await harness.submit()
    harness.clock.value += 299
    await harness.submit()
    assert len(harness.script.calls) == 1
    assert outcome_lines(caplog, "skipped_failed_scope") == [
        "coaching summary outcome=skipped_failed_scope"
    ]
    assert harness.clock.delays == []
    assert harness.store.summaries == {}
    harness.seed(harness.request.conversation_id)
    await harness.submit()
    assert len(harness.script.calls) == 2
    assert harness.script.calls[1][1].targets[:1] == harness.script.calls[0][1].targets
    assert harness.script.calls[1][1].until_seq == 4
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 4


@bounded_test
async def test_new_turn_with_unchanged_target_does_not_skip_oldest_prefix() -> None:
    """새 답변이 있어도 대상 끝이 같으면 재생성을 생략하고 오래된 쌍을 건너뛰지 않는다."""
    harness = SummaryHarness(max_pairs=1)
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    await harness.submit()
    harness.seed(harness.request.conversation_id)
    await harness.submit()
    assert len(harness.script.calls) == 1
    assert harness.store.summaries == {}
    harness.script.max_pairs = 2
    harness.seed(harness.request.conversation_id)
    await harness.submit()
    assert harness.script.calls[1][1].targets[:1] == harness.script.calls[0][1].targets
    assert [call[1].until_seq for call in harness.script.calls] == [2, 4, 6]


@bounded_test
async def test_changed_summary_allows_same_target_end_to_be_replanned() -> None:
    """다른 작업이 기준 요약을 갱신하면 끝 번호가 같아도 새 범위로 판단한다."""
    harness = SummaryHarness()
    harness.seed(harness.request.conversation_id)
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    await harness.submit()
    harness.store.summaries[harness.request.conversation_id] = StoredSummary(
        "다른 합성 요약", 2
    )
    await harness.submit()
    assert [call[1].until_seq for call in harness.script.calls] == [4, 4]
    assert harness.script.calls[1][0] == StoredSummary("다른 합성 요약", 2)
    assert harness.script.calls[1][1].targets[0].question_seq == 3


@pytest.mark.parametrize("new_answer", [False, True])
@bounded_test
async def test_submit_during_nonretryable_failure_is_replanned(
    new_answer: bool,
) -> None:
    """실패할 생성 중 들어온 예약도 새 상태를 읽되 동일 범위는 즉시 재생성하지 않는다."""
    harness = SummaryHarness()
    harness.script.errors = [SummaryError("invalid_format", retryable=False)]
    harness.script.gate = asyncio.Event()
    task = harness.submit()
    await harness.script.entered.wait()
    if new_answer:
        harness.seed(harness.request.conversation_id)
    assert harness.submit() is None
    harness.script.gate.set()
    await task
    assert len(harness.script.calls) == (2 if new_answer else 1)
    assert len(harness.script.plans) >= 2
    if new_answer:
        assert harness.store.summaries[harness.request.conversation_id].until_seq == 4
        assert (
            harness.script.calls[1][1].targets[:1] == harness.script.calls[0][1].targets
        )
    else:
        assert harness.store.summaries == {}
    assert harness.clock.delays == []


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("합성 연결 장애"),
        PoolTimeoutError("합성 풀 대기 초과"),
        CannotConnectNowError("합성 일시 접속 장애"),
        DBAPIError(
            None, None, Exception("합성 연결 무효화"), connection_invalidated=True
        ),
    ],
)
@bounded_test
async def test_transient_initial_read_retries_without_blocking_conversation(
    error: Exception,
) -> None:
    """최초 조회의 연결 장애는 세션을 반환한 뒤 재시도하여 정상 저장까지 진행한다."""
    harness = SummaryHarness()
    read = harness.store.find_history_context
    harness.store.find_history_context = Mock(side_effect=[error])

    def restore_read() -> None:
        """재시도 대기에서 세션 반환을 확인하고 읽기 장애를 복구한다."""
        assert harness.factory.active == 0
        harness.store.find_history_context = read

    harness.clock.observe = restore_read
    await harness.submit()
    assert harness.clock.delays == [30]
    assert len(harness.script.calls) == 1
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2


@bounded_test
async def test_transient_read_exhaustion_obeys_cooldown() -> None:
    """조회 장애도 세 번과 대기 상한을 지키고 cooldown 이후 정상 예약으로 복구한다."""
    harness = SummaryHarness()
    read = harness.store.find_history_context
    harness.store.find_history_context = Mock(
        side_effect=ConnectionError("합성 연결 장애")
    )
    await harness.submit()
    assert harness.store.find_history_context.call_count == 3
    assert harness.clock.delays == [30, 120]
    assert harness.script.calls == []
    assert harness.submit() is None
    harness.store.find_history_context = read
    harness.clock.value += 300
    await harness.submit()
    assert harness.store.saves == 1


@pytest.mark.parametrize("stage", ["read", "save"])
@bounded_test
async def test_nontransient_storage_error_is_not_retried(stage: str) -> None:
    """SQL 계약 오류는 연결 장애로 간주하거나 무조건 세 번 반복하지 않는다."""
    harness = SummaryHarness()
    error = ProgrammingError(None, None, Exception("합성 SQL 계약 오류"))
    method = "find_history_context" if stage == "read" else "save_summary"
    operation = Mock(side_effect=error)
    setattr(harness.store, method, operation)
    await harness.submit()
    assert operation.call_count == 1
    assert harness.clock.delays == []
    assert harness.store.summaries == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("until_seq", 4),
        ("policy_version", "wrong"),
        ("text", " "),
        ("tokens", True),
        ("tokens", 801),
    ],
)
@bounded_test
async def test_invalid_generated_result_never_saves_or_retries(
    field: str, value: object
) -> None:
    """잘못된 범위·버전·본문·토큰 수는 저장하거나 재생성하지 않는다."""
    harness = SummaryHarness()
    harness.script.result = replace(
        SummaryResult("합성 요약", 2, 10, "synthetic-v1"), **{field: value}
    )
    await harness.submit()
    assert harness.store.saves == 0
    assert harness.factory.commits == 0
    assert len(harness.script.calls) == 1
    assert harness.clock.delays == []


@bounded_test
async def test_rejected_cas_reloads_once_and_stops_without_spin() -> None:
    """변화 없는 조건부 갱신 0건도 재조회 후 멈춰 같은 계획을 무한 재실행하지 않는다."""
    harness = SummaryHarness()
    harness.store.reject_save = True
    await harness.submit()
    assert harness.store.saves == 1
    assert len(harness.script.calls) == 1
    assert harness.factory.opened == 3
    assert harness.store.summaries == {}


@bounded_test
async def test_rejected_cas_retries_only_failed_reload() -> None:
    """CAS 거부 뒤 재조회가 일시 실패해도 같은 저장을 다시 호출하지 않는다."""
    harness = SummaryHarness()
    harness.store.reject_save = True
    read = harness.store.find_history_context
    reads = 0

    async def fail_reload_once(*args, **kwargs):
        """최초 조회·저장 검증 다음의 재조회만 연결 장애로 실패시킨다."""
        nonlocal reads
        reads += 1
        if reads == 3:
            raise ConnectionError("합성 CAS 재조회 장애")
        return await read(*args, **kwargs)

    harness.store.find_history_context = fail_reload_once
    await harness.submit()
    assert reads == 4
    assert harness.store.saves == 1
    assert len(harness.script.calls) == 1
    assert harness.clock.delays == [30]


@bounded_test
async def test_submit_during_cas_reload_replans_latest_scope() -> None:
    """CAS 종료를 위한 조회 중 새 완료 예약이 오면 재조회하여 새 계획으로 저장한다."""
    harness = SummaryHarness()
    harness.store.reject_save = True
    read = harness.store.find_history_context
    entered, release = asyncio.Event(), asyncio.Event()
    reads = 0

    async def hold_reload(*args, **kwargs):
        """CAS 후 조회 결과를 지연해 새 답변 완료와 교차시킨다."""
        nonlocal reads
        reads += 1
        found = await read(*args, **kwargs)
        if reads == 3:
            entered.set()
            await release.wait()
        return found

    harness.store.find_history_context = hold_reload
    task = harness.submit()
    await entered.wait()
    harness.seed(harness.request.conversation_id)
    assert harness.submit() is None
    harness.store.reject_save = False
    release.set()
    await task
    assert [call[1].until_seq for call in harness.script.calls] == [2, 4]
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 4


@bounded_test
async def test_failed_commit_reconciles_before_retrying_same_result() -> None:
    """commit 실패로 롤백되면 새 조회로 확인하고 생성 결과를 재사용해 저장만 재시도한다."""
    harness = SummaryHarness()
    harness.factory.commit_errors = [ConnectionError("합성 저장 실패")]
    await harness.submit()
    assert len(harness.script.calls) == 1
    assert harness.store.saves == 2
    assert harness.factory.rollbacks == 1
    assert harness.clock.delays == [30]
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2


@bounded_test
async def test_unknown_commit_result_reloads_saved_value_without_repeating_llm() -> (
    None
):
    """응답만 실패하고 실제 저장된 경우 새 세션으로 확인해 중복 생성을 피한다."""
    harness = SummaryHarness()
    factory = FakeSessionFactory()
    factory.commit_errors = [ConnectionError("합성 commit 응답 소실")]
    harness.manager = SummaryManager(
        session_factory=factory,
        store=harness.store,
        functions=harness.script.functions(),
        sleep=harness.clock.sleep,
    )
    await harness.submit()
    assert harness.store.saves == 1
    assert len(harness.script.calls) == 1
    assert harness.clock.delays == []
    assert factory.opened == 4


@pytest.mark.parametrize("committed", [False, True])
@bounded_test
async def test_save_recovery_read_retries_before_writing_again(committed: bool) -> None:
    """저장 결과 확인 조회도 실패하면 다음 시도에 먼저 재조회하고 생성 결과를 재사용한다."""
    harness = SummaryHarness()
    factory = FakeSessionFactory() if committed else harness.factory
    factory.commit_errors = [ConnectionError("합성 commit 응답 소실")]
    read = harness.store.find_history_context
    reads = 0

    async def unavailable_recovery(*args, **kwargs):
        """첫 저장 뒤 복구 조회만 두 차례 실패하여 재저장 전에 조회하는지 검사한다."""
        nonlocal reads
        reads += 1
        if reads in (3, 4):
            raise ConnectionError("합성 복구 조회 연결 장애")
        return await read(*args, **kwargs)

    harness.store.find_history_context = unavailable_recovery
    harness.manager = SummaryManager(
        session_factory=factory,
        store=harness.store,
        functions=harness.script.functions(),
        sleep=harness.clock.sleep,
    )
    await harness.submit()
    assert harness.clock.delays == [30, 120]
    assert len(harness.script.calls) == 1
    assert harness.store.saves == (1 if committed else 2)
    assert harness.store.summaries[harness.request.conversation_id].until_seq == 2


@bounded_test
async def test_timeout_includes_waiting_for_global_slot() -> None:
    """다른 대화의 생성 슬롯을 기다리는 시간도 각 시도의 기한에 포함한다."""
    harness = SummaryHarness(attempt_seconds=0.01)
    harness.script.gate = asyncio.Event()
    task = harness.submit()
    second = harness.seed()
    waiting = harness.submit(second.conversation_id)
    await asyncio.gather(task, waiting)
    assert len(harness.script.calls) <= 3
    assert harness.store.saves == 0
    assert len(harness.clock.delays) == 4
    assert harness.factory.active == 0


@bounded_test
async def test_timeout_discards_result_from_generator_that_swallows_cancel() -> None:
    """생성 함수가 기한 취소를 삼켜 반환해도 늦은 결과를 저장하지 않는다."""
    harness = SummaryHarness(attempt_seconds=0.005)
    harness.script.gate = asyncio.Event()
    harness.script.suppress_cancel = True
    await harness.submit()
    assert harness.script.cancelled
    assert harness.store.saves == 0
    assert harness.store.summaries == {}


@bounded_test
async def test_timeout_discards_result_after_cpu_work_without_yield() -> None:
    """동기 계산이 타이머 콜백 실행을 지연시켜도 실제 기한을 넘긴 결과를 버린다."""
    harness = SummaryHarness(attempt_seconds=0.003)
    calls = 0

    async def cpu_bound(summary, plan):
        """짧은 합성 CPU 작업으로 이벤트 루프에 양보하지 않고 기한을 넘긴다."""
        nonlocal calls
        calls += 1
        until = time.monotonic() + 0.006
        while time.monotonic() < until:
            pass
        return SummaryResult(
            "기한 뒤 합성 결과", plan.until_seq, 10, plan.policy_version
        )

    harness.manager = SummaryManager(
        session_factory=harness.factory,
        store=harness.store,
        functions=replace(harness.script.functions(), generate_summary=cpu_bound),
        attempt_seconds=0.003,
        sleep=harness.clock.sleep,
    )
    await harness.submit()
    assert calls == 3
    assert harness.store.saves == 0
    assert harness.clock.delays == [30, 120]


@bounded_test
async def test_shutdown_rejects_new_work_and_late_result() -> None:
    """종료는 새 예약을 막고 취소를 삼킨 생성 함수의 결과도 저장하지 않는다."""
    harness = SummaryHarness()
    harness.script.gate = asyncio.Event()
    harness.script.suppress_cancel = True
    task = harness.submit()
    await harness.script.entered.wait()
    await harness.manager.aclose(timeout=0.03)
    assert task.cancelled()
    assert harness.submit() is None
    assert harness.store.saves == 0
    assert harness.factory.active == 0


@bounded_test
async def test_runner_shutdown_bounds_wait_and_keeps_uncooperative_task_reference() -> (
    None
):
    """종료 상한 뒤에도 취소를 지연한 작업의 참조를 유지하고 늦은 저장을 차단한다."""
    harness = SummaryHarness()
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def uncooperative(summary, plan):
        """취소를 받은 뒤 테스트가 허용할 때까지 반환을 지연한다."""
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return SummaryResult("늦은 합성 결과", plan.until_seq, 10, plan.policy_version)

    harness.manager = SummaryManager(
        session_factory=harness.factory,
        store=harness.store,
        functions=replace(harness.script.functions(), generate_summary=uncooperative),
    )
    task = harness.submit()
    await entered.wait()
    runner_harness = Harness()
    runner_harness.runner._summaries = harness.manager
    await runner_harness.runner.aclose(timeout=0.005)
    assert cancelled.is_set()
    assert task in harness.manager.tasks
    assert not task.done()
    assert harness.submit() is None
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert harness.store.saves == 0


@bounded_test
async def test_busy_completion_during_empty_snapshot_triggers_fresh_read() -> None:
    """빈 스냅샷 조회 중 새 답변의 예약이 오면 작업을 중복 생성하지 않고 다시 읽는다."""
    harness = SummaryHarness()
    original_read = harness.store.find_history_context
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def read_then_hold(*args, **kwargs):
        """첫 조회만 빈 시점의 결과를 보관해 완료 예약과 교차시킨다."""
        nonlocal calls
        found = await original_read(*args, **kwargs)
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            return replace(found, turns=())
        return found

    harness.store.find_history_context = read_then_hold
    task = harness.submit()
    await entered.wait()
    assert harness.submit() is None
    release.set()
    await task
    assert len(harness.script.calls) == 1
    assert harness.store.saves == 1


@bounded_test
async def test_cancel_before_start_releases_busy_registration() -> None:
    """본문 실행 전 취소된 작업도 등록이 해제되어 다음 예약이 가능하다."""
    harness = SummaryHarness()
    task = harness.submit()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replacement = harness.submit()
    assert replacement is not None
    await replacement
    assert len(harness.script.calls) == 1
    assert harness.store.saves == 1


@bounded_test
async def test_cancel_during_retry_wait_preserves_data_and_propagates() -> None:
    """재시도 대기 취소를 일반 오류로 바꾸지 않고 원문·기존 요약을 유지한다."""
    harness = SummaryHarness()
    original = harness.store.requests.copy()
    harness.script.errors = [SummaryError("timeout", retryable=True)]
    harness.clock.gate = asyncio.Event()
    task = harness.submit()
    await harness.clock.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert harness.store.requests == original
    assert harness.store.summaries == {}
    assert harness.factory.active == 0


@bounded_test
async def test_runner_schedules_only_after_completed_commit_and_ignores_submit_failure() -> (
    None
):
    """완료 commit 후에만 예약하며 예약 오류는 새 WS 오류로 바꾸지 않는다."""
    harness = Harness()
    summaries = Mock(spec=SummaryManager)

    def schedule(**kwargs: object) -> None:
        """예약 시점에 완료 저장·이벤트가 끝났음을 확인하고 합성 오류를 낸다."""
        assert harness.factory.commits == 2
        assert harness.channel.events[-1]["type"] == "completed"
        assert kwargs == {
            "user_id": harness.user_id,
            "conversation_id": harness.request.conversation_id,
        }
        raise RuntimeError("합성 예약 실패")

    summaries.submit.side_effect = schedule
    harness.runner._summaries = summaries
    await harness.submit()
    assert summaries.submit.call_count == 1
    assert harness.store.state(harness.request.id).status == "completed"
    assert [event["type"] for event in harness.channel.events].count("completed") == 1
    assert not any(event["type"] == "error" for event in harness.channel.events)


@bounded_test
async def test_failed_answer_commit_never_schedules_summary() -> None:
    """완료 답변의 commit이 실패하면 요약 작업을 시작하지 않는다."""
    harness = Harness()
    summaries = Mock(spec=SummaryManager)
    harness.runner._summaries = summaries

    def fail_second_commit() -> None:
        """답변 저장 commit만 합성 오류로 실패시킨다."""
        if harness.factory.commits == 2:
            raise RuntimeError("합성 답변 저장 실패")

    harness.factory.on_commit = fail_second_commit
    await harness.submit()
    summaries.submit.assert_not_called()
    assert harness.channel.events[-1]["data"]["code"] == "PERSISTENCE_FAILED"


def test_manager_without_functions_stays_disabled() -> None:
    """함수가 없는 관리자 대체 주입은 기본 앱 활성화와 별개로 비활성이다."""
    manager = SummaryManager(session_factory=FakeSessionFactory())
    assert manager.submit(user_id=uuid4(), conversation_id=uuid4()) is None
