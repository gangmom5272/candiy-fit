"""한마디 전용 읽기 블록. 외부 연결의 반환은 이 블록이 소유한다."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import isfinite
from time import perf_counter as monotonic
from typing import Generic, TypeVar

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
)

from app.health.daily_message import DailyMessageMetric
from app.health.diagnostics import emit_health_event
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.lookup import lookup_boundary as _lookup_boundary

T = TypeVar("T")
S = TypeVar("S")


class ReadCleanupError(RuntimeError):
    """전체 실패. HealthDataLookupError와 달리 부분 성공으로 낮추면 안 된다."""

    def __init__(self) -> None:
        super().__init__("daily message connection cleanup incomplete")


@dataclass(frozen=True)
class CleanupResult:
    error: BaseException | None = None
    disposal_error: BaseException | None = None
    driver_closed: bool = False
    pool_blocked: bool = False
    tasks_done: bool = False
    cleanup_seconds: float = 0.0
    disposal_seconds: float | None = None
    cleanup_started_at: float | None = None
    disposal_started_at: float | None = None


def _known_cleanup_error(error: BaseException) -> bool:
    # 정리 중 ValidationError 등은 저장 데이터 오류가 아니라 프로그램 오류다.
    if isinstance(
        error,
        (OSError, TimeoutError, PoolTimeoutError, OperationalError, InterfaceError),
    ):
        return True
    if isinstance(error, DBAPIError):
        sqlstate = getattr(error.orig, "sqlstate", "") or ""
        return (
            error.connection_invalidated
            or sqlstate.startswith("08")
            or sqlstate in ("57014", "53300", "57P01", "57P02", "57P03")
        )
    return False


def _resolve_error(
    original: BaseException | None,
    cleanup: CleanupResult,
    metrics: tuple[DailyMessageMetric, ...],
) -> BaseException | None:
    if original is not None and not isinstance(
        original, (HealthDataLookupError, HealthDataNotReadyError)
    ):
        return original
    if cleanup.error is None:
        return original
    complete = (
        cleanup.driver_closed
        and cleanup.pool_blocked
        and cleanup.tasks_done
        and cleanup.disposal_error is None
    )
    if not complete or not _known_cleanup_error(cleanup.error):
        return ReadCleanupError()
    return HealthDataLookupError(metrics)


@dataclass(frozen=True)
class ReadBlockResult(Generic[T, S]):
    value: T
    score: S | None
    score_error: HealthDataLookupError | None
    cleanup_seconds: float


async def _wait_task(
    task: asyncio.Task, deadline: float, cancellations: list[asyncio.CancelledError]
) -> bool:
    """호출자 취소는 보존하고 종료 작업과 절대 기한은 유지한다."""
    while not task.done():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
        except asyncio.CancelledError as error:
            cancellations.append(error)
        else:
            if not done:
                return False
    return True


def _task_error(task: asyncio.Task) -> BaseException | None:
    try:
        task.result()
    except BaseException as error:
        return error
    return None


async def _finish_read(
    connection: AsyncConnection,
    transaction: AsyncTransaction | None,
    session: AsyncSession | None,
    raw,
    driver,
    cancellations: list[asyncio.CancelledError],
    cleanup_timeout: float,
    disposal_timeout: float,
) -> CleanupResult:
    started = monotonic()

    async def normal_close():
        if session is not None:
            await session.close()
        if transaction is not None and transaction.is_active:
            await transaction.rollback()
        # 반환 전 마지막 확인. 반환 뒤에는 이전 드라이버를 종료하지 않는다.
        if monotonic() - started >= cleanup_timeout:
            raise TimeoutError
        await connection.close()

    task = asyncio.create_task(normal_close(), name="daily-message-cleanup-normal")
    finished = await _wait_task(task, started + cleanup_timeout, cancellations)
    error = _task_error(task) if finished else TimeoutError()
    elapsed = monotonic() - started
    if error is None and elapsed <= cleanup_timeout:
        return CleanupResult(
            cleanup_seconds=elapsed, tasks_done=True, cleanup_started_at=started
        )
    if error is None:
        error = TimeoutError()

    disposal_started = monotonic()
    task.cancel()
    driver_closed = False
    pool_blocked = False
    disposal_error = None
    try:
        if raw is None or driver is None:
            raise ReadCleanupError()
        if connection.closed:
            # 드라이버 상태와 무관하게 반환이 끝난 대여는 소유권을 잃었다.
            raise ReadCleanupError()
        if not driver.is_closed():
            if not raw.is_valid:
                # 이미 반환되었거나 소유권이 끊긴 연결에는 terminate 금지.
                raise ReadCleanupError()
            driver.terminate()
        driver_closed = driver.is_closed()
        try:
            # 공개 PoolProxiedConnection API. greenlet 밖에서 무효화해
            # 종료된 드라이버의 내부 shield 작업을 새로 만들지 않는다.
            if raw.is_valid:
                raw.invalidate()
            pool_blocked = not raw.is_valid
        finally:
            if driver_closed and not connection.closed:
                if raw.is_valid:
                    # 무효화 자체가 실패해도 풀과의 연결을 끊은 뒤 종료한다.
                    raw.detach()
                    pool_blocked = raw.is_detached
                    raw.close()
                await connection.close()
        if not await _wait_task(
            task, disposal_started + disposal_timeout, cancellations
        ):
            raise TimeoutError
        _task_error(task)
    except BaseException as failure:
        disposal_error = failure
        if isinstance(failure, asyncio.CancelledError):
            cancellations.append(failure)
    if not task.done():
        if not task.cancelling():
            task.cancel()
        try:
            await asyncio.wait((task,), timeout=0)
        except asyncio.CancelledError as cancel:
            cancellations.append(cancel)
        task.add_done_callback(_task_error)
    disposal_seconds = monotonic() - disposal_started
    if disposal_seconds > disposal_timeout and disposal_error is None:
        disposal_error = TimeoutError()
    return CleanupResult(
        error=error,
        disposal_error=disposal_error,
        driver_closed=driver_closed,
        pool_blocked=pool_blocked,
        tasks_done=task.done(),
        cleanup_seconds=elapsed,
        disposal_seconds=disposal_seconds,
        cleanup_started_at=started,
        disposal_started_at=disposal_started,
    )


async def read_daily_message_block(
    engine: AsyncEngine,
    read: Callable[[AsyncSession], Awaitable[T]],
    *,
    request_id: str,
    metric: DailyMessageMetric | None = None,
    score_read: Callable[[AsyncSession, T], Awaitable[S]] | None = None,
    cleanup_timeout: float = 1.0,
    disposal_timeout: float = 1.0,
) -> ReadBlockResult[T, S]:
    """read/score_read가 캡처한 요청 날짜를 사용한다. 결과는 정리 뒤 반환한다."""
    if score_read is not None and metric != "sleeps":
        raise ValueError("score read requires sleeps scope")
    if any(
        not isfinite(limit) or limit <= 0
        for limit in (cleanup_timeout, disposal_timeout)
    ):
        raise ValueError("cleanup limits must be finite and positive")
    connection = None
    transaction = None
    session = None
    score = None
    score_error = None
    block_started = monotonic()
    required_finished = None
    score_started = None
    score_finished = None
    original = None
    cancellations = []
    raw = None
    driver = None
    metrics = (metric,) if metric is not None else ()
    try:
        async with _lookup_boundary(metrics, clock=monotonic):
            connection = await engine.connect()
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection, join_transaction_mode="rollback_only"
            )
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            value = await read(session)
        required_finished = monotonic()
        if score_read is not None:
            score_started = monotonic()
            try:
                async with _lookup_boundary(metrics, clock=monotonic):
                    score = await score_read(session, value)
            except HealthDataLookupError as error:
                score_error = error
            finally:
                score_finished = monotonic()
    except BaseException as error:
        # 자원 경계에서만 전체 오류/취소를 보존하고 정리 후 다시 전파한다.
        original = error
    if required_finished is None:
        required_finished = monotonic()
    cleanup = CleanupResult()
    if connection is not None:
        cleanup = await _finish_read(
            connection,
            transaction,
            session,
            raw,
            driver,
            cancellations,
            cleanup_timeout,
            disposal_timeout,
        )
    if cancellations and not isinstance(original, asyncio.CancelledError):
        original = cancellations[0]
    emit_health_event(
        "daily_message_read_finished",
        **{
            "request_id": request_id,
            "metric": metric,
            "original_kind": type(original).__name__ if original else None,
            "score_error_kind": type(score_error).__name__ if score_error else None,
            "score_error_reason": score_error.reason if score_error else None,
            "cleanup_kind": type(cleanup.error).__name__ if cleanup.error else None,
            "disposal_kind": type(cleanup.disposal_error).__name__
            if cleanup.disposal_error
            else None,
            "cleanup_seconds": cleanup.cleanup_seconds,
            "disposal_seconds": cleanup.disposal_seconds,
            "driver_closed": cleanup.driver_closed,
            "pool_blocked": cleanup.pool_blocked,
            "tasks_done": cleanup.tasks_done,
            "cleanup_limit_seconds": cleanup_timeout,
            "disposal_limit_seconds": disposal_timeout,
            "block_started_at": block_started,
            "block_seconds": monotonic() - block_started,
            "required_lookup_seconds": required_finished - block_started,
            "score_lookup_seconds": score_finished - score_started
            if score_finished is not None
            else None,
            "cleanup_started_at": cleanup.cleanup_started_at,
            "disposal_started_at": cleanup.disposal_started_at,
        },
    )
    error = _resolve_error(original, cleanup, metrics)
    if error is not None:
        raise error
    return ReadBlockResult(value, score, score_error, cleanup.cleanup_seconds)
