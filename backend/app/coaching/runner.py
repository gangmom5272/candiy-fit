"""WS 연결과 독립적으로 접수된 코칭 요청의 생성·저장·종결을 관리한다."""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol, TypeVar, get_args
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.connection import EventChannel, log_failure
from app.coaching.events import PublicEventSink
from app.coaching.history import HistorySnapshot
from app.coaching.repository import EvidenceInfo, RequestState, ToolCallInfo
from app.coaching.schemas import (
    RequestFailure,
    RequestIds,
    failure_from_coach_error,
    partition_evidence,
)
from app.coaching.summary import SummaryManager
from app.coaching.tool_call_log import ToolCallLog
from app.users import service as user_service
from coach_ai.agent.context import CoachContext, CoachProfile
from coach_ai.agent.graph import CoachResult
from coach_ai.agent.state import FinishReason

SHUTDOWN_WAIT_SECONDS = 5.0
FINALIZE_TIMEOUT_SECONDS = 5.0
INTERRUPT_SAVE_TIMEOUT_SECONDS = 3.0
TOOL_CALL_SAVE_TIMEOUT_SECONDS = 3.0
logger = logging.getLogger(__name__)
T = TypeVar("T")


def utc_now() -> datetime:
    """현재 시각을 시간대가 명시된 UTC datetime으로 반환한다."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class GenerationJob:
    """접수된 요청과 질문을 보낸 연결·시작 시각을 불변 값으로 전달한다."""

    user_id: UUID
    request: RequestState
    question: str
    channel: EventChannel
    started_at: float
    accepted_at: float


class CoachingStore(Protocol):
    """실행기가 세션과 commit을 소유할 수 있도록 저장소 함수 계약을 정의한다."""

    async def mark_generating(self, session: AsyncSession, *, request_id: UUID) -> bool:
        """접수 상태를 생성 중으로 바꾸고 조건부 변경 여부를 반환한다."""
        ...

    async def find_history_context(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        conversation_id: UUID,
        before_sequence_no: int | None = None,
    ) -> HistorySnapshot | None:
        """소유권 있는 요약과 이전 완료 쌍을 동일 스냅샷으로 반환한다."""
        ...

    async def complete(
        self,
        session: AsyncSession,
        *,
        request_id: UUID,
        answer: str,
        now: datetime,
        evidence: Sequence[EvidenceInfo] = (),
    ) -> bool:
        """진행 중 요청의 답변과 완료 상태를 저장하고 변경 여부를 반환한다."""
        ...

    async def fail(
        self,
        session: AsyncSession,
        *,
        request_id: UUID,
        error_code: str,
        error_context: Mapping[str, Any] | None,
        partial_answer: str,
        now: datetime,
    ) -> bool:
        """진행 중 요청의 오류·부분 답변을 저장하고 변경 여부를 반환한다."""
        ...


class ToolCallStore(Protocol):
    """요청 하나의 도구 호출 기록을 저장하는 저장소 계약을 정의한다."""

    async def save_tool_calls(
        self, session: AsyncSession, *, rows: Sequence[ToolCallInfo]
    ) -> None:
        """기록 행을 commit 없이 넣는다."""
        ...


@dataclass(frozen=True, slots=True)
class _Completed:
    """성공적으로 생성된 최종 답변·검증된 근거·AI 종료 사유를 보관한다."""

    answer: str
    evidence: tuple[EvidenceInfo, ...]
    finish_reason: str = "-"


@dataclass(frozen=True, slots=True)
class _Failed:
    """공개 오류·저장할 부분 답변·AI 종료 사유(AI 결과가 없으면 하이픈)를 보관한다."""

    failure: RequestFailure
    partial_answer: str
    finish_reason: str = "-"


_Outcome = _Completed | _Failed


class CoachingRunner:
    """접수된 질문의 생성과 취소에 보호된 저장을 앱 수명 동안 관리한다.

    작업은 WS 연결과 무관하게 실행·저장된다. 세션은 단계마다 열고 직접 commit한다.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        agent: CoachAgent,
        store: CoachingStore = repository,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        finalize_timeout: float = FINALIZE_TIMEOUT_SECONDS,
        interrupt_timeout: float = INTERRUPT_SAVE_TIMEOUT_SECONDS,
        summaries: SummaryManager | None = None,
        tool_call_store: ToolCallStore | None = None,
        tool_call_timeout: float = TOOL_CALL_SAVE_TIMEOUT_SECONDS,
    ) -> None:
        """생성·중단 저장 의존성과 상한을 받고 작업 참조·시작 여부·종료 상태를 준비한다.

        tool_call_store가 없으면 도구 호출 기록은 AI 패키지의 기본 메모리 기록기에 둔다.
        """
        self._session_factory = session_factory
        self._agent = agent
        self._store = store
        self._clock = clock
        self._monotonic = monotonic
        self._finalize_timeout = finalize_timeout
        self._interrupt_timeout = interrupt_timeout
        self._summaries = summaries
        self._tool_call_store = tool_call_store
        self._tool_call_timeout = tool_call_timeout
        self._tasks: set[asyncio.Task[None]] = set()
        self._started: set[asyncio.Task[None]] = set()
        self._saves: set[asyncio.Task[Any]] = set()
        self._closing = False

    def is_ready(self) -> bool:
        """에이전트의 포트·공유 모델 준비 여부를 반환한다."""
        return self._agent.ensure_ready()

    @property
    def active_count(self) -> int:
        """현재 참조 중인 미완료 작업 수를 반환한다."""
        return sum(not task.done() for task in self._tasks)

    def submit(self, job: GenerationJob) -> asyncio.Task[None]:
        """생성 요청을 완료 콜백에 연결하고 닫는 중이면 시작 전 취소 추적 없이 중단 저장만 한다."""
        generation_job = None if self._closing else job
        if self._closing:
            sink = PublicEventSink(
                job.channel,
                RequestIds.from_state(job.request),
                monotonic=self._monotonic,
            )
            work = self._save_interrupted(job, sink)
        else:
            work = self._run(job)
        task = asyncio.create_task(work, name=f"coaching-{job.request.id}")
        self._tasks.add(task)
        task.add_done_callback(partial(self._task_done, job=generation_job))
        return task

    def _task_done(
        self, task: asyncio.Task[None], *, job: GenerationJob | None
    ) -> None:
        """작업 참조를 제거하고 첫 실행 전 취소만 중단 저장하며 예상 밖 예외를 기록한다.

        종료 중 접수한 저장 작업은 job이 없어 제외하며 루프가 닫혔으면 로그만 남긴다.
        """
        self._tasks.discard(task)
        started = task in self._started
        self._started.discard(task)
        if task.cancelled():
            if job is not None and not started:
                sink = PublicEventSink(
                    job.channel,
                    RequestIds.from_state(job.request),
                    monotonic=self._monotonic,
                )
                work = self._save_interrupted(job, sink)
                try:
                    save = asyncio.create_task(work, name="coaching-interrupt-save")
                except RuntimeError as error:
                    work.close()
                    log_failure(logger, "interrupt", error)
                else:
                    self._saves.add(save)
                    save.add_done_callback(self._save_done)
        else:
            error = task.exception()
            if error is not None:
                log_failure(logger, "generation task", error)

    def _save_done(self, task: asyncio.Task[Any]) -> None:
        """보호 저장의 강한 참조를 제거하고 관측되지 않은 일반 오류를 기록한다."""
        self._saves.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                log_failure(logger, "save task", error)

    async def _shielded(self, coro: Coroutine[Any, Any, T]) -> T:
        """자체 상한이 있는 저장을 보호하고 첫 취소는 저장 종료 후 다시 올린다.

        재차 취소되면 즉시 취소를 전파하되 안쪽 작업은 참조를 유지하며 계속 실행한다.
        """
        task = asyncio.create_task(coro, name="coaching-save")
        self._saves.add(task)
        task.add_done_callback(self._save_done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            try:
                await asyncio.shield(task)
            finally:
                raise cancelled

    async def _run(self, job: GenerationJob) -> None:
        """본문 시작을 기록하고 취소 시 중단 저장하며 종결 저장·송신·기록은 보호한다.

        도구 호출 기록은 요청을 종결(중단 포함)한 뒤 한 번에 저장한다.
        """
        task = asyncio.current_task()
        if task is not None:
            self._started.add(task)
        sink = PublicEventSink(
            job.channel, RequestIds.from_state(job.request), monotonic=self._monotonic
        )
        deadline = asyncio.timeout(
            max(0.0, (job.request.deadline_at - self._clock()).total_seconds())
        )
        call_log = None if self._tool_call_store is None else ToolCallLog()
        try:
            sink.send_stage("reading_history")
            async with deadline:
                async with self._session_factory() as session:
                    changed = await self._store.mark_generating(
                        session, request_id=job.request.id
                    )
                    history = (
                        await self._store.find_history_context(
                            session,
                            user_id=job.user_id,
                            conversation_id=job.request.conversation_id,
                            before_sequence_no=job.request.user_message.sequence_no,
                        )
                        if changed
                        else None
                    )
                    profile = None
                    if history is not None:
                        body = await user_service.get_body_profile(session, job.user_id)
                        profile = CoachProfile(
                            height_cm=body.height_cm,
                            weight_kg=body.weight_kg,
                            bmi=body.bmi,
                        )
                    await session.commit()
                if not changed:
                    logger.info(
                        "coaching request not accepted request_id=%s", job.request.id
                    )
                    self._log_finished(job, sink, "not_accepted", "-")
                    return
                if history is None:
                    raise RuntimeError("owned conversation is unavailable")
                # 공유 모델의 프로세스 공용 LIMITER 슬롯 대기도 이 생성 기한에 포함된다.
                result = await self._agent.run_with_history(
                    ctx=CoachContext(
                        user_id=str(job.user_id),
                        conversation_id=str(job.request.conversation_id),
                        request_id=str(job.request.id),
                        profile=profile,
                    ),
                    question=job.question,
                    history=history,
                    sink=sink,
                    # 답변의 "오늘"과 요약의 answered_on을 같은 접수 시각으로 맞춘다.
                    now=job.request.created_at,
                    message_id=str(job.request.assistant_message.id),
                    audit=call_log,
                )
            outcome = self._outcome(result, sink)
        except asyncio.CancelledError:
            await self._save_interrupted(job, sink)
            await self._save_tool_calls(call_log)
            raise
        except TimeoutError as error:
            if deadline.expired():
                outcome = _Failed(RequestFailure("GENERATION_TIMEOUT"), sink.answer)
            else:
                log_failure(logger, "generation", error)
                outcome = _Failed(RequestFailure("INTERNAL_ERROR"), sink.answer)
        except Exception as error:
            # 최상위 생성 경계에서 DB·포트·버그를 공개 오류로 닫되 앱 종료 취소는 전파한다.
            log_failure(logger, "generation", error)
            outcome = _Failed(RequestFailure("INTERNAL_ERROR"), sink.answer)
        # 기한을 넘긴 요청도 GENERATION_TIMEOUT 자체는 저장해야 하므로 밖에서 종결한다.
        await self._shielded(self._finish_and_log(job, sink, outcome))
        await self._save_tool_calls(call_log)

    async def _save_tool_calls(self, call_log: ToolCallLog | None) -> None:
        """모은 도구 호출 기록을 새 세션에서 한 번에 저장하고 실패는 종류·위치만 로그에 기록한다.

        종결 저장·이벤트 송신 뒤에 실행하므로 저장 결과가 답변과 요청 상태를 바꾸지 않는다.
        기록이 없으면 세션을 열지 않는다.
        """
        store = self._tool_call_store
        if call_log is None or store is None or not call_log.records:
            return

        async def save() -> None:
            """별도 상한으로 행 변환·저장·commit을 실행한다."""
            try:
                async with asyncio.timeout(self._tool_call_timeout):
                    async with self._session_factory() as session:
                        await store.save_tool_calls(session, rows=call_log.rows())
                        await session.commit()
            except Exception as error:
                # 기록 저장 경계의 일반 오류·시간 초과는 로그로만 알리고 취소는 전파한다.
                log_failure(logger, "tool call log", error)

        await self._shielded(save())

    async def _finish_and_log(
        self, job: GenerationJob, sink: PublicEventSink, outcome: _Outcome
    ) -> None:
        """종결 저장·이벤트 송신과 시간·AI 종료 사유 기록을 하나의 보호 작업으로 실행한다."""
        label, code = await self._finish(job, sink, outcome)
        self._log_finished(job, sink, label, code, finish_reason=outcome.finish_reason)

    async def _save_interrupted(
        self, job: GenerationJob, sink: PublicEventSink
    ) -> None:
        """앱 종료로 취소된 요청을 GENERATION_INTERRUPTED와 부분 답변으로 보호 저장한다.

        저장되면 error를 보내고 실패하면 로그만 남겨 다음 기동 정리에 맡긴다.
        취소 외 예외는 올리지 않는다.
        """

        async def save_and_report() -> None:
            """별도 상한과 세션으로 중단을 commit한 뒤 오류 이벤트와 결과를 기록한다."""
            try:
                async with asyncio.timeout(self._interrupt_timeout):
                    async with self._session_factory() as session:
                        changed = await self._store.fail(
                            session,
                            request_id=job.request.id,
                            error_code="GENERATION_INTERRUPTED",
                            error_context=None,
                            partial_answer=sink.answer,
                            now=self._clock(),
                        )
                        await session.commit()
                if changed:
                    sink.send_error(RequestFailure("GENERATION_INTERRUPTED"))
                label = "interrupted" if changed else "already_finished"
            except Exception as error:
                # 중단 저장 경계의 실패는 기동 정리에 맡기되 취소는 전파한다.
                log_failure(logger, "interrupt", error)
                label = "interrupt_failed"
            self._log_finished(job, sink, label, "GENERATION_INTERRUPTED")

        await self._shielded(save_and_report())

    @staticmethod
    def _outcome(result: CoachResult, sink: PublicEventSink) -> _Outcome:
        """근거는 형식이 맞는 항목만 남기고 완료한다."""
        if result.finish_reason in {"stop", "length", "guard", "tool_limit"}:
            partition = partition_evidence(
                [item.event_item() for item in result.evidence]
            )
            if partition.skipped > 0:
                logger.warning(
                    "coaching evidence skipped=%d metrics=%s",
                    partition.skipped,
                    ",".join(partition.skipped_metrics),
                )
            return _Completed(
                result.answer, partition.valid, finish_reason=result.finish_reason
            )
        code_metrics = sink.coach_error or (result.error_code, ())
        return _Failed(
            failure_from_coach_error(*code_metrics),
            result.answer,
            finish_reason=result.finish_reason,
        )

    async def _finish(
        self, job: GenerationJob, sink: PublicEventSink, outcome: _Outcome
    ) -> tuple[str, str]:
        """결과를 조건부 저장·commit한 뒤 completed 또는 error를 보낸다.

        저장 실패 시 PERSISTENCE_FAILED를 보내고 새 세션으로 보조 저장을 한 번 시도한다.
        반환하는 결과 분류와 공개 코드는 시간 기록에 사용한다.
        """
        try:
            async with asyncio.timeout(self._finalize_timeout):
                now = self._clock()
                async with self._session_factory() as session:
                    if isinstance(outcome, _Completed):
                        changed = await self._store.complete(
                            session,
                            request_id=job.request.id,
                            answer=outcome.answer,
                            evidence=outcome.evidence,
                            now=now,
                        )
                    else:
                        changed = await self._store.fail(
                            session,
                            request_id=job.request.id,
                            error_code=outcome.failure.code,
                            error_context=outcome.failure.error_context(),
                            partial_answer=outcome.partial_answer,
                            now=now,
                        )
                    await session.commit()
        except Exception as error:
            # 저장 경계의 일반 오류·시간 초과는 복구하되 CancelledError는 그대로 전파한다.
            log_failure(logger, "finalize", error)
            sink.send_error(RequestFailure("PERSISTENCE_FAILED"))
            partial = (
                outcome.answer
                if isinstance(outcome, _Completed)
                else outcome.partial_answer
            )
            await self._save_persistence_failure(job, partial)
            return "persistence_failed", "PERSISTENCE_FAILED"
        if not changed:
            logger.info(
                "coaching request already finished request_id=%s", job.request.id
            )
            return "already_finished", "-"
        if isinstance(outcome, _Completed):
            sink.send_completed(
                job.request.assistant_message,
                outcome.answer,
                now,
                evidence=outcome.evidence,
            )
            if self._summaries is not None:
                try:
                    self._summaries.submit(
                        user_id=job.user_id,
                        conversation_id=job.request.conversation_id,
                    )
                except Exception as error:
                    # 요약 예약의 실패는 이미 commit한 완료 답변·종결 이벤트를 바꾸지 않는다.
                    log_failure(logger, "summary submit", error)
            return "completed", "-"
        sink.send_error(outcome.failure)
        return "failed", outcome.failure.code

    async def _save_persistence_failure(self, job: GenerationJob, partial: str) -> None:
        """새 세션·동일 상한으로 저장 실패를 한 번 저장하고 재실패는 로그만 남긴다."""
        try:
            async with asyncio.timeout(self._finalize_timeout):
                async with self._session_factory() as session:
                    await self._store.fail(
                        session,
                        request_id=job.request.id,
                        error_code="PERSISTENCE_FAILED",
                        error_context=None,
                        partial_answer=partial,
                        now=self._clock(),
                    )
                    await session.commit()
        except Exception as error:
            # 보조 저장까지 실패하면 기동 정리가 복구하도록 두고 작업 예외는 누출하지 않는다.
            log_failure(logger, "finalize", error)

    def _log_finished(
        self,
        job: GenerationJob,
        sink: PublicEventSink,
        outcome: str,
        code: str,
        *,
        finish_reason: str = "-",
    ) -> None:
        """질문 시작 기준의 정수 ms와 요청 ID·결과·공개 코드·AI 종료 사유만 INFO로 남긴다.

        종료 사유가 AI 패키지의 허용 값(하이픈 제외) 밖이면 `unknown`으로 바꿔 남긴다.
        """
        if finish_reason != "-" and finish_reason not in get_args(FinishReason):
            finish_reason = "unknown"

        def milliseconds(value: float | None) -> int | str:
            """관측 시각을 시작 대비 정수 ms로 바꾸고 미관측 값은 하이픈으로 표시한다."""
            return "-" if value is None else int((value - job.started_at) * 1000)

        logger.info(
            "coaching request finished request_id=%s outcome=%s code=%s "
            "accepted_ms=%s first_status_ms=%s first_delta_ms=%s finished_ms=%s "
            "finish_reason=%s",
            job.request.id,
            outcome,
            code,
            milliseconds(job.accepted_at),
            milliseconds(sink.first_status_at),
            milliseconds(sink.first_delta_at),
            milliseconds(self._monotonic()),
            finish_reason,
        )

    async def aclose(self, timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:  # noqa: ASYNC109 - 앱 종료 대기 상한을 받는 공개 계약이다.
        """기존 생성만 한 번 취소하고 보호 저장과 뒤늦은 접수를 상한까지 기다린다.

        상한 후에도 남은 작업은 유지하며 재호출은 추가 취소 없이 다시 기다린다.
        """
        if not self._closing:
            self._closing = True
            if self._summaries is not None:
                self._summaries.stop()
            for task in tuple(self._tasks):
                if not task.done():
                    task.cancel()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # 중단 저장과 종료 중 접수는 취소하지 않고 매번 새 작업 집합으로 기다린다.
        while (remaining := deadline - loop.time()) > 0:
            summary_tasks = (
                self._summaries.tasks if self._summaries is not None else frozenset()
            )
            pending = {
                task
                for task in self._tasks | self._saves | summary_tasks
                if not task.done()
            }
            if not pending:
                return
            await asyncio.wait(pending, timeout=remaining)
