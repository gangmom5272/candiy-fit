"""DB·네트워크 없이 코칭 생성 작업의 저장·송신·취소 경계를 검증한다."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, ParamSpec, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import WebSocket
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import agent as agent_module
from app.coaching.agent import CoachAgent, default_agent
from app.coaching.connection import EventChannel
from app.coaching.events import PublicEventSink
from app.coaching.health_port import HealthQueryPort
from app.coaching.runner import CoachingRunner, GenerationJob
from app.coaching.schemas import RequestIds
from app.core.config import Settings
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.graph import CoachResult
from coach_ai.agent.state import FinishReason
from coach_ai.history.types import HistoryTurn, StoredSummary
from tests.coaching.fakes import (
    MODEL,
    NOW,
    PORT,
    SYNTHETIC_PROFILE,
    SYNTHETIC_SNAPSHOT,
    CountingModelFactory,
    FakeSessionFactory,
    FakeStore,
    RecordingChannel,
    RecordingPortFactory,
    ScriptedRunCoach,
    fixed_now,
    synthetic_evidence,
    synthetic_evidence_info,
)

TestArgs = ParamSpec("TestArgs")


def bounded_test(
    test: Callable[TestArgs, Awaitable[None]],
) -> Callable[TestArgs, Awaitable[None]]:
    """비동기 테스트 본문과 같은 태스크에서 1초 미만 대기 상한을 적용한다."""

    @wraps(test)
    async def bounded(*args: TestArgs.args, **kwargs: TestArgs.kwargs) -> None:
        """기존 fixture 인자를 전달하고 테스트가 무한 대기하면 시간 초과로 끝낸다."""
        async with asyncio.timeout(0.9):
            await test(*args, **kwargs)

    return bounded


class Harness:
    """생성 실행기 검증에 필요한 합성 요청과 관측 지점을 묶는다."""

    def __init__(
        self,
        script: ScriptedRunCoach | None = None,
        *,
        deadline_seconds: float = 120,
        finalize_timeout: float = 0.05,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """합성 요청·채널·에이전트를 만들고 실행기를 준비한다."""
        self.store = FakeStore()
        self.factory = FakeSessionFactory()
        self.channel = RecordingChannel()
        self.user_id = uuid4()
        self.request = self.store.seed_accepted(
            user_id=self.user_id,
            content="합성 비공개 질문",
            deadline_at=NOW + timedelta(seconds=deadline_seconds),
        )
        self.script = script or ScriptedRunCoach(
            [
                {"type": "status", "stage": "thinking"},
                {"type": "token", "delta": "합성 비공개 답변"},
                {"type": "done"},
            ]
        )
        self.model_factory = CountingModelFactory()
        self.port_factory = RecordingPortFactory()
        self.agent = CoachAgent(
            model_factory=self.model_factory,
            port_factory=self.port_factory,
            run=self.script,
        )
        extra: dict[str, Any] = {} if monotonic is None else {"monotonic": monotonic}
        self.runner = CoachingRunner(
            session_factory=cast(
                Callable[[], AbstractAsyncContextManager[AsyncSession]], self.factory
            ),
            agent=self.agent,
            store=self.store,
            clock=fixed_now,
            finalize_timeout=finalize_timeout,
            **extra,
        )
        self.job = GenerationJob(
            user_id=self.user_id,
            request=self.request,
            question=self.request.user_message.content,
            channel=cast(EventChannel, self.channel),
            started_at=0,
            accepted_at=0.125,
        )
        assert self.runner.is_ready()

    def submit(self) -> asyncio.Task[None]:
        """기본 합성 요청을 실행기에 접수하고 작업 참조를 반환한다."""
        return self.runner.submit(self.job)

    def assert_failure(self, code: str, *, partial: str = "") -> None:
        """공개 오류·부분 답변·실패 상태가 일치하고 completed가 없는지 검사한다."""
        state = self.store.state(self.request.id)
        assert state.status == "failed"
        assert state.error_code == code
        assert state.assistant_message.content == partial
        assert self.channel.events[-1]["type"] == "error"
        assert self.channel.events[-1]["data"]["code"] == code
        assert self.channel.events[-1]["data"]["accepted"] is True
        assert not any(event["type"] == "completed" for event in self.channel.events)


@bounded_test
async def test_runner_normal_completion() -> None:
    """A1 정상 생성 → 순서와 연속 eventSeq를 지키고 최종 답변을 저장한다."""
    harness = Harness()
    task = harness.submit()
    assert harness.runner.active_count == 1
    await task
    events = harness.channel.events
    assert [event["type"] for event in events] == [
        "status",
        "status",
        "delta",
        "completed",
    ]
    assert [event["eventSeq"] for event in events] == [2, 3, 4, 5]
    assert events[0]["data"]["stage"] == "reading_history"
    assert events[1]["data"]["stage"] == "generating_answer"
    state = harness.store.state(harness.request.id)
    assert state.status == "completed"
    assert state.assistant_message.content == "합성 비공개 답변"
    assert (
        events[-1]["data"]["assistantMessage"]["content"]
        == state.assistant_message.content
    )
    assert harness.runner.active_count == 0


@bounded_test
async def test_runner_passes_acceptance_time_as_now() -> None:
    """실행 시작이 KST 자정을 넘겨도 → 에이전트와 건강 포트가 접수 시각을 now로 받는다."""
    harness = Harness()
    accepted_at = datetime(2026, 10, 28, 14, 59, 59, 900000, tzinfo=UTC)
    clock_now = datetime(2026, 10, 28, 15, 0, 0, 100000, tzinfo=UTC)
    harness.runner._clock = lambda: clock_now
    request = replace(
        harness.request,
        created_at=accepted_at,
        deadline_at=clock_now + timedelta(seconds=120),
    )
    await harness.runner.submit(replace(harness.job, request=request))
    assert harness.script.calls[0]["now"] == accepted_at
    assert harness.port_factory.calls == [(str(harness.user_id), accepted_at)]
    assert harness.store.state(harness.request.id).finished_at == clock_now


@bounded_test
async def test_runner_completed_follows_commit() -> None:
    """A2 정상 종결 → commit 전에는 completed를 보내지 않는다."""
    harness = Harness()
    seen: list[list[str]] = []

    def observe_commit() -> None:
        """각 commit 직전의 공개 이벤트 종류를 기록한다."""
        seen.append([event["type"] for event in harness.channel.events])

    harness.factory.on_commit = observe_commit
    await harness.submit()
    assert seen == [["status"], ["status", "status", "delta"]]
    assert harness.factory.commits == 2
    assert harness.channel.events[-1]["type"] == "completed"


@bounded_test
async def test_runner_releases_session_before_llm() -> None:
    """A3 LLM이 대기 중이면 → DB 세션을 모두 닫아 둔다."""
    hold = asyncio.Event()
    harness = Harness(ScriptedRunCoach(hold=hold))
    task = harness.submit()
    await harness.script.entered.wait()
    assert harness.factory.active == 0
    assert harness.factory.opened == 1
    assert harness.factory.commits == 1
    hold.set()
    await task
    assert harness.factory.opened == 2
    assert harness.factory.active == 0


@bounded_test
async def test_runner_llm_error_preserves_partial() -> None:
    """A4 내부 llm_timeout → 부분 답변과 사유 없는 AI_UNAVAILABLE을 저장한다."""
    harness = Harness(
        ScriptedRunCoach(
            [
                {"type": "token", "delta": "부분"},
                {"type": "error", "code": "llm_timeout"},
            ],
            result=CoachResult("부분", [], "error", error_code="internal"),
        )
    )
    await harness.submit()
    harness.assert_failure("AI_UNAVAILABLE", partial="부분")
    assert harness.store.state(harness.request.id).error_context is None
    data = harness.channel.events[-1]["data"]
    assert data["retryable"] is True
    assert "reason" not in data
    assert "metrics" not in data


@bounded_test
async def test_runner_health_error_persists_context() -> None:
    """A5 건강 준비 전 → 공개 사유·지표를 이벤트와 저장소에 보존한다."""
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "error", "code": "data_not_ready", "metrics": ["sleeps"]}],
            result=CoachResult("", [], "error", error_code="data_not_ready"),
        )
    )
    await harness.submit()
    harness.assert_failure("HEALTH_DATA_UNAVAILABLE")
    context = {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]}
    assert harness.store.state(harness.request.id).error_context == context
    assert all(
        harness.channel.events[-1]["data"][key] == value
        for key, value in context.items()
    )


@bounded_test
async def test_runner_deadline_expired_saves_partial_answer_as_timeout() -> None:
    """A6 저장된 기한 초과 → 생성을 취소하고 부분 답변을 시간 초과로 저장한다."""
    harness = Harness(
        ScriptedRunCoach([{"type": "token", "delta": "중간"}], forever=True),
        deadline_seconds=0.03,
    )
    await harness.submit()
    harness.assert_failure("GENERATION_TIMEOUT", partial="중간")
    assert harness.script.cancelled
    assert harness.factory.commits == 2


@bounded_test
async def test_runner_inner_timeout_is_internal_error() -> None:
    """A7 내부 TimeoutError → 전체 기한 초과와 구분해 INTERNAL_ERROR로 저장한다."""
    harness = Harness()
    harness.store.errors["find_history_context"] = TimeoutError("합성 비공개 DB 오류")
    await harness.submit()
    harness.assert_failure("INTERNAL_ERROR")
    assert harness.script.calls == []


@bounded_test
async def test_runner_preparation_database_error() -> None:
    """A8 준비 DB 오류 → 에이전트를 호출하지 않고 INTERNAL_ERROR를 저장한다."""
    harness = Harness()
    harness.store.errors["mark_generating"] = RuntimeError("합성 비공개 DB 오류")
    await harness.submit()
    harness.assert_failure("INTERNAL_ERROR")
    assert harness.script.calls == []
    assert harness.factory.rollbacks == 1


@bounded_test
async def test_runner_finalize_failure_uses_auxiliary_save() -> None:
    """A9 종결 저장 실패 → PERSISTENCE_FAILED를 보내고 새 세션으로 보조 저장한다."""
    harness = Harness()
    harness.store.errors["complete"] = RuntimeError("합성 비공개 저장 오류")
    await harness.submit()
    harness.assert_failure("PERSISTENCE_FAILED", partial="합성 비공개 답변")
    assert harness.factory.opened == 3
    complete_call = next(
        args for name, args in harness.store.calls if name == "complete"
    )
    fail_call = next(args for name, args in harness.store.calls if name == "fail")
    assert complete_call["session"] is not fail_call["session"]
    assert fail_call["error_context"] is None


@bounded_test
async def test_runner_both_finalization_writes_fail() -> None:
    """A10 종결·보조 저장 실패 → 오류 하나만 보내고 진행 중 상태를 남긴다."""
    harness = Harness()
    harness.store.errors.update(
        complete=RuntimeError("합성 오류"), fail=RuntimeError("합성 오류")
    )
    await harness.submit()
    assert harness.store.state(harness.request.id).status == "generating"
    assert [event["type"] for event in harness.channel.events].count("error") == 1
    assert harness.channel.events[-1]["data"]["code"] == "PERSISTENCE_FAILED"
    assert harness.factory.rollbacks == 2
    assert harness.runner.active_count == 0


@bounded_test
async def test_runner_finalize_commit_timeout() -> None:
    """A11 종결 commit 대기 초과 → 별도 상한으로 중단하고 저장 실패를 보낸다."""
    harness = Harness(finalize_timeout=0.02)

    def block_final_commit() -> None:
        """준비 commit 이후의 모든 commit을 미해제 이벤트로 막는다."""
        if harness.factory.commits >= 2:
            harness.factory.commit_gate = asyncio.Event()

    harness.factory.on_commit = block_final_commit
    await harness.submit()
    assert harness.channel.events[-1]["data"]["code"] == "PERSISTENCE_FAILED"
    assert not any(event["type"] == "completed" for event in harness.channel.events)
    assert harness.factory.opened == 3
    assert harness.factory.rollbacks == 2
    assert harness.factory.active == 0


@bounded_test
async def test_runner_existing_final_result_wins(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A12 생성 도중 먼저 종결됨 → 첫 결과를 유지하고 종료 이벤트를 보내지 않는다."""
    harness = Harness(ScriptedRunCoach(hold=asyncio.Event()))
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    task = harness.submit()
    await harness.script.entered.wait()
    async with harness.factory() as session:
        await harness.store.complete(
            cast(AsyncSession, session),
            request_id=harness.request.id,
            answer="먼저 저장",
            now=NOW,
        )
        await session.commit()
    harness.script.hold.set()
    await task
    assert (
        harness.store.state(harness.request.id).assistant_message.content == "먼저 저장"
    )
    assert [event["type"] for event in harness.channel.events] == ["status"]
    assert "outcome=already_finished" in caplog.text


@bounded_test
async def test_runner_not_accepted_skips_generation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A13 mark_generating이 False → 이중 생성·종결 없이 reading_history만 보낸다."""
    harness = Harness()
    async with harness.factory() as session:
        await harness.store.mark_generating(
            cast(AsyncSession, session), request_id=harness.request.id
        )
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    assert harness.script.calls == []
    assert [event["type"] for event in harness.channel.events] == ["status"]
    assert not any(name in {"complete", "fail"} for name, _ in harness.store.calls)
    assert "outcome=not_accepted" in caplog.text


@bounded_test
async def test_runner_disconnected_channel_still_completes() -> None:
    """A14 스트리밍 중 연결 끊김 → 추가 송신 없이 최종 답변 저장을 계속한다."""
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "token", "delta": "합성 조각"}], hold=asyncio.Event()
        )
    )
    task = harness.submit()
    await harness.script.entered.wait()
    harness.channel.close()
    events_before = list(harness.channel.events)
    harness.script.hold.set()
    await task
    assert harness.channel.events == events_before
    assert harness.store.state(harness.request.id).status == "completed"
    assert (
        harness.store.state(harness.request.id).assistant_message.content == "합성 조각"
    )
    assert not harness.script.cancelled


@bounded_test
async def test_runner_slow_real_channel_keeps_generating() -> None:
    """A15 실제 채널 송신 정체 → close 1011 이후에도 생성·저장을 완료한다."""
    harness = Harness()
    sending = asyncio.Event()
    blocked = asyncio.Event()

    async def blocked_send(text: str) -> None:
        """송신 진입을 알리고 취소될 때까지 대기한다."""
        sending.set()
        await blocked.wait()

    websocket = AsyncMock(spec=WebSocket)
    websocket.send_text.side_effect = blocked_send
    channel = EventChannel(websocket, max_pending=1)
    channel.start()
    channel.send({"type": "ready"})
    await sending.wait()
    job = GenerationJob(
        harness.user_id, harness.request, harness.job.question, channel, 0, 0.125
    )
    try:
        await harness.runner.submit(job)
        await channel.aclose()
        websocket.close.assert_awaited_once_with(code=1011)
        assert channel.failure == "overflow"
        assert harness.store.state(harness.request.id).status == "completed"
        assert not harness.script.cancelled
    finally:
        await channel.aclose()
        await harness.runner.aclose()


@bounded_test
async def test_runner_two_connections_are_isolated() -> None:
    """A16 두 작업·두 연결 → 요청별 이벤트와 연속 순번이 섞이지 않는다."""
    hold = asyncio.Event()
    harness = Harness(
        ScriptedRunCoach([{"type": "token", "delta": "공통 합성 답변"}], hold=hold)
    )
    other_user = uuid4()
    other = harness.store.seed_accepted(user_id=other_user)
    other_channel = RecordingChannel()
    tasks = [
        harness.submit(),
        harness.runner.submit(
            GenerationJob(
                other_user,
                other,
                other.user_message.content,
                cast(EventChannel, other_channel),
                0,
                0.125,
            )
        ),
    ]
    await harness.script.entered.wait()
    assert harness.runner.active_count == 2
    hold.set()
    await asyncio.gather(*tasks)
    for request, channel in (
        (harness.request, harness.channel),
        (other, other_channel),
    ):
        assert {event["requestId"] for event in channel.events} == {str(request.id)}
        assert [event["eventSeq"] for event in channel.events] == [2, 3, 4]
        assert channel.events[-1]["data"]["assistantMessage"]["id"] == str(
            request.assistant_message.id
        )
        assert harness.store.state(request.id).status == "completed"
    assert harness.model_factory.calls == 1


@bounded_test
async def test_runner_close_interrupts_running_generation() -> None:
    """A17 생성 중 앱 종료 → 실행을 취소하고 부분 답변과 GENERATION_INTERRUPTED를 저장·전송한다."""
    partial = "합성 부분 답변"
    harness = Harness(
        ScriptedRunCoach([{"type": "token", "delta": partial}], forever=True)
    )
    task = harness.submit()
    await harness.script.entered.wait()
    await harness.runner.aclose(timeout=0.05)
    await harness.runner.aclose(timeout=0.05)
    assert task.cancelled()
    assert harness.script.cancelled
    assert harness.runner.active_count == 0
    harness.assert_failure("GENERATION_INTERRUPTED", partial=partial)


@bounded_test
async def test_runner_logs_only_ids_outcome_and_timing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A18 완료 로그 → 요청 ID·결과·상대 ms만 남기고 질문·답변·회원은 숨긴다."""
    times = iter([1.0, 2.0, 3.0])
    harness = Harness(monotonic=lambda: next(times))
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    assert f"request_id={harness.request.id} outcome=completed code=-" in caplog.text
    assert (
        "accepted_ms=125 first_status_ms=1000 first_delta_ms=2000 finished_ms=3000"
        in caplog.text
    )
    assert harness.job.question not in caplog.text
    assert "합성 비공개 답변" not in caplog.text
    assert str(harness.user_id) not in caplog.text


def _finished_line(caplog: pytest.LogCaptureFixture) -> str:
    """캡처한 로그에서 종료 로그 한 줄을 찾아 반환한다."""
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("coaching request finished")
    ]
    assert len(lines) == 1
    return lines[0]


@pytest.mark.parametrize("reason", ["stop", "length", "guard", "tool_limit"])
@bounded_test
async def test_runner_finish_log_records_completed_reason(
    reason: FinishReason, caplog: pytest.LogCaptureFixture
) -> None:
    """L1~L3 정상 종료 사유 → completed 로그 맨 끝에 finish_reason이 붙는다."""
    harness = Harness(ScriptedRunCoach(result=CoachResult("합성 답변", [], reason)))
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    line = _finished_line(caplog)
    assert "outcome=completed code=- " in line
    assert line.endswith(f" finish_reason={reason}")


@bounded_test
async def test_runner_finish_log_records_error_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L4 AI가 error로 종료 → failed 로그에 finish_reason=error가 남는다."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult("", [], "error", error_code="llm_unavailable")
        )
    )
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    line = _finished_line(caplog)
    assert "outcome=failed code=AI_UNAVAILABLE " in line
    assert line.endswith(" finish_reason=error")


@bounded_test
async def test_runner_finish_log_timeout_has_no_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L5 생성 기한 초과(AI 결과 없음) → finish_reason=-로 남는다."""
    harness = Harness(
        ScriptedRunCoach([{"type": "token", "delta": "중간"}], forever=True),
        deadline_seconds=0.03,
    )
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    line = _finished_line(caplog)
    assert "code=GENERATION_TIMEOUT " in line
    assert line.endswith(" finish_reason=-")


@bounded_test
async def test_runner_finish_log_not_accepted_has_no_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L6 접수 상태가 아니라 실행 안 함 → finish_reason=-로 남는다."""
    harness = Harness()
    async with harness.factory() as session:
        await harness.store.mark_generating(
            cast(AsyncSession, session), request_id=harness.request.id
        )
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    line = _finished_line(caplog)
    assert "outcome=not_accepted" in line
    assert line.endswith(" finish_reason=-")


def test_runner_finish_log_unknown_reason_is_normalized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L7 허용 목록 밖 종료 사유 → unknown으로 남기고 원래 문자열은 숨긴다."""
    harness = Harness()
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    sink = Mock(first_status_at=None, first_delta_at=None)
    harness.runner._log_finished(
        harness.job, sink, "completed", "-", finish_reason="합성임의값"
    )
    line = _finished_line(caplog)
    assert line.endswith(" finish_reason=unknown")
    assert "합성임의값" not in caplog.text


def test_agent_missing_port_never_builds_model() -> None:
    """포트 미준비 → 모델 생성 없이 준비 실패를 반환한다."""
    factory = CountingModelFactory()
    agent = CoachAgent(model_factory=factory, port_factory=None)
    assert not agent.ensure_ready()
    assert factory.calls == 0


def test_agent_model_failure_is_safe_and_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """모델 생성 실패 → 문구 없이 종류·위치를 기록하고 다음 호출만 재시도한다."""
    factory = CountingModelFactory()
    factory.errors.append(ValueError("합성 비공개 설정 값"))
    agent = CoachAgent(model_factory=factory, port_factory=RecordingPortFactory())
    assert not agent.ensure_ready()
    assert "ValueError" in caplog.text
    assert "fakes.py:" in caplog.text
    assert "합성 비공개 설정 값" not in caplog.text
    assert agent.ensure_ready()
    assert agent.ensure_ready()
    assert factory.calls == 2


def test_agent_success_reuses_one_model() -> None:
    """준비 성공 후 반복 확인 → 모델을 앱에서 한 번만 만든다."""
    factory = CountingModelFactory()
    agent = CoachAgent(model_factory=factory, port_factory=RecordingPortFactory())
    assert all(agent.ensure_ready() for _ in range(3))
    assert factory.calls == 1


@bounded_test
async def test_agent_run_requires_readiness_and_forwards_identity() -> None:
    """준비 전 실행은 거부하고 → 준비 후 요청마다 포트와 입력 객체를 그대로 전달한다."""
    script = ScriptedRunCoach()
    port_factory = RecordingPortFactory()
    agent = CoachAgent(
        model_factory=CountingModelFactory(), port_factory=port_factory, run=script
    )
    harness = Harness()
    ctx = CoachContext(
        user_id=str(harness.user_id),
        conversation_id=str(harness.request.conversation_id),
        request_id=str(harness.request.id),
        summary=StoredSummary(text="이전 합성 요약", until_seq=2),
        turns=(
            HistoryTurn(
                request_id="previous-request",
                question_message_id="previous-question",
                answer_message_id="previous-answer",
                question_seq=3,
                answer_seq=4,
                question="이전 합성 질문",
                answer="이전 합성 답변",
                answered_on=NOW.date(),
            ),
        ),
    )
    sink = PublicEventSink(
        cast(EventChannel, harness.channel), RequestIds.from_state(harness.request)
    )
    arguments = dict(
        ctx=ctx,
        question="이번 합성 질문",
        sink=sink,
        now=NOW,
        message_id=str(harness.request.assistant_message.id),
    )
    with pytest.raises(RuntimeError):
        await agent.run(**arguments)
    assert agent.ensure_ready()
    for _ in range(2):
        assert await agent.run(**arguments) is script.result
    assert port_factory.calls == [(ctx.user_id, NOW), (ctx.user_id, NOW)]
    for call in script.calls:
        assert call["ctx"] is ctx
        assert call["ctx"].summary == ctx.summary
        assert call["ctx"].turns is ctx.turns
        assert call["message_id"] == arguments["message_id"]
        assert call["model"] is MODEL
        assert call["port"] is PORT
        assert "summary" not in call and "history" not in call


@bounded_test
async def test_agent_close_is_idempotent() -> None:
    """에이전트를 두 번 닫으면 → 소유 HTTP 클라이언트 정리는 한 번만 호출한다."""
    close = AsyncMock()
    agent = CoachAgent(
        model_factory=CountingModelFactory(), port_factory=None, close=close
    )
    await agent.aclose()
    await agent.aclose()
    close.assert_awaited_once()


@pytest.mark.parametrize("app_env", ["dev", "prod"])
@bounded_test
async def test_default_agent_dev_prod_uses_shared_db_port(
    app_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev·prod 조립과 반복 포트 요청 → DB·AI 설정 조회 없이 같은 DB 포트를 반환한다."""
    read_settings = Mock(side_effect=AssertionError("AI settings must stay lazy"))
    make_client = Mock(side_effect=AssertionError("HTTP client must stay lazy"))
    factory = Mock(side_effect=AssertionError("DB must stay unopened"))
    monkeypatch.setattr(agent_module, "get_ai_settings", read_settings)
    monkeypatch.setattr(agent_module, "make_http_client", make_client)
    engine = Mock()
    agent = default_agent(
        Settings(_env_file=None, app_env=app_env), factory, engine=engine
    )
    assert agent._port_factory is not None
    owner = str(uuid4())
    first = agent._port_factory(owner, NOW)
    second = agent._port_factory(str(uuid4()), NOW + timedelta(seconds=1))
    repeated = agent._port_factory(owner, NOW + timedelta(seconds=2))
    assert isinstance(first, HealthQueryPort)
    assert first is second is repeated
    assert first._session_factory is factory
    assert first.engine is engine
    engine.connect.assert_not_called()
    await agent.aclose()
    factory.assert_not_called()
    read_settings.assert_not_called()
    make_client.assert_not_called()


@bounded_test
async def test_default_agent_dev_model_ready_keeps_db_unopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dev 모델 생성 성공 → DB 접속 없이 준비되고 HTTP 클라이언트를 정상 종료한다."""
    ai_settings = Mock(llm_max_concurrency=2)
    read_settings = Mock(return_value=ai_settings)
    client = AsyncMock()
    make_client = Mock(return_value=client)
    make_model = Mock(return_value=MODEL)
    factory = Mock(side_effect=AssertionError("DB must stay unopened"))
    monkeypatch.setattr(agent_module, "get_ai_settings", read_settings)
    monkeypatch.setattr(agent_module, "make_http_client", make_client)
    monkeypatch.setattr(agent_module, "create_chat_model", make_model)
    agent = default_agent(
        Settings(_env_file=None, app_env="dev"), factory, engine=Mock()
    )
    assert agent.ensure_ready() is True
    read_settings.assert_called_once_with()
    make_client.assert_called_once_with(ai_settings)
    make_model.assert_called_once_with(ai_settings, http_client=client)
    await agent.aclose()
    client.aclose.assert_awaited_once()
    factory.assert_not_called()


@pytest.mark.parametrize("app_env", ["local", "test"])
@bounded_test
async def test_default_agent_is_lazy_and_uses_sample_port(
    app_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local·test 기본 구성 → DB 접속 없이 지연 모델·HTTP 클라이언트와 합성 포트를 재사용한다."""
    ai_settings = Mock(llm_max_concurrency=2)
    read_settings = Mock(return_value=ai_settings)
    client = AsyncMock()
    make_client = Mock(return_value=client)
    make_model = Mock(return_value=MODEL)
    factory = Mock(side_effect=AssertionError("DB must stay unopened"))
    monkeypatch.setattr(agent_module, "get_ai_settings", read_settings)
    monkeypatch.setattr(agent_module, "make_http_client", make_client)
    monkeypatch.setattr(agent_module, "create_chat_model", make_model)
    agent = default_agent(Settings(_env_file=None, app_env=app_env), factory)
    read_settings.assert_not_called()
    make_client.assert_not_called()
    make_model.assert_not_called()
    port = agent._port_factory("local-user", NOW)
    result = await port.get_metric_result(
        "local-user", "exercise", NOW.date(), NOW.date()
    )
    assert result.exercise_activity() is not None
    baseline = await sample_health_data("local-user", NOW).get_metric_result(
        "local-user", "exercise", NOW.date(), NOW.date()
    )
    snapshot = dict(result.snapshot)
    snapshot["detail"] = dict(snapshot["detail"])
    snapshot["detail"].pop("exercise_activity")
    assert snapshot == baseline.snapshot
    assert agent.ensure_ready()
    assert agent.ensure_ready()
    make_client.assert_called_once_with(ai_settings)
    make_model.assert_called_once_with(ai_settings, http_client=client)
    await agent.aclose()
    await agent.aclose()
    client.aclose.assert_awaited_once()
    factory.assert_not_called()


@bounded_test
async def test_runner_passes_completed_history_and_context() -> None:
    """완료·실패 이력이 섞이면 → 완료된 문맥만 순서대로 인증 컨텍스트와 전달한다."""
    harness = Harness()
    async with harness.factory() as session:
        await harness.store.complete(
            cast(AsyncSession, session),
            request_id=harness.request.id,
            answer="이전 합성 답변",
            now=NOW,
        )
        failed = harness.store.seed_accepted(
            user_id=harness.user_id,
            conversation_id=harness.request.conversation_id,
            content="실패한 합성 질문",
        )
        await harness.store.fail(
            cast(AsyncSession, session),
            request_id=failed.id,
            error_code="AI_UNAVAILABLE",
            error_context=None,
            partial_answer="실패한 부분",
            now=NOW,
        )
    current = harness.store.seed_accepted(
        user_id=harness.user_id,
        conversation_id=harness.request.conversation_id,
        content="이번 합성 질문",
    )
    await harness.runner.submit(
        GenerationJob(
            harness.user_id,
            current,
            current.user_message.content,
            cast(EventChannel, harness.channel),
            0,
            0.125,
        )
    )
    call = harness.script.calls[0]
    assert call["ctx"] == CoachContext(
        user_id=str(harness.user_id),
        conversation_id=str(current.conversation_id),
        request_id=str(current.id),
        profile=SYNTHETIC_PROFILE,
        turns=(
            HistoryTurn(
                request_id=str(harness.request.id),
                question_message_id=str(harness.request.user_message.id),
                answer_message_id=str(harness.request.assistant_message.id),
                question_seq=harness.request.user_message.sequence_no,
                answer_seq=harness.request.assistant_message.sequence_no,
                question=harness.job.question,
                answer="이전 합성 답변",
                answered_on=NOW.date(),
            ),
        ),
    )
    assert "summary" not in call and "history" not in call
    assert call["question"] == current.user_message.content
    assert call["message_id"] == str(current.assistant_message.id)
    assert call["now"] == NOW


@pytest.mark.parametrize("reason", ["stop", "length", "guard", "tool_limit"])
@bounded_test
async def test_runner_success_finish_reasons(reason: FinishReason) -> None:
    """정상 종료 사유 네 가지 → 최종 결과 본문을 completed로 저장한다."""
    harness = Harness(
        ScriptedRunCoach(result=CoachResult("최종 합성 답변", [], reason))
    )
    await harness.submit()
    assert (
        harness.store.state(harness.request.id).assistant_message.content
        == "최종 합성 답변"
    )
    assert harness.channel.events[-1]["type"] == "completed"


@bounded_test
async def test_runner_error_without_internal_event_uses_result_code() -> None:
    """내부 error 이벤트 누락 → 결과의 error_code로 공개 오류를 만든다."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult("부분", [], "error", error_code="llm_unavailable")
        )
    )
    await harness.submit()
    harness.assert_failure("AI_UNAVAILABLE", partial="부분")


@bounded_test
async def test_runner_failed_result_save_failure_uses_partial() -> None:
    """실패 결과 저장까지 실패 → 보조 저장에도 원래 부분 답변을 전달한다."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult("부분", [], "error", error_code="llm_unavailable")
        )
    )
    harness.store.errors["fail"] = RuntimeError("합성 오류")
    await harness.submit()
    calls = [args for name, args in harness.store.calls if name == "fail"]
    assert [call["error_code"] for call in calls] == [
        "AI_UNAVAILABLE",
        "PERSISTENCE_FAILED",
    ]
    assert [call["partial_answer"] for call in calls] == ["부분", "부분"]


@bounded_test
async def test_runner_commit_error_does_not_send_completed() -> None:
    """종결 commit 예외 → 성공을 송신하지 않고 조건부 보조 저장을 시도한다."""
    harness = Harness()

    def fail_final_commit() -> None:
        """두 번째 commit에만 합성 오류를 예약한다."""
        if harness.factory.commits == 2:
            harness.factory.commit_errors.append(RuntimeError("합성 commit 오류"))

    harness.factory.on_commit = fail_final_commit
    await harness.submit()
    assert harness.channel.events[-1]["data"]["code"] == "PERSISTENCE_FAILED"
    assert not any(event["type"] == "completed" for event in harness.channel.events)
    assert harness.factory.opened == 3
    # FakeStore는 commit 전에 반영하므로 보조 fail은 이미 완료된 상태를 보존한다.
    assert harness.store.state(harness.request.id).status == "completed"


@bounded_test
async def test_runner_unexpected_task_exception_is_safely_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """예상 밖 작업 예외 → done callback이 참조를 제거하고 종류만 기록한다."""
    harness = Harness()

    async def broken_run(job: GenerationJob) -> None:
        """작업 경계 밖의 예상하지 못한 오류를 재현한다."""
        raise ValueError("합성 비공개 버그")

    monkeypatch.setattr(harness.runner, "_run", broken_run)
    with pytest.raises(ValueError, match="합성 비공개 버그"):
        await harness.submit()
    assert harness.runner.active_count == 0
    assert "ValueError" in caplog.text
    assert "합성 비공개 버그" not in caplog.text


@bounded_test
async def test_runner_close_bounds_cancellation_resistant_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """취소 처리가 지연되면 → 종료 대기 상한 뒤에도 남은 작업 참조를 유지한다."""
    harness = Harness()
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def delayed_cancellation(job: GenerationJob) -> None:
        """취소를 관측한 뒤 정리 지연을 재현하고 명시적으로 풀 때 종료한다."""
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    monkeypatch.setattr(harness.runner, "_run", delayed_cancellation)
    task = harness.submit()
    try:
        await entered.wait()
        await harness.runner.aclose(timeout=0.02)
        assert cancelled.is_set()
        assert not task.done()
        assert harness.runner.active_count == 1
    finally:
        release.set()
        await task
    await harness.runner.aclose(timeout=0.02)
    assert harness.runner.active_count == 0


@bounded_test
async def test_runner_port_failure_logs_no_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """건강 포트 생성 실패 → 안전한 오류 로그와 미관측 delta 시각을 남긴다."""
    harness = Harness()

    def broken_port(user_id: str, now: Any) -> Any:
        """포트 구성 중 합성 비공개 문구가 포함된 예외를 재현한다."""
        raise ValueError("합성 비공개 건강 값")

    harness.agent._port_factory = broken_port
    caplog.set_level(logging.INFO, logger="app.coaching.runner")
    await harness.submit()
    harness.assert_failure("INTERNAL_ERROR")
    assert harness.script.calls == []
    assert "ValueError" in caplog.text
    assert "합성 비공개 건강 값" not in caplog.text
    assert "first_delta_ms=-" in caplog.text
    assert "outcome=failed code=INTERNAL_ERROR" in caplog.text


@bounded_test
async def test_runner_preparation_commit_error_never_calls_agent() -> None:
    """준비 commit 실패 → 세션을 정리하고 에이전트 없이 내부 오류로 종결한다."""
    harness = Harness()
    harness.factory.commit_errors.append(RuntimeError("합성 준비 commit 오류"))
    await harness.submit()
    harness.assert_failure("INTERNAL_ERROR")
    assert harness.script.calls == []
    assert harness.factory.rollbacks == 1
    assert harness.factory.active == 0


@bounded_test
async def test_runner_final_evidence_pair_persists_and_sends_same_items() -> None:
    """정상 근거 두 개 → 완료 저장 인자·상태·잠정 및 최종 이벤트가 일치한다."""
    items = [synthetic_evidence(), synthetic_evidence(2)]
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "evidence", "items": [item.event_item() for item in items]}],
            result=CoachResult("합성 근거 답변", items, "stop"),
        )
    )
    await harness.submit()
    call = next(args for name, args in harness.store.calls if name == "complete")
    expected = (synthetic_evidence_info(), synthetic_evidence_info(2))
    assert call["evidence"] == expected
    assert harness.store.state(harness.request.id).evidence == expected
    expected_json = [
        {
            "id": "66666666-6666-4666-8666-666666666666",
            "position": 1,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": SYNTHETIC_SNAPSHOT,
        },
        {
            "id": "77777777-7777-4777-8777-777777777777",
            "position": 2,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": SYNTHETIC_SNAPSHOT,
        },
    ]
    provisional = next(
        event for event in harness.channel.events if event["type"] == "evidence"
    )
    assert provisional["data"]["items"] == expected_json
    assert harness.channel.events[-1]["type"] == "completed"
    assert harness.channel.events[-1]["data"]["evidence"] == expected_json


@bounded_test
async def test_runner_guard_without_evidence_completes_with_empty_tuple() -> None:
    """근거 없는 경계 안내 → 빈 근거로 완료하고 evidence 이벤트는 없다."""
    harness = Harness(ScriptedRunCoach(result=CoachResult("합성 안내", [], "guard")))
    await harness.submit()
    call = next(args for name, args in harness.store.calls if name == "complete")
    assert call["evidence"] == ()
    assert harness.store.state(harness.request.id).evidence == ()
    assert harness.channel.events[-1]["data"]["evidence"] == []
    assert not any(event["type"] == "evidence" for event in harness.channel.events)


@bounded_test
async def test_runner_llm_failure_after_evidence_never_completes() -> None:
    """잠정 근거 뒤 LLM 실패 → fail만 호출하고 저장 근거 없이 error로 끝난다."""
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "evidence", "items": [synthetic_evidence().event_item()]}],
            result=CoachResult(
                "합성 부분 답변", [], "error", error_code="llm_unavailable"
            ),
        )
    )
    await harness.submit()
    harness.assert_failure("AI_UNAVAILABLE", partial="합성 부분 답변")
    assert not any(name == "complete" for name, _ in harness.store.calls)
    assert any(event["type"] == "evidence" for event in harness.channel.events)
    assert harness.store.state(harness.request.id).evidence == ()


def _bad_evidence(position: int = 2) -> Any:
    """user_id가 섞여 형식이 틀린 합성 근거를 만든다."""
    return replace(
        synthetic_evidence(
            position, id=f"9999999{position}-9999-4999-8999-999999999999"
        ),
        snapshot={
            **SYNTHETIC_SNAPSHOT,
            "user_id": "88888888-8888-4888-8888-888888888888",
        },
    )


def _good_evidence(position: int) -> Any:
    """지정 번호의 정상 합성 근거를 만든다(3번 이상은 별도 ID를 쓴다)."""
    if position <= 2:
        return synthetic_evidence(position)
    return synthetic_evidence(position, id="55555555-5555-4555-8555-555555555555")


def _assert_skipped_log_is_safe(
    caplog: pytest.LogCaptureFixture, count: int, metrics: str
) -> None:
    """근거 건너뜀 로그가 한 줄이고 지표 이름만 담고 값·요약·ID·traceback은 없음을 확인한다."""
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        f"coaching evidence skipped={count} metrics={metrics}"
    )
    assert "88888888-8888-4888-8888-888888888888" not in caplog.text
    assert "합성 요약 문장" not in caplog.text
    assert "66666666-6666-4666-8666-666666666666" not in caplog.text
    assert caplog.records[0].exc_info is None


@bounded_test
async def test_runner_invalid_final_evidence_completes_without_it_and_hides_input(
    caplog,
) -> None:
    """최종 근거가 틀린 것 하나뿐 → 답변은 completed, 근거 0개, 값은 로그에 없다."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult("합성 답변", [_bad_evidence(1)], "stop"),
        )
    )
    with caplog.at_level(logging.WARNING, logger="app.coaching.runner"):
        await harness.submit()
    assert sum(name == "complete" for name, _ in harness.store.calls) == 1
    assert not any(name == "fail" for name, _ in harness.store.calls)
    call = next(args for name, args in harness.store.calls if name == "complete")
    assert call["answer"] == "합성 답변"
    assert call["evidence"] == ()
    assert harness.store.state(harness.request.id).evidence == ()
    assert harness.channel.events[-1]["type"] == "completed"
    assert harness.channel.events[-1]["data"]["evidence"] == []
    _assert_skipped_log_is_safe(caplog, 1, "steps")


@bounded_test
async def test_runner_final_evidence_good_then_two_bad_keeps_only_good(
    caplog,
) -> None:
    """최종 근거 [정상 1, 틀림 2, 틀림 2] → completed, 정상 1개만 저장하고 skipped=2 로그."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult(
                "합성 답변",
                [_good_evidence(1), _bad_evidence(2), _bad_evidence(2)],
                "stop",
            ),
        )
    )
    with caplog.at_level(logging.WARNING, logger="app.coaching.runner"):
        await harness.submit()
    call = next(args for name, args in harness.store.calls if name == "complete")
    assert call["evidence"] == (synthetic_evidence_info(),)
    assert not any(name == "fail" for name, _ in harness.store.calls)
    final = harness.channel.events[-1]
    assert final["type"] == "completed"
    assert [item["position"] for item in final["data"]["evidence"]] == [1]
    _assert_skipped_log_is_safe(caplog, 2, "steps,steps")


@bounded_test
async def test_runner_final_evidence_bad_middle_keeps_positions_without_renumber(
    caplog,
) -> None:
    """최종 근거 [정상 1, 틀림 2, 정상 3] → position [1, 3]을 유지하고 다시 매기지 않는다."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult(
                "합성 답변",
                [_good_evidence(1), _bad_evidence(2), _good_evidence(3)],
                "stop",
            ),
        )
    )
    with caplog.at_level(logging.WARNING, logger="app.coaching.runner"):
        await harness.submit()
    call = next(args for name, args in harness.store.calls if name == "complete")
    assert [item.position for item in call["evidence"]] == [1, 3]
    assert [
        item.position for item in harness.store.state(harness.request.id).evidence
    ] == [1, 3]
    final = harness.channel.events[-1]
    assert [item["position"] for item in final["data"]["evidence"]] == [1, 3]
    _assert_skipped_log_is_safe(caplog, 1, "steps")


@bounded_test
async def test_runner_provisional_and_final_evidence_match_for_same_input() -> None:
    """같은 근거를 중간 이벤트와 최종 결과로 넣음 → 마지막 evidence 이벤트와 completed가 같다."""
    items = [_good_evidence(1), _bad_evidence(2), _good_evidence(3)]
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "evidence", "items": [item.event_item() for item in items]}],
            result=CoachResult("합성 답변", items, "stop"),
        )
    )
    await harness.submit()
    provisional = [e for e in harness.channel.events if e["type"] == "evidence"][-1]
    completed = harness.channel.events[-1]
    assert completed["type"] == "completed"
    assert [item["position"] for item in provisional["data"]["items"]] == [1, 3]
    assert completed["data"]["evidence"] == provisional["data"]["items"]


@bounded_test
async def test_runner_timeout_after_evidence_never_saves_evidence() -> None:
    """잠정 근거 뒤 기한 초과 → 완료 호출 없이 시간 초과로 닫고 근거는 비어 있다."""
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "evidence", "items": [synthetic_evidence().event_item()]}],
            forever=True,
        ),
        deadline_seconds=0.03,
    )
    await harness.submit()
    harness.assert_failure("GENERATION_TIMEOUT")
    assert any(event["type"] == "evidence" for event in harness.channel.events)
    assert not any(name == "complete" for name, _ in harness.store.calls)
    assert harness.store.state(harness.request.id).evidence == ()


@bounded_test
async def test_runner_shutdown_after_evidence_never_saves_evidence() -> None:
    """잠정 근거 뒤 앱 종료 → 완료 호출 없이 중단으로 닫고 근거는 비어 있다."""
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "evidence", "items": [synthetic_evidence().event_item()]}],
            forever=True,
        )
    )
    task = harness.submit()
    await harness.script.entered.wait()
    await harness.runner.aclose(timeout=0.05)
    assert task.cancelled()
    harness.assert_failure("GENERATION_INTERRUPTED")
    assert any(event["type"] == "evidence" for event in harness.channel.events)
    assert not any(name == "complete" for name, _ in harness.store.calls)
    assert harness.store.state(harness.request.id).evidence == ()


@bounded_test
async def test_runner_evidence_insert_failure_uses_auxiliary_failure_save() -> None:
    """근거 INSERT 제약 오류 → 기존 저장 실패 경로로 롤백하고 보조 fail만 저장한다."""
    harness = Harness(
        ScriptedRunCoach(
            result=CoachResult("합성 답변", [synthetic_evidence()], "stop")
        )
    )
    harness.store.errors["complete"] = IntegrityError(
        "synthetic-sql", {}, Exception("synthetic-private")
    )
    await harness.submit()
    harness.assert_failure("PERSISTENCE_FAILED", partial="합성 답변")
    assert harness.factory.rollbacks == 1
    assert harness.factory.opened == 3
    calls = [
        (name, args)
        for name, args in harness.store.calls
        if name in {"complete", "fail"}
    ]
    assert [name for name, _ in calls] == ["complete", "fail"]
    assert calls[0][1]["session"] is not calls[1][1]["session"]
    assert "evidence" not in calls[1][1]
    assert harness.store.state(harness.request.id).evidence == ()


@bounded_test
async def test_runner_final_result_ignores_provisional_accumulation() -> None:
    """잠정 근거와 최종 근거가 다름 → 최종 CoachResult의 근거만 저장·전송한다."""
    harness = Harness(
        ScriptedRunCoach(
            [{"type": "evidence", "items": [synthetic_evidence().event_item()]}],
            result=CoachResult("합성 안내", [], "guard"),
        )
    )
    await harness.submit()
    assert harness.store.state(harness.request.id).evidence == ()
    assert harness.channel.events[-1]["data"]["evidence"] == []
