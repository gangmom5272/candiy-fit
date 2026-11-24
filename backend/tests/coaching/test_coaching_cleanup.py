"""DB·네트워크·실제 LLM 없이 서버 종료와 기동 정리의 경계를 검사한다."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from functools import wraps
from typing import ParamSpec, TypeVar
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.lifecycle import (
    _expiry_loop,
    close_expired_requests,
    coaching_lifespan,
    interrupt_stale_requests,
)
from app.coaching.runner import CoachingRunner, GenerationJob
from app.coaching.schemas import request_state_data
from app.coaching.service import CoachingQuestionHandler
from app.core.config import Settings
from app.main import create_app
from tests.coaching.fakes import (
    NOW,
    CountingModelFactory,
    FakeSessionFactory,
    FakeStore,
    RecordingChannel,
    RecordingPortFactory,
    RollbackSessionFactory,
    ScriptedRunCoach,
    fixed_now,
)

TestArgs = ParamSpec("TestArgs")
LifespanValue = TypeVar("LifespanValue")
PARTIAL = "합성 비공개 부분 답변"


def bounded_test(
    test: Callable[TestArgs, Awaitable[None]],
) -> Callable[TestArgs, Awaitable[None]]:
    """테스트 전체에 1초 미만 상한을 적용해 비동기 교착을 실패로 드러낸다."""

    @wraps(test)
    async def bounded(*args: TestArgs.args, **kwargs: TestArgs.kwargs) -> None:
        """fixture를 유지한 채 같은 태스크에서 테스트를 최대 0.9초 실행한다."""
        async with asyncio.timeout(0.9):
            await test(*args, **kwargs)

    return bounded


@asynccontextmanager
async def bounded_lifespan(
    lifespan: AbstractAsyncContextManager[LifespanValue],
) -> AsyncIterator[LifespanValue]:
    """진입·종료를 각각 0.5초로 제한하고 취소가 흡수돼도 시간 초과로 실패한다."""
    stack = AsyncExitStack()
    try:
        async with asyncio.timeout(0.5) as entering:
            value = await stack.enter_async_context(lifespan)
        if entering.expired():
            raise TimeoutError("lifespan entry exceeded 0.5 seconds")
        yield value
    finally:
        async with asyncio.timeout(0.5) as exiting:
            await stack.aclose()
        if exiting.expired():
            raise TimeoutError("lifespan exit exceeded 0.5 seconds")


class CleanupHarness:
    """실제 실행기·에이전트와 합성 저장소로 종료 경계를 관측한다."""

    def __init__(self, *, forever: bool = True, rollback: bool = False) -> None:
        """토큰을 보내는 합성 실행과 작은 저장 상한으로 의존성을 조립한다."""
        self.store = FakeStore()
        self.factory = (
            RollbackSessionFactory(self.store) if rollback else FakeSessionFactory()
        )
        self.run = ScriptedRunCoach(
            [{"type": "token", "delta": PARTIAL}], forever=forever
        )
        self.agent = CoachAgent(
            model_factory=CountingModelFactory(),
            port_factory=RecordingPortFactory(),
            run=self.run,
        )
        self.runner = CoachingRunner(
            session_factory=self.factory,
            agent=self.agent,
            store=self.store,
            clock=fixed_now,
            interrupt_timeout=0.2,
            finalize_timeout=0.4,
        )
        assert self.runner.is_ready()
        self.job, self.channel = self.new_job()

    def new_job(self) -> tuple[GenerationJob, RecordingChannel]:
        """독립 UUIDv4 요청과 연결을 생성해 같은 실행기에 제출할 수 있게 한다."""
        user_id = uuid4()
        state = self.store.seed_accepted(user_id=user_id)
        channel = RecordingChannel()
        return GenerationJob(user_id, state, "합성 질문", channel, 0, 0), channel

    def fail_count(self) -> int:
        """중복 중단 저장을 검사할 fail 호출 수를 반환한다."""
        return sum(method == "fail" for method, _ in self.store.calls)

    def assert_interrupted(
        self, job: GenerationJob, channel: RecordingChannel, partial: str
    ) -> None:
        """저장된 부분 답변과 접수 후 재시도 가능한 중단 오류가 일치하는지 검사한다."""
        state = self.store.state(job.request.id)
        assert state.status == "failed"
        assert state.error_code == "GENERATION_INTERRUPTED"
        assert state.error_context is None
        assert state.finished_at == NOW
        assert state.assistant_message.content == partial
        errors = [event for event in channel.events if event["type"] == "error"]
        assert len(errors) == 1
        assert channel.events[-1] == errors[0]
        assert errors[0]["requestId"] == str(job.request.id)
        assert errors[0]["data"]["code"] == "GENERATION_INTERRUPTED"
        assert errors[0]["data"]["accepted"] is True
        assert errors[0]["data"]["retryable"] is True


@bounded_test
async def test_aclose_during_generation_saves_partial_as_interrupted(caplog) -> None:
    """A1 생성 중 앱 종료 → 부분 답변을 중단 저장하고 error와 failed 조회 상태를 제공한다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness()
    task = harness.runner.submit(harness.job)
    await harness.run.entered.wait()
    await harness.runner.aclose(timeout=0.4)
    harness.assert_interrupted(harness.job, harness.channel, PARTIAL)
    assert task.cancelled()
    assert harness.run.cancelled
    assert harness.runner.active_count == 0
    assert not harness.runner._saves
    assert "outcome=interrupted code=GENERATION_INTERRUPTED" in caplog.text
    async with harness.factory() as session:
        state = await harness.store.get_request_state(
            session, user_id=harness.job.user_id, request_id=harness.job.request.id
        )
    assert request_state_data(state).status == "failed"
    await harness.agent.aclose()


@bounded_test
async def test_aclose_before_generation_starts_saves_empty_interrupted_answer() -> None:
    """첫 실행 전 앱 종료 → 에이전트 호출 없이 빈 답변을 중단 저장하고 error 하나만 보낸다."""
    harness = CleanupHarness()
    loop = asyncio.get_running_loop()
    started = loop.time()
    task = harness.runner.submit(harness.job)
    await harness.runner.aclose(timeout=0.4)
    assert loop.time() - started < 0.5
    assert task.cancelled()
    assert not harness.run.calls
    harness.assert_interrupted(harness.job, harness.channel, "")
    assert [event["type"] for event in harness.channel.events] == ["error"]
    assert harness.fail_count() == 1
    assert harness.runner.active_count == 0
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_interrupt_before_generation_starts_save_failure_is_bounded_and_private(
    caplog,
) -> None:
    """첫 실행 전 중단 저장 실패 → 접수 상태를 보존하고 예외 문구 없이 기록하며 상한 내 닫는다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness()
    harness.store.errors["fail"] = ConnectionError(PARTIAL)
    loop = asyncio.get_running_loop()
    started = loop.time()
    task = harness.runner.submit(harness.job)
    await harness.runner.aclose(timeout=0.4)
    assert loop.time() - started < 0.5
    assert task.cancelled()
    assert not harness.run.calls
    assert harness.store.state(harness.job.request.id).status == "accepted"
    assert harness.store.state(harness.job.request.id) == harness.job.request
    assert not harness.channel.events
    assert harness.fail_count() == 1
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert (
        warnings[0]
        .getMessage()
        .startswith("coaching interrupt failed: ConnectionError at ")
    )
    assert warnings[0].exc_info is None
    assert PARTIAL not in caplog.text
    assert harness.job.question not in caplog.text
    assert str(harness.job.user_id) not in caplog.text
    assert "outcome=interrupt_failed" in caplog.text
    assert harness.runner.active_count == 0
    assert not harness.runner._saves
    await harness.agent.aclose()


@pytest.mark.parametrize("failure", ["store", "commit_timeout", "commit_error"])
@bounded_test
async def test_interrupt_save_failure_is_bounded_and_private(failure, caplog) -> None:
    """A2 중단 저장 오류·commit 지연 → 진행 상태를 보존하고 종류만 기록하며 상한 내 닫는다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness(rollback=True)
    task = harness.runner.submit(harness.job)
    await harness.run.entered.wait()
    if failure == "store":
        harness.store.errors["fail"] = ConnectionError(PARTIAL)
    elif failure == "commit_error":
        harness.factory.commit_errors.append(ConnectionError(PARTIAL))
    else:
        harness.factory.commit_gate = asyncio.Event()
    loop = asyncio.get_running_loop()
    started = loop.time()
    await harness.runner.aclose(timeout=0.4)
    assert loop.time() - started < 0.5
    assert task.cancelled()
    assert harness.store.state(harness.job.request.id).status == "generating"
    assert not any(event["type"] == "error" for event in harness.channel.events)
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    kind = "TimeoutError" if failure == "commit_timeout" else "ConnectionError"
    assert warnings[0].getMessage().startswith(f"coaching interrupt failed: {kind} at ")
    assert warnings[0].exc_info is None
    assert PARTIAL not in caplog.text
    assert str(harness.job.user_id) not in caplog.text
    assert "outcome=interrupt_failed" in caplog.text
    assert harness.runner.active_count == 0
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_aclose_waits_for_terminal_commit_without_interrupting(caplog) -> None:
    """A3 종결 commit 도중 종료 → commit과 completed·시간 기록을 보호하고 중단 저장은 생략한다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness(forever=False)
    gate, entered = asyncio.Event(), asyncio.Event()

    def block_terminal_commit() -> None:
        """두 번째 commit만 막아 생성 준비와 종결 저장을 분리한다."""
        if harness.factory.commits == 2:
            harness.factory.commit_gate = gate
            entered.set()

    harness.factory.on_commit = block_terminal_commit
    task = harness.runner.submit(harness.job)
    await entered.wait()
    assert not any(event["type"] == "completed" for event in harness.channel.events)
    closing = asyncio.create_task(harness.runner.aclose(timeout=0.4))
    asyncio.get_running_loop().call_later(0.02, gate.set)
    await closing
    assert task.cancelled()
    assert harness.store.state(harness.job.request.id).status == "completed"
    assert harness.channel.events[-1]["type"] == "completed"
    assert harness.fail_count() == 0
    assert "GENERATION_INTERRUPTED" not in str(harness.channel.events)
    assert "outcome=completed" in caplog.text
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_submit_while_closing_only_saves_and_is_awaited() -> None:
    """A4 종료 대기 중 뒤늦은 접수 → 생성 없이 빈 답변을 중단 저장하고 새 작업까지 기다린다."""
    harness = CleanupHarness()
    await harness.runner.aclose(timeout=0.4)
    first_gate, late_gate = asyncio.Event(), asyncio.Event()
    first_entered, late_entered = asyncio.Event(), asyncio.Event()

    def block_commits() -> None:
        """첫 저장과 종료 중 추가 저장에 서로 다른 commit 게이트를 적용한다."""
        if harness.factory.commits == 1:
            harness.factory.commit_gate = first_gate
            first_entered.set()
        else:
            harness.factory.commit_gate = late_gate
            late_entered.set()

    harness.factory.on_commit = block_commits
    first = harness.runner.submit(harness.job)
    await first_entered.wait()
    closing = asyncio.create_task(harness.runner.aclose(timeout=0.4))
    await asyncio.sleep(0)
    job, channel = harness.new_job()
    late = harness.runner.submit(job)
    await late_entered.wait()
    first_gate.set()
    await first
    assert not closing.done()
    asyncio.get_running_loop().call_later(0.02, late_gate.set)
    await closing
    assert late.done() and not late.cancelled()
    assert not harness.run.calls
    for submitted, connection in ((harness.job, harness.channel), (job, channel)):
        harness.assert_interrupted(submitted, connection, "")
        assert [event["type"] for event in connection.events] == ["error"]
    assert harness.runner.active_count == 0
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_startup_cleanup_logs_count_and_uses_start_time(settings, caplog) -> None:
    """A5 기동 정리 성공 → 고정 시작 시각을 전달하고 변경 개수 INFO를 남긴다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness()
    cleanup = AsyncMock(return_value=2)
    app = FastAPI()
    async with bounded_lifespan(
        coaching_lifespan(
            app,
            settings,
            harness.factory,
            agent=harness.agent,
            cleanup=cleanup,
            clock=fixed_now,
            cleanup_timeout=0.2,
        )
    ):
        assert app.state.coaching_question_handler is not None
        cleanup.assert_not_awaited()
        await asyncio.sleep(0)
        cleanup.assert_awaited_once_with(
            harness.factory, created_before=NOW, now=NOW, timeout=0.2
        )
        assert "coaching startup cleanup interrupted=2" in caplog.text
    assert app.state.coaching_question_handler is None


@bounded_test
async def test_startup_cleanup_only_changes_requests_before_start() -> None:
    """A5·C8 이전 진행 요청만 중단 → 경계·이후·종결 요청과 assistant 본문은 보존한다."""
    store, factory = FakeStore(), FakeSessionFactory()
    before = NOW - timedelta(seconds=1)
    old = [store.seed_accepted(user_id=uuid4(), now=before) for _ in range(2)]
    store.requests[old[1].id] = replace(
        old[1],
        status="generating",
        assistant_message=replace(old[1].assistant_message, content=PARTIAL),
    )
    untouched = [
        store.seed_accepted(user_id=uuid4(), now=NOW),
        store.seed_accepted(user_id=uuid4(), now=NOW + timedelta(seconds=1)),
    ]
    async with factory() as session:
        for status in ("completed", "failed"):
            state = store.seed_accepted(user_id=uuid4(), now=before)
            if status == "completed":
                await store.complete(
                    session, request_id=state.id, answer="완료", now=before
                )
            else:
                await store.fail(
                    session,
                    request_id=state.id,
                    error_code="GENERATION_TIMEOUT",
                    error_context=None,
                    partial_answer="기존 부분",
                    now=before,
                )
            untouched.append(store.state(state.id))
    assert (
        await interrupt_stale_requests(
            factory, created_before=NOW, now=NOW, timeout=0.2, store=store
        )
        == 2
    )
    assert factory.commits == 1
    for state in old:
        updated = store.state(state.id)
        assert updated.status == "failed"
        assert updated.error_code == "GENERATION_INTERRUPTED"
        assert updated.finished_at == NOW
        assert store.conversations[state.conversation_id].updated_at == NOW
    assert store.state(old[1].id).assistant_message.content == PARTIAL
    for state in untouched:
        assert store.state(state.id) == state
        assert store.conversations[state.conversation_id].updated_at == state.created_at
    async with factory() as session:
        result = await store.accept(
            session,
            user_id=store.owners[old[0].conversation_id],
            client_request_id=uuid4(),
            conversation_id=old[0].conversation_id,
            content="재시작 뒤 합성 질문",
            now=NOW,
        )
    assert isinstance(result, repository.Accepted)


@bounded_test
async def test_startup_failure_retries_without_delaying_lifespan(
    settings, caplog
) -> None:
    """A6 DB 정리 실패 후 복구 → 앱은 먼저 열리고 같은 시작 기준으로 재시도해 성공한다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness()
    entered, release, recovered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    ticks = iter((NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)))

    async def cleanup(factory, **kwargs) -> int:
        """첫 정리는 외부 신호까지 지연 후 실패하고 두 번째 정리는 성공한다."""
        calls.append((factory, kwargs))
        entered.set()
        if len(calls) == 1:
            await release.wait()
            raise ConnectionError(PARTIAL)
        recovered.set()
        return 2

    app = FastAPI()
    async with bounded_lifespan(
        coaching_lifespan(
            app,
            settings,
            harness.factory,
            agent=harness.agent,
            cleanup=cleanup,
            clock=lambda: next(ticks),
            cleanup_timeout=0.2,
            retry_seconds=0.01,
        )
    ):
        assert isinstance(app.state.coaching_question_handler, CoachingQuestionHandler)
        assert not entered.is_set()
        async with asyncio.timeout(0.2):
            await entered.wait()
        assert not release.is_set()
        release.set()
        async with asyncio.timeout(0.2):
            await recovered.wait()
        assert [call[1]["created_before"] for call in calls] == [NOW, NOW]
        assert [call[1]["now"] for call in calls] == [
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=2),
        ]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert (
            warnings[0]
            .getMessage()
            .startswith("coaching startup cleanup failed: ConnectionError at ")
        )
        assert warnings[0].exc_info is None
        assert PARTIAL not in caplog.text
        assert "coaching startup cleanup interrupted=2" in caplog.text


@pytest.mark.parametrize("cancellations", [2, 3])
@bounded_test
async def test_repeated_cancel_preserves_one_interrupt_save(cancellations) -> None:
    """A7 생성 취소 후 저장 대기를 재취소 → 보호 저장은 한 번 끝까지 실행된다."""
    harness = CleanupHarness()
    task = harness.runner.submit(harness.job)
    await harness.run.entered.wait()
    gate, entered = asyncio.Event(), asyncio.Event()
    harness.factory.commit_gate = gate
    harness.factory.on_commit = entered.set
    task.cancel()
    await entered.wait()
    for _ in range(cancellations - 1):
        task.cancel()
        await asyncio.sleep(0)
    assert harness.runner._saves
    assert all(not save.cancelled() for save in harness.runner._saves)
    if cancellations == 3:
        assert task.cancelled()
    gate.set()
    await harness.runner.aclose(timeout=0.4)
    harness.assert_interrupted(harness.job, harness.channel, PARTIAL)
    assert harness.fail_count() == 1
    assert task.cancelled()
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_second_aclose_waits_without_cancelling_remaining_save() -> None:
    """A7 aclose 상한 초과 후 재호출 → 남은 저장을 다시 취소하지 않고 기다린다."""
    harness = CleanupHarness()
    harness.runner._interrupt_timeout = 0.5
    task = harness.runner.submit(harness.job)
    await harness.run.entered.wait()
    gate = asyncio.Event()
    harness.factory.commit_gate = gate
    await harness.runner.aclose(timeout=0.2)
    assert not task.done()
    assert task.cancelling() == 1
    assert harness.runner._saves
    asyncio.get_running_loop().call_later(0.02, gate.set)
    await harness.runner.aclose(timeout=0.4)
    assert task.cancelling() == 1
    harness.assert_interrupted(harness.job, harness.channel, PARTIAL)
    assert harness.fail_count() == 1
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_lifespan_cancels_and_collects_retry_task(settings) -> None:
    """A7 기동 정리 재시도 대기 중 종료 → 정리 태스크를 즉시 취소·수거한다."""
    harness = CleanupHarness()
    attempted = asyncio.Event()

    async def cleanup(*args, **kwargs) -> int:
        """매번 실패해 다음 재시도 대기에 들어가도록 한다."""
        attempted.set()
        raise ConnectionError()

    before = asyncio.all_tasks()
    async with bounded_lifespan(
        coaching_lifespan(
            FastAPI(),
            settings,
            harness.factory,
            agent=harness.agent,
            cleanup=cleanup,
            clock=fixed_now,
            retry_seconds=10,
        )
    ):
        async with asyncio.timeout(0.2):
            await attempted.wait()
        spawned = asyncio.all_tasks() - before
        # 기동 정리 태스크와 주기 정리 태스크 두 개가 뜬다.
        assert {t.get_name() for t in spawned} == {
            "coaching-startup-cleanup",
            "coaching-expiry-cleanup",
        }
        cleanup_task = next(
            t for t in spawned if t.get_name() == "coaching-startup-cleanup"
        )
    assert cleanup_task.cancelled()
    assert cleanup_task not in asyncio.all_tasks()
    assert not (asyncio.all_tasks() - before)


@bounded_test
async def test_three_generations_shutdown_with_separate_partial_answers() -> None:
    """A8 동시 생성 세 개 종료 → 각 요청의 부분 답변과 error를 격리하고 상한을 지킨다."""
    harness = CleanupHarness()
    jobs, tasks = [], []
    for index in range(3):
        job, channel = harness.new_job()
        partial = f"합성 부분 {index}"
        harness.run.events = [{"type": "token", "delta": partial}]
        harness.run.entered.clear()
        tasks.append(harness.runner.submit(job))
        await harness.run.entered.wait()
        jobs.append((job, channel, partial))
    loop = asyncio.get_running_loop()
    started = loop.time()
    await harness.runner.aclose(timeout=0.4)
    assert loop.time() - started < 0.5
    for job, channel, partial in jobs:
        harness.assert_interrupted(job, channel, partial)
    assert all(task.cancelled() for task in tasks)
    assert harness.fail_count() == 3
    assert harness.runner.active_count == 0
    assert not harness.runner._saves
    await harness.agent.aclose()


@bounded_test
async def test_real_app_lifespan_without_db_enters_and_exits_within_half_second(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A9 실제 앱 lifespan에서 DB가 응답하지 않아도 → 0.5초 내 진입·종료하고 정리를 취소한다."""
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def unavailable_store(*args, **kwargs) -> int:
        """네트워크 없이 DB 대기를 재현하고 lifespan 종료 취소를 기록한다."""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return 0

    ai_settings = Mock(side_effect=AssertionError("AI 설정 로딩 금지"))
    http_send = AsyncMock(side_effect=AssertionError("HTTP 요청 금지"))
    monkeypatch.setattr(repository, "interrupt_in_progress", unavailable_store)
    monkeypatch.setattr("app.coaching.agent.get_ai_settings", ai_settings)
    monkeypatch.setattr(httpx.AsyncClient, "send", http_send)
    before = asyncio.all_tasks()
    app = create_app(settings)
    async with bounded_lifespan(app.router.lifespan_context(app)):
        assert isinstance(app.state.coaching_question_handler, CoachingQuestionHandler)
        assert not entered.is_set()
        async with asyncio.timeout(0.2):
            await entered.wait()
    assert app.state.coaching_question_handler is None
    assert cancelled.is_set()
    assert not (asyncio.all_tasks() - before)
    ai_settings.assert_not_called()
    http_send.assert_not_called()


@bounded_test
async def test_cancellation_during_preparation_saves_empty_interrupted_answer() -> None:
    """준비 commit 중 취소 → 에이전트 호출 없이 빈 답변을 중단 저장한다."""
    harness = CleanupHarness(rollback=True)
    entered = asyncio.Event()
    gate = asyncio.Event()
    harness.factory.commit_gate = gate
    harness.factory.on_commit = entered.set
    task = harness.runner.submit(harness.job)
    await entered.wait()
    closing = asyncio.create_task(harness.runner.aclose(timeout=0.4))
    asyncio.get_running_loop().call_later(0.02, gate.set)
    await closing
    assert task.cancelled()
    assert not harness.run.calls
    harness.assert_interrupted(harness.job, harness.channel, "")
    assert harness.fail_count() == 1
    await harness.agent.aclose()


@bounded_test
async def test_closing_submit_does_not_overwrite_terminal_request(caplog) -> None:
    """이미 완료된 요청을 닫는 중 제출 → 종결 상태를 보존하고 추가 이벤트를 보내지 않는다."""
    caplog.set_level(logging.INFO)
    harness = CleanupHarness()
    async with harness.factory() as session:
        await harness.store.complete(
            session, request_id=harness.job.request.id, answer="완료", now=NOW
        )
    previous = harness.store.state(harness.job.request.id)
    await harness.runner.aclose(timeout=0.4)
    await harness.runner.submit(harness.job)
    assert harness.store.state(harness.job.request.id) == previous
    assert not harness.channel.events
    assert "outcome=already_finished" in caplog.text
    await harness.agent.aclose()


@bounded_test
async def test_close_expired_requests_commits_once_and_returns_count() -> None:
    """S5 기한 지난 진행 요청 → 한 번 닫고 commit 1회, 개수를 반환한다."""
    store, factory = FakeStore(), FakeSessionFactory()
    expired = store.seed_accepted(
        user_id=uuid4(), deadline_at=NOW - timedelta(seconds=30)
    )
    store.seed_accepted(user_id=uuid4(), deadline_at=NOW + timedelta(seconds=30))
    changed = await close_expired_requests(
        factory,
        now=NOW,
        expired_before=NOW - timedelta(seconds=20),
        timeout=0.2,
        store=store,
    )
    assert changed == 1
    assert factory.commits == 1
    assert store.state(expired.id).error_code == "GENERATION_TIMEOUT"


@bounded_test
async def test_close_expired_requests_rejects_naive_times() -> None:
    """S12 시간대 없는 now·expired_before → ValueError, commit 없음."""
    store, factory = FakeStore(), FakeSessionFactory()
    naive = NOW.replace(tzinfo=None)
    for now, before in ((naive, NOW), (NOW, naive)):
        with pytest.raises(ValueError):
            await close_expired_requests(
                factory, now=now, expired_before=before, timeout=0.2, store=store
            )
    assert factory.commits == 0


@bounded_test
async def test_expiry_loop_does_not_call_before_first_interval() -> None:
    """O3 첫 주기 전 → 저장소 호출 0회, 취소하면 그대로 종료한다."""
    calls = []

    async def expire(*args, **kwargs) -> int:
        """호출을 기록만 한다."""
        calls.append(kwargs)
        return 0

    task = asyncio.create_task(
        _expiry_loop(
            FakeSessionFactory(),
            expire=expire,
            clock=fixed_now,
            interval=10,
            grace=20,
            timeout=1,
        )
    )
    await asyncio.sleep(0.02)
    assert calls == []
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


@bounded_test
async def test_expiry_loop_passes_clock_minus_grace() -> None:
    """O4 매 주기 → now=clock(), expired_before=clock()-grace, timeout을 넘긴다."""
    done = asyncio.Event()
    calls = []
    ticks = iter((NOW, NOW + timedelta(seconds=5)))
    factory = FakeSessionFactory()

    async def expire(session_factory, **kwargs) -> int:
        """인자를 기록하고 두 번째 호출에서 신호를 보낸다."""
        calls.append((session_factory, kwargs))
        if len(calls) == 2:
            done.set()
        return 0

    task = asyncio.create_task(
        _expiry_loop(
            factory,
            expire=expire,
            clock=lambda: next(ticks),
            interval=0.01,
            grace=20,
            timeout=0.7,
        )
    )
    await done.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert [c[0] for c in calls] == [factory, factory]
    assert [c[1] for c in calls] == [
        {"now": NOW, "expired_before": NOW - timedelta(seconds=20), "timeout": 0.7},
        {
            "now": NOW + timedelta(seconds=5),
            "expired_before": NOW - timedelta(seconds=15),
            "timeout": 0.7,
        },
    ]


@bounded_test
async def test_expiry_loop_logs_failure_and_continues(caplog) -> None:
    """S10 첫 실행 예외·둘째 정상 → 경고 1줄(질문 내용 없음) 후 셋째 실행까지 이어진다."""
    caplog.set_level(logging.INFO)
    done = asyncio.Event()
    count = 0

    async def expire(*args, **kwargs) -> int:
        """첫 호출은 실패하고 이후 호출은 0건으로 성공하며 셋째에서 신호를 보낸다."""
        nonlocal count
        count += 1
        if count == 1:
            raise ConnectionError(PARTIAL)
        if count == 3:
            done.set()
        return 0

    task = asyncio.create_task(
        _expiry_loop(
            FakeSessionFactory(),
            expire=expire,
            clock=fixed_now,
            interval=0.01,
            grace=20,
            timeout=1,
        )
    )
    await done.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert (
        warnings[0]
        .getMessage()
        .startswith("coaching expiry cleanup failed: ConnectionError at ")
    )
    assert PARTIAL not in caplog.text


@bounded_test
async def test_expiry_loop_warns_only_when_changed(caplog) -> None:
    """S11 3건 닫음 → WARNING 1줄, 0건 → 로그 없음."""
    caplog.set_level(logging.INFO)
    done = asyncio.Event()
    results = iter((3, 0, 0))
    calls: list[int] = []

    async def expire(*args, **kwargs) -> int:
        """3건, 0건, 0건을 차례로 반환하고 마지막에 신호를 보낸다."""
        value = next(results)
        calls.append(value)
        if len(calls) == 3:
            done.set()
        return value

    task = asyncio.create_task(
        _expiry_loop(
            FakeSessionFactory(),
            expire=expire,
            clock=fixed_now,
            interval=0.01,
            grace=20,
            timeout=1,
        )
    )
    await done.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    records = [r for r in caplog.records if "expiry cleanup" in r.getMessage()]
    assert [r.getMessage() for r in records] == ["coaching expiry cleanup failed=3"]
    assert records[0].levelno == logging.WARNING


@bounded_test
async def test_lifespan_cancels_expiry_task_before_runner_close(
    settings, monkeypatch
) -> None:
    """S13 앱 종료 → 주기 작업을 취소·수거한 뒤 runner.aclose가 실행된다."""
    harness = CleanupHarness()
    order: list[str] = []
    original = CoachingRunner.aclose

    async def aclose(self, *args, **kwargs) -> None:
        """실행기 종료 시점의 주기 작업 상태를 기록한 뒤 원래 종료를 수행한다."""
        order.append(f"runner-close expiry_done={expiry_task.done()}")
        await original(self, *args, **kwargs)

    monkeypatch.setattr(CoachingRunner, "aclose", aclose)
    before = asyncio.all_tasks()

    async def noop_cleanup(*args, **kwargs) -> int:
        """기동 정리를 즉시 끝낸다."""
        return 0

    async with bounded_lifespan(
        coaching_lifespan(
            FastAPI(),
            settings,
            harness.factory,
            agent=harness.agent,
            cleanup=noop_cleanup,
            clock=fixed_now,
            expiry_interval=10,
        )
    ):
        await asyncio.sleep(0)
        spawned = {
            t
            for t in asyncio.all_tasks() - before
            if t.get_name() == "coaching-expiry-cleanup"
        }
        assert len(spawned) == 1
        expiry_task = next(iter(spawned))
    assert order == ["runner-close expiry_done=True"]
    assert expiry_task.cancelled()
    assert not (asyncio.all_tasks() - before)
