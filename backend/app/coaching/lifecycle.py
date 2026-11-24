"""앱 수명 동안 코칭 처리기·실행기·에이전트를 조립하고 정리한다."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from typing import Protocol

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.coaching import repository
from app.coaching.agent import CoachAgent, default_agent
from app.coaching.connection import log_failure
from app.coaching.runner import CoachingRunner, utc_now
from app.coaching.service import CoachingQuestionHandler
from app.coaching.summary import SummaryManager
from app.coaching.summary_adapter import SummaryFunctions, default_summary_functions
from app.core.config import Settings

STARTUP_CLEANUP_TIMEOUT_SECONDS = 5.0
CLEANUP_RETRY_SECONDS = 10.0
EXPIRY_INTERVAL_SECONDS = 10.0
EXPIRY_GRACE_SECONDS = 20.0
COACHING_LOGGER = "app.coaching"
_COACHING_HANDLER_NAME = "coaching-stdout"
logger = logging.getLogger(__name__)


def configure_coaching_logging() -> None:
    """app.coaching 로거의 INFO 이상을 표준 출력으로 내보내고, 이미 설정했으면 아무것도 하지 않는다.

    다른 로거·루트 로거·uvicorn 설정은 바꾸지 않는다.
    """
    coaching_logger = logging.getLogger(COACHING_LOGGER)
    coaching_logger.setLevel(logging.INFO)
    if any(h.get_name() == _COACHING_HANDLER_NAME for h in coaching_logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_COACHING_HANDLER_NAME)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    # propagate는 기본 True로 둔다: 운영에는 루트 핸들러가 없어 중복 출력이 없고 pytest caplog가 계속 잡는다.
    coaching_logger.addHandler(handler)


class InterruptStore(Protocol):
    """기동 기준 시각 이전의 진행 요청을 닫는 저장소 계약을 정의한다."""

    async def interrupt_in_progress(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        created_before: datetime | None = None,
    ) -> int:
        """기준 이전의 진행 요청을 중단 처리하고 commit 없이 변경 개수를 반환한다."""
        ...


async def interrupt_stale_requests(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    created_before: datetime,
    now: datetime,
    timeout: float,  # noqa: ASYNC109 - 기동 정리 계약의 상한을 받아 asyncio.timeout으로 적용한다.
    store: InterruptStore = repository,
) -> int:
    """이번 시작 이전에 접수돼 진행 중으로 남은 요청을 GENERATION_INTERRUPTED로 닫고 개수를 반환한다.

    세션 진입부터 commit까지 지정 상한을 적용하며 실패와 취소는 호출자에게 전파한다.
    백엔드 프로세스(워커) 1개 전제다. 시작 전에 접수된 진행 요청을 모두 이전 프로세스 것으로 보고 닫는다.
    워커를 늘리면 다른 워커의 정상 요청을 끊으므로 `backend/Dockerfile`의 `--workers 1`을 유지한다.
    """
    async with asyncio.timeout(timeout):
        async with session_factory() as session:
            changed = await store.interrupt_in_progress(
                session, now=now, created_before=created_before
            )
            await session.commit()
    return changed


class ExpiryStore(Protocol):
    """생성 기한이 지난 진행 요청을 닫는 저장소 계약을 정의한다."""

    async def fail_expired_requests(
        self, session: AsyncSession, *, now: datetime, expired_before: datetime
    ) -> int:
        """기한이 기준 시각보다 앞선 진행 요청을 시간 초과 처리하고 commit 없이 개수를 반환한다."""
        ...


async def close_expired_requests(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    now: datetime,
    expired_before: datetime,
    timeout: float,  # noqa: ASYNC109 - 정리 계약의 상한을 받아 asyncio.timeout으로 적용한다.
    store: ExpiryStore = repository,
) -> int:
    """기한과 유예가 지난 진행 요청을 한 번 닫고 commit한 뒤 변경 개수를 반환한다. 실패와 취소는 호출자에게 전파한다."""
    async with asyncio.timeout(timeout):
        async with session_factory() as session:
            changed = await store.fail_expired_requests(
                session, now=now, expired_before=expired_before
            )
            await session.commit()
    return changed


async def _expiry_loop(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    expire: Callable[..., Awaitable[int]],
    clock: Callable[[], datetime],
    interval: float,
    grace: float,
    timeout: float,  # noqa: ASYNC109 - 한 번의 정리 상한을 expire에 넘긴다.
) -> None:
    """주기마다 기한과 유예가 지난 진행 요청을 닫고 일반 오류는 기록 후 계속하며 취소는 전파한다."""
    while True:
        await asyncio.sleep(interval)
        try:
            now = clock()
            changed = await expire(
                session_factory,
                now=now,
                expired_before=now - timedelta(seconds=grace),
                timeout=timeout,
            )
        except Exception as error:
            # 주기 정리 경계에서 일반 오류만 기록하고 취소는 전파한다.
            log_failure(logger, "expiry cleanup", error)
        else:
            if changed >= 1:
                # 정리했다면 실행기가 정상 종결하지 못한 요청이 있었다는 뜻이라 warning으로 남긴다.
                logger.warning("coaching expiry cleanup failed=%d", changed)


async def _cleanup_loop(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    started_at: datetime,
    cleanup: Callable[..., Awaitable[int]],
    clock: Callable[[], datetime],
    cleanup_timeout: float,
    retry_seconds: float,
) -> None:
    """고정 기동 시각으로 정리를 즉시 시도하고 성공할 때까지 재시도하며 취소는 전파한다."""
    while True:
        try:
            changed = await cleanup(
                session_factory,
                created_before=started_at,
                now=clock(),
                timeout=cleanup_timeout,
            )
        except Exception as error:
            # 기동 정리 경계에서 DB 복구를 기다리며 일반 오류만 기록하고 취소는 전파한다.
            log_failure(logger, "startup cleanup", error)
            await asyncio.sleep(retry_seconds)
        else:
            logger.info("coaching startup cleanup interrupted=%d", changed)
            return


@asynccontextmanager
async def coaching_lifespan(
    app: FastAPI,
    settings: Settings,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    agent: CoachAgent | None = None,
    engine: AsyncEngine | None = None,
    cleanup: Callable[..., Awaitable[int]] = interrupt_stale_requests,
    clock: Callable[[], datetime] = utc_now,
    cleanup_timeout: float = STARTUP_CLEANUP_TIMEOUT_SECONDS,
    retry_seconds: float = CLEANUP_RETRY_SECONDS,
    expire: Callable[..., Awaitable[int]] = close_expired_requests,
    expiry_interval: float = EXPIRY_INTERVAL_SECONDS,
    expiry_grace: float = EXPIRY_GRACE_SECONDS,
    summary_functions: SummaryFunctions | None = None,
) -> AsyncIterator[CoachingRunner]:
    """코칭 등록 후 기동 정리를 기다리지 않고 앱을 열며 종료 시 모든 자원을 순서대로 닫는다.

    백엔드 프로세스(워커) 1개 전제다. 시작 전에 접수된 진행 요청을 모두 이전 프로세스 것으로 보고 닫는다.
    워커를 늘리면 다른 워커의 정상 요청을 끊으므로 `backend/Dockerfile`의 `--workers 1`을 유지한다.
    기한이 지난 진행 요청은 주기 정리가 닫는다(기한만 보므로 워커 수와 무관).
    시작할 때 app.coaching 로거의 INFO 출력을 켠다(configure_coaching_logging).
    도구 호출 기록은 dev·prod에서 coaching_tool_call에 저장하고 local·test에서는 AI 메모리 기록기에 둔다.
    """
    configure_coaching_logging()
    started_at = clock()
    agent = (
        agent
        if agent is not None
        else default_agent(settings, session_factory, engine=engine)
    )
    summaries = SummaryManager(
        session_factory=session_factory,
        functions=summary_functions
        if summary_functions is not None
        else default_summary_functions(model_provider=agent.get_model),
        clock=clock,
    )
    runner = CoachingRunner(
        session_factory=session_factory,
        agent=agent,
        summaries=summaries,
        tool_call_store=None if settings.app_env in {"local", "test"} else repository,
    )
    handler = CoachingQuestionHandler(session_factory=session_factory, runner=runner)
    app.state.coaching_runner = runner
    app.state.coaching_question_handler = handler
    cleanup_task = asyncio.create_task(
        _cleanup_loop(
            session_factory,
            started_at=started_at,
            cleanup=cleanup,
            clock=clock,
            cleanup_timeout=cleanup_timeout,
            retry_seconds=retry_seconds,
        ),
        name="coaching-startup-cleanup",
    )
    expiry_task = asyncio.create_task(
        _expiry_loop(
            session_factory,
            expire=expire,
            clock=clock,
            interval=expiry_interval,
            grace=expiry_grace,
            timeout=cleanup_timeout,
        ),
        name="coaching-expiry-cleanup",
    )
    try:
        yield runner
    finally:
        try:
            app.state.coaching_question_handler = None
        finally:
            try:
                cleanup_task.cancel()
                expiry_task.cancel()
                await asyncio.gather(cleanup_task, expiry_task, return_exceptions=True)
            finally:
                try:
                    await runner.aclose()
                finally:
                    await agent.aclose()
