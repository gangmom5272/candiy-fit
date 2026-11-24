"""완료 답변과 독립적인 누적 요약 작업의 수명·재시도·저장을 관리한다."""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import repository
from app.coaching.connection import log_failure
from app.coaching.history import (
    HistorySnapshot,
    StoredSummary,
    SummaryPlan,
    SummaryResult,
    validate_plan,
    validate_result,
)
from app.coaching.summary_adapter import SummaryError, SummaryFunctions
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error

logger = logging.getLogger(__name__)

# 같은 범위에서 이 횟수만큼 형식 오류가 나면 더는 자동 재생성하지 않는다.
MAX_SAME_SCOPE_FORMAT_FAILURES = 3
# 형식 오류로 세는 SummaryError.code 목록이다.
FORMAT_FAILURE_CODES = frozenset({"invalid_format", "truncated"})


def _utc_now() -> datetime:
    """요약 저장 시각을 시간대가 있는 UTC로 반환한다."""
    return datetime.now(UTC)


class SummaryStore(Protocol):
    """요약 조회·조건부 갱신과 호출자 소유 트랜잭션의 경계를 정의한다."""

    async def find_history_context(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        conversation_id: UUID,
        before_sequence_no: int | None = None,
    ) -> HistorySnapshot | None:
        """소유권 있는 전체 완료 쌍·요약·미종결 경계를 반환한다."""
        ...

    async def save_summary(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        conversation_id: UUID,
        snapshot: HistorySnapshot,
        plan: SummaryPlan,
        result: SummaryResult,
        now: datetime,
    ) -> bool:
        """기준점이 같은 경우에만 요약을 갱신하고 commit은 하지 않는다."""
        ...


@dataclass(slots=True)
class _Work:
    """재시도 중 대상 범위를 고정하고 생성 완료한 결과는 재사용한다."""

    snapshot: HistorySnapshot
    plan: SummaryPlan
    result: SummaryResult | None = None
    needs_reconcile: bool = False
    superseded: bool = False


@dataclass(frozen=True, slots=True)
class _FailedScope:
    """재시도 불가로 실패한 요약 범위, 형식 오류 횟수, 재시도 가능 단조 시각을 담는다.

    retry_at이 무한대이면 정지 상태로 이 범위는 다시 생성하지 않는다.
    """

    summary: StoredSummary | None
    until_seq: int
    format_failures: int
    retry_at: float


class _RetryStorage(Exception):
    """일시적 조회·저장 장애를 같은 작업의 제한된 재시도로 전달한다."""


class SummaryManager:
    """대화당 한 작업과 프로세스 전체 한 생성으로 요약을 비동기 실행한다."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        functions: SummaryFunctions | None = None,
        store: SummaryStore = repository,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        attempt_seconds: float = 60.0,
        retry_delays: tuple[float, float] = (30.0, 120.0),
        cooldown_seconds: float = 300.0,
        failed_scope_retention_seconds: float = 86400.0,
    ) -> None:
        """주입 함수·시간 설정과 강한 작업 참조·슬롯·실패 대기를 초기화한다."""
        self._session_factory = session_factory
        self._functions = functions
        self._store = store
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._attempt_seconds = attempt_seconds
        self._retry_delays = retry_delays
        self._cooldown_seconds = cooldown_seconds
        self._failed_scope_retention = failed_scope_retention_seconds
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._requested: set[UUID] = set()
        self._cooldowns: dict[UUID, float] = {}
        self._failed_scopes: dict[UUID, _FailedScope] = {}
        self._slot = asyncio.Semaphore(1)
        self._closing = False

    @property
    def tasks(self) -> frozenset[asyncio.Task[None]]:
        """종료 관리자가 동일한 종료 상한에서 기다릴 강한 참조를 반환한다."""
        return frozenset(self._tasks.values())

    def submit(
        self, *, user_id: UUID, conversation_id: UUID
    ) -> asyncio.Task[None] | None:
        """비활성·종료·중복·실패 대기 상태면 예약하지 않고 나머지는 즉시 등록한다.

        재시도 시각에 보관 기간이 더 지난 재시도 불가 실패 범위 기록도 이때 정리한다.
        정지 기록은 정리하지 않는다.
        """
        if self._closing or self._functions is None:
            return None
        existing = self._tasks.get(conversation_id)
        if existing is not None and not existing.done():
            self._requested.add(conversation_id)
            return None
        now = self._monotonic()
        self._cooldowns = {
            key: until for key, until in self._cooldowns.items() if until > now
        }
        self._failed_scopes = {
            key: scope
            for key, scope in self._failed_scopes.items()
            if scope.retry_at + self._failed_scope_retention > now
        }
        if conversation_id in self._cooldowns:
            return None
        task = asyncio.create_task(
            self._run(user_id, conversation_id), name="coaching-summary"
        )
        self._tasks[conversation_id] = task
        task.add_done_callback(partial(self._done, conversation_id=conversation_id))
        return task

    def _done(self, task: asyncio.Task[None], *, conversation_id: UUID) -> None:
        """시작 전 취소도 등록을 해제하고 새 작업을 지우지 않으며 일반 예외만 기록한다."""
        if self._tasks.get(conversation_id) is task:
            del self._tasks[conversation_id]
            self._requested.discard(conversation_id)
        if not task.cancelled() and (error := task.exception()) is not None:
            log_failure(logger, "summary task", error)

    def _check_running(self, deadline: asyncio.Timeout) -> None:
        """취소를 삼킨 생성 함수나 종료·기한 뒤에 도착한 결과의 저장을 막는다."""
        task = asyncio.current_task()
        if self._closing or (task is not None and task.cancelling()):
            if deadline.expired() and not self._closing:
                raise TimeoutError
            raise asyncio.CancelledError
        # 동기 계산이 루프를 점유하면 timeout 콜백보다 결과가 먼저 돌아올 수 있다.
        when = deadline.when()
        if deadline.expired() or (
            when is not None and asyncio.get_running_loop().time() >= when
        ):
            raise TimeoutError

    async def _read(
        self, user_id: UUID, conversation_id: UUID
    ) -> HistorySnapshot | None:
        """조회용 세션을 즉시 닫아 LLM·재시도 대기 중 트랜잭션이 남지 않게 한다."""
        try:
            async with self._session_factory() as session:
                return await self._store.find_history_context(
                    session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
        except STORAGE_ERRORS as error:
            if not is_temporary_storage_error(error):
                raise
            log_failure(logger, "summary read", error)
            raise _RetryStorage from error

    async def _reconcile(
        self, user_id: UUID, conversation_id: UUID, work: _Work
    ) -> bool | None:
        """저장 반영은 True, 경쟁·삭제는 False, 기존 기준점 유지는 None으로 구분한다."""
        assert work.result is not None
        current = await self._read(user_id, conversation_id)
        if current is None:
            return False
        if current.summary == StoredSummary(work.result.text, work.plan.until_seq):
            return True
        if current.summary != work.snapshot.summary:
            return False
        return None

    async def _save(
        self,
        user_id: UUID,
        conversation_id: UUID,
        work: _Work,
        deadline: asyncio.Timeout,
    ) -> bool:
        """결과를 commit하며 오류 시 새 세션으로 이미 반영됐는지 먼저 확인한다."""
        assert work.result is not None
        if work.needs_reconcile:
            resolved = await self._reconcile(user_id, conversation_id, work)
            self._check_running(deadline)
            if resolved is not None:
                return resolved
        # 복구 조회가 실패하거나 commit 대기가 취소되어도 다음 시도는 조회부터 한다.
        work.needs_reconcile = True
        try:
            async with self._session_factory() as session:
                changed = await self._store.save_summary(
                    session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    snapshot=work.snapshot,
                    plan=work.plan,
                    result=work.result,
                    now=self._clock(),
                )
                self._check_running(deadline)
                await session.commit()
            work.needs_reconcile = False
            return changed
        except STORAGE_ERRORS as error:
            # 연결 장애만 재시도한다. SQL·검증·프로그램 오류와 취소는 그대로 전파한다.
            if not is_temporary_storage_error(error):
                raise
            log_failure(logger, "summary save", error)
            self._check_running(deadline)
            resolved = await self._reconcile(user_id, conversation_id, work)
            self._check_running(deadline)
            if resolved is not None:
                return resolved
            work.needs_reconcile = False
            raise _RetryStorage from error

    def _record_failed_scope(
        self, conversation_id: UUID, work: _Work, code: str
    ) -> None:
        """같은 범위의 형식 오류 횟수를 이어 기록하고 상한이면 정지 시각을 둔다.

        정지가 처음 되는 순간에만 failed_scope_exhausted warning을 남긴다.
        """
        previous = self._failed_scopes.get(conversation_id)
        count = 0
        if (
            previous is not None
            and previous.summary == work.snapshot.summary
            and previous.until_seq == work.plan.until_seq
        ):
            count = previous.format_failures
        if code in FORMAT_FAILURE_CODES:
            count += 1
        if count >= MAX_SAME_SCOPE_FORMAT_FAILURES:
            retry_at = math.inf
            if previous is None or previous.retry_at != math.inf:
                logger.warning(
                    "coaching summary outcome=failed_scope_exhausted code=%s", code
                )
        else:
            retry_at = self._monotonic() + self._cooldown_seconds
        self._failed_scopes[conversation_id] = _FailedScope(
            work.snapshot.summary, work.plan.until_seq, count, retry_at
        )

    async def _run(self, user_id: UUID, conversation_id: UUID) -> None:
        """고정 계획을 최대 세 번 시도하고 성공 후 밀린 다음 구간을 새로 판단한다."""
        functions = self._functions
        assert functions is not None
        work: _Work | None = None
        failures = 0
        while not self._closing:
            retry_after = 0.0
            try:
                deadline = asyncio.timeout(self._attempt_seconds)
                async with deadline, self._slot:
                    self._check_running(deadline)
                    if work is None:
                        self._requested.discard(conversation_id)
                        snapshot = await self._read(user_id, conversation_id)
                        if snapshot is None:
                            return
                        plan = functions.plan_summary(snapshot.summary, snapshot.turns)
                        if plan is None:
                            if conversation_id in self._requested:
                                continue
                            return
                        validate_plan(snapshot, plan, functions.policy_version)
                        failed = self._failed_scopes.get(conversation_id)
                        if (
                            failed is not None
                            and failed.summary == snapshot.summary
                            and failed.until_seq == plan.until_seq
                            and self._monotonic() < failed.retry_at
                        ):
                            logger.info("coaching summary outcome=skipped_failed_scope")
                            if conversation_id in self._requested:
                                continue
                            return
                        work = _Work(snapshot, plan)
                    if work.result is None:
                        generated = await functions.generate_summary(
                            work.snapshot.summary, work.plan
                        )
                        self._check_running(deadline)
                        work.result = generated
                    self._check_running(deadline)
                    validate_result(work.plan, work.result)
                    changed = not work.superseded and await self._save(
                        user_id, conversation_id, work, deadline
                    )
                    self._check_running(deadline)
                    if not changed:
                        # CAS 0건은 새 상태를 읽고 종료한다. 같은 계획을 즉시 반복하지 않는다.
                        work.superseded = True
                        await self._read(user_id, conversation_id)
                        logger.info("coaching summary outcome=superseded")
                        if conversation_id in self._requested:
                            work = None
                            failures = 0
                            continue
                        return
                # 저장 성공 후 밀린 다음 구간은 새 DB 상태와 새 계획으로 판단한다.
                logger.info("coaching summary outcome=saved")
                self._failed_scopes.pop(conversation_id, None)
                work = None
                failures = 0
                continue
            except asyncio.CancelledError:
                raise
            except SummaryError as error:
                retryable = error.retryable
                retry_after = error.retry_after or 0.0
                code = error.code
            except TimeoutError:
                retryable = True
                code = "attempt_timeout"
            except _RetryStorage:
                retryable = True
                code = "storage_unavailable"
            except Exception as error:
                # 계약 위반·예상 밖 버그는 완료 답변에 전파하지 않고 즉시 자동 재시도를 중지한다.
                retryable = False
                code = "internal"
                log_failure(logger, "summary", error)
            failures += 1
            logger.warning(
                "coaching summary outcome=failed code=%s retryable=%s attempt=%d",
                code,
                retryable,
                failures,
            )
            if not retryable:
                # 같은 기준 요약·대상만 생략하고 새 완료 예약은 원문 앞부분부터 다시 판단한다.
                if work is not None:
                    self._record_failed_scope(conversation_id, work, code)
                if conversation_id in self._requested:
                    work = None
                    failures = 0
                    continue
                return
            if failures >= 3:
                self._cooldowns[conversation_id] = (
                    self._monotonic() + self._cooldown_seconds
                )
                return
            await self._sleep(max(self._retry_delays[failures - 1], retry_after))

    def stop(self) -> None:
        """신규 예약을 차단하고 이미 등록한 작업을 한 번만 취소한다."""
        if not self._closing:
            self._closing = True
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()

    async def aclose(self, timeout: float = 5.0) -> None:  # noqa: ASYNC109 - 앱과 테스트에서 종료 상한을 전달한다.
        """작업을 취소한 뒤 지정 상한만 기다리고 미종료 작업의 참조는 유지한다."""
        self.stop()
        pending = {task for task in self.tasks if not task.done()}
        if pending:
            await asyncio.wait(pending, timeout=max(0.0, timeout))
