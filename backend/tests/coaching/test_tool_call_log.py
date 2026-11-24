"""DB 없이 도구 호출 기록의 행 변환과 요청이 끝날 때의 저장 시점·실패 처리를 검증한다."""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import repository
from app.coaching.lifecycle import coaching_lifespan
from app.coaching.repository import ToolCallInfo
from app.coaching.runner import CoachingRunner
from app.coaching.tool_call_log import ToolCallLog
from app.core.config import Settings
from coach_ai.adapters.memory import InMemoryHealthData, StepDay, SyncState
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.agent.graph import CoachResult
from coach_ai.security.audit import ToolCallRecord
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.handlers import HANDLERS
from coach_ai.tools.periods import DateRange
from tests.coaching.fakes import (
    EVIDENCE_IDS,
    NOW,
    ScriptedRunCoach,
    fixed_now,
    synthetic_tool_call,
)
from tests.coaching.test_coaching_agent import (
    SSE_HEADERS,
    ScriptedHTTP,
    real_agent,
    text_body,
    tool_body,
)
from tests.coaching.test_coaching_runner import Harness, bounded_test

MEMBER = "00000000-0000-4000-8000-0000000000a1"
REQUEST_ID = UUID("00000000-0000-4000-8000-0000000000b2")
# 목요일. 한국 시간 12:00 이고 11:00 까지 동기화했다
TODAY = date(2026, 10, 22)
GATEWAY_NOW = datetime(2026, 10, 22, 3, tzinfo=UTC)
SYNCED = datetime(2026, 10, 22, 2, tzinfo=UTC)
STEP_VALUE = 7412
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def synthetic_port() -> InMemoryHealthData:
    """10-19·10-20 걸음만 있는 합성 회원. 10-21 은 기록 없음, 10-22 는 동기화 전이다."""
    states = {
        metric: SyncState(last_data_at=SYNCED)
        for metric in ("steps", "exercise", "sleep", "heart_rate")
    }
    return InMemoryHealthData(
        now=GATEWAY_NOW,
        steps={MEMBER: [StepDay(date(2026, 10, day), STEP_VALUE) for day in (19, 20)]},
        sync={MEMBER: states},
    )


def steps_on(period: str, call_id: str) -> ToolCall:
    """걸음 기간 요약 호출을 만든다."""
    return ToolCall(
        call_id, "get_metric_summary", {"metric": "steps", "period": period}
    )


async def gateway_log(
    calls: Sequence[ToolCall], *, request_id: UUID = REQUEST_ID, **handlers: Any
) -> ToolCallLog:
    """실제 AI 게이트웨이가 쓰는 기록을 모은다. 실패로 끝나는 응답의 기록도 모인다."""
    log = ToolCallLog()
    gateway = ToolGateway(
        synthetic_port(), TODAY, handlers={**HANDLERS, **handlers}, audit=log
    )
    ctx = CoachContext(
        user_id=MEMBER, conversation_id=str(uuid4()), request_id=str(request_id)
    )
    with suppress(Exception):
        await gateway.execute(calls, ctx)
    return log


def stored_text(rows: Sequence[ToolCallInfo]) -> str:
    """행 전체를 검사하되 UUID·시각의 우연한 숫자 일치는 건강값 누출과 구분한다."""
    text = json.dumps(
        [asdict(row) for row in rows],
        ensure_ascii=False,
        default=lambda value: (
            "<timestamp>" if isinstance(value, datetime) else str(value)
        ),
    )
    return UUID_TEXT.sub("", text)


def synthetic_records(request_id: UUID) -> list[ToolCallRecord]:
    """허용·거부·동기화 전 결과가 섞인 두 도구 왕복의 AI 기록."""
    return [
        synthetic_tool_call(request_id, 1),
        synthetic_tool_call(
            request_id,
            2,
            tool_name="export_all_users",
            args=None,
            period_dates={},
            decision="deny",
            reason="unknown_tool",
            result_status=None,
            latency_ms=0,
            result_count=0,
            evidence_ids=(),
        ),
        synthetic_tool_call(
            request_id,
            3,
            round_no=2,
            tool_name="get_sleep_records",
            args={"period": "days_ago:0"},
            period_dates={"period": DateRange(date(2026, 10, 25), date(2026, 10, 25))},
            result_status="unsynced",
            result_count=1,
            evidence_ids=(EVIDENCE_IDS[1],),
        ),
    ]


def expected_rows(request_id: UUID) -> list[ToolCallInfo]:
    """synthetic_records 를 변환 함수 없이 저장 행으로 적는다."""
    first = ToolCallInfo(
        request_id=request_id,
        sequence_no=1,
        round_no=1,
        tool_name="get_metric_summary",
        args={"metric": "steps", "period": "recent_days:7"},
        period_dates={"period": {"start": "2026-10-19", "end": "2026-10-25"}},
        decision="allow",
        deny_reason=None,
        result_status="ok",
        latency_ms=12,
        result_count=7,
        evidence_ids=(UUID(EVIDENCE_IDS[0]),),
        created_at=NOW,
    )
    return [
        first,
        replace(
            first,
            sequence_no=2,
            tool_name="export_all_users",
            args=None,
            period_dates={},
            decision="deny",
            deny_reason="unknown_tool",
            result_status=None,
            latency_ms=0,
            result_count=0,
            evidence_ids=(),
        ),
        replace(
            first,
            sequence_no=3,
            round_no=2,
            tool_name="get_sleep_records",
            args={"period": "days_ago:0"},
            period_dates={"period": {"start": "2026-10-25", "end": "2026-10-25"}},
            result_status="unsynced",
            result_count=1,
            evidence_ids=(UUID(EVIDENCE_IDS[1]),),
        ),
    ]


class RecordingToolCallStore:
    """도구 호출 저장의 행을 기록하고 지정한 오류나 대기를 적용한다."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        """저장된 행 목록과 저장 시작 때 실행할 콜백 자리를 준비한다."""
        self.saved: list[list[ToolCallInfo]] = []
        self.error = error
        self.gate = gate
        self.on_save: Callable[[], None] | None = None

    async def save_tool_calls(
        self, session: AsyncSession, *, rows: Sequence[ToolCallInfo]
    ) -> None:
        """콜백·대기·오류를 차례로 적용한 뒤 행을 기록한다."""
        if self.on_save is not None:
            self.on_save()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        self.saved.append(list(rows))


class ToolCallHarness(Harness):
    """도구 호출 저장소를 연결한 실행기로 기본 하네스를 다시 조립한다."""

    def __init__(
        self,
        script: ScriptedRunCoach | None = None,
        *,
        tool_store: RecordingToolCallStore | None = None,
        tool_call_timeout: float = 0.05,
        deadline_seconds: float = 120,
    ) -> None:
        """기본 합성 요청의 AI 기록 세 건을 실행 함수에 넣고 저장소를 연결한다."""
        super().__init__(script, deadline_seconds=deadline_seconds)
        self.script.tool_calls = synthetic_records(self.request.id)
        self.tool_store = tool_store or RecordingToolCallStore()
        self.runner = CoachingRunner(
            session_factory=cast(
                Callable[[], AbstractAsyncContextManager[AsyncSession]], self.factory
            ),
            agent=self.agent,
            store=self.store,
            clock=fixed_now,
            finalize_timeout=0.05,
            tool_call_store=self.tool_store,
            tool_call_timeout=tool_call_timeout,
        )


def test_stored_text_ignores_timestamp_digits_but_keeps_payload_values() -> None:
    row = replace(
        expected_rows(REQUEST_ID)[0],
        created_at=datetime(2026, 11, 16, microsecond=374126, tzinfo=UTC),
    )
    assert "7412" not in stored_text([row])
    assert "7412" in stored_text([replace(row, args={"unexpected": "7412"})])


async def test_rows_keep_gateway_order_dates_and_status() -> None:
    log = await gateway_log(
        [
            steps_on("weekday:mon", "c1"),
            ToolCall("c2", "export_all_users", {"user_id": MEMBER}),
            steps_on("date:10-21", "c3"),
            steps_on("days_ago:0", "c4"),
        ]
    )
    rows = log.rows()
    assert [
        (
            r.sequence_no,
            r.round_no,
            r.tool_name,
            r.decision,
            r.deny_reason,
            r.result_status,
        )
        for r in rows
    ] == [
        (1, 1, "get_metric_summary", "allow", None, "ok"),
        (2, 1, "export_all_users", "deny", "unknown_tool", None),
        (3, 1, "get_metric_summary", "allow", None, "no_record"),
        (4, 1, "get_metric_summary", "allow", None, "unsynced"),
    ]
    first, denied = rows[0], rows[1]
    assert first.request_id == REQUEST_ID
    assert first.args == {"metric": "steps", "period": "weekday:mon"}
    # 10-22 목요일 기준 지난 월요일
    assert first.period_dates == {
        "period": {"start": "2026-10-19", "end": "2026-10-19"}
    }
    assert first.result_count == 1
    assert [type(evidence_id) for evidence_id in first.evidence_ids] == [UUID]
    assert first.created_at.tzinfo is not None
    assert (denied.args, denied.period_dates, denied.evidence_ids) == (None, {}, ())


async def test_rows_hold_no_health_values_or_exception_text() -> None:
    async def broken(inp: Any, req: Any) -> Any:
        raise RuntimeError(f"{MEMBER} 수면 412분 조회 중 db-01 연결 끊김")

    log = await gateway_log(
        [
            steps_on("recent_days:7", "c1"),
            ToolCall("c2", "get_sleep_records", {"period": "days_ago:0"}),
        ],
        get_sleep_records=broken,
    )
    rows = log.rows()
    assert [r.result_status for r in rows] == ["ok", "internal"]
    stored = stored_text(rows)
    for secret in ("7412", "7,412", "14824", "14,824", "412분", "db-01", "연결 끊김"):
        assert secret not in stored


async def test_real_run_saves_rows_without_question_answer_or_values() -> None:
    question = "혈압 135/88인데 지난 7일 걸음 수 알려줘"
    answer = ("합성 걸음 평균을 ", "확인했어요.")
    http = ScriptedHTTP(
        [
            httpx.Response(200, content=tool_body(), headers=SSE_HEADERS),
            httpx.Response(200, content=text_body(*answer), headers=SSE_HEADERS),
        ]
    )
    tool_store = RecordingToolCallStore()
    async with real_agent(http) as harness:
        runner = CoachingRunner(
            session_factory=cast(
                Callable[[], AbstractAsyncContextManager[AsyncSession]],
                harness.factory,
            ),
            agent=harness.agent,
            store=harness.store,
            clock=fixed_now,
            finalize_timeout=0.1,
            tool_call_store=tool_store,
        )
        job, channel = harness.job(question)
        try:
            await runner.submit(job)
        finally:
            await runner.aclose(timeout=0.05)
    assert channel.events[-1]["type"] == "completed"
    [rows] = tool_store.saved
    assert [(r.sequence_no, r.tool_name, r.args, r.result_status) for r in rows] == [
        (1, "get_metric_summary", {"metric": "steps", "period": "last_7_days"}, "ok")
    ]
    snapshot = harness.store.state(job.request.id).evidence[0].snapshot
    values = [
        int(day["value"]) for day in snapshot["days"] if (day["value"] or 0) >= 1000
    ]
    assert values
    stored = stored_text(rows)
    texts = (question, "135/88", "혈압", "".join(answer), "확인했어요")
    numbers = [text for value in values for text in (str(value), f"{value:,}")]
    for secret in (*texts, *numbers):
        assert secret not in stored


@bounded_test
async def test_runner_saves_tool_calls_once_after_completed_event() -> None:
    harness = ToolCallHarness()
    sent_before_save: list[str] = []
    harness.tool_store.on_save = lambda: sent_before_save.extend(
        event["type"] for event in harness.channel.events
    )
    await harness.submit()
    assert harness.store.state(harness.request.id).status == "completed"
    assert harness.tool_store.saved == [expected_rows(harness.request.id)]
    assert sent_before_save[-1] == "completed"
    assert isinstance(harness.script.calls[0]["audit"], ToolCallLog)
    # 생성 시작, 종결, 호출 기록이 각자의 세션에서 commit 된다
    assert (harness.factory.opened, harness.factory.commits) == (3, 3)


@bounded_test
async def test_runner_saves_tool_calls_of_failed_request() -> None:
    script = ScriptedRunCoach(
        [{"type": "token", "delta": "중간"}],
        result=CoachResult("중간", [], "error", error_code="llm_unavailable"),
    )
    harness = ToolCallHarness(script)
    await harness.submit()
    assert harness.store.state(harness.request.id).status == "failed"
    assert harness.tool_store.saved == [expected_rows(harness.request.id)]


@bounded_test
async def test_runner_saves_tool_calls_written_before_deadline() -> None:
    harness = ToolCallHarness(
        ScriptedRunCoach([{"type": "token", "delta": "중간"}], forever=True),
        deadline_seconds=0.03,
    )
    await harness.submit()
    harness.assert_failure("GENERATION_TIMEOUT", partial="중간")
    assert harness.tool_store.saved == [expected_rows(harness.request.id)]


@bounded_test
async def test_runner_saves_tool_calls_after_interrupt_save() -> None:
    harness = ToolCallHarness(
        ScriptedRunCoach([{"type": "token", "delta": "중간"}], forever=True)
    )
    status_at_save: list[str] = []
    harness.tool_store.on_save = lambda: status_at_save.append(
        harness.store.state(harness.request.id).status
    )
    task = harness.submit()
    await harness.script.entered.wait()
    await harness.runner.aclose(timeout=0.2)
    assert task.cancelled()
    harness.assert_failure("GENERATION_INTERRUPTED", partial="중간")
    assert harness.tool_store.saved == [expected_rows(harness.request.id)]
    assert status_at_save == ["failed"]


@bounded_test
async def test_tool_call_save_failure_keeps_answer_and_logs_error_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "합성 비공개 DB 오류 7412"
    harness = ToolCallHarness(
        tool_store=RecordingToolCallStore(
            error=IntegrityError(secret, {}, Exception(secret))
        )
    )
    caplog.set_level(logging.WARNING, logger="app.coaching.runner")
    await harness.submit()
    state = harness.store.state(harness.request.id)
    assert (state.status, state.assistant_message.content) == (
        "completed",
        "합성 비공개 답변",
    )
    assert harness.channel.events[-1]["type"] == "completed"
    assert "coaching tool call log failed: IntegrityError at" in caplog.text
    assert secret not in caplog.text
    assert harness.factory.rollbacks == 1


@bounded_test
async def test_tool_call_save_timeout_keeps_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = ToolCallHarness(
        tool_store=RecordingToolCallStore(gate=asyncio.Event()),
        tool_call_timeout=0.02,
    )
    caplog.set_level(logging.WARNING, logger="app.coaching.runner")
    await harness.submit()
    assert harness.store.state(harness.request.id).status == "completed"
    assert harness.tool_store.saved == []
    assert "coaching tool call log failed: TimeoutError at" in caplog.text


@bounded_test
async def test_runner_skips_tool_call_save_without_tool_calls() -> None:
    harness = ToolCallHarness()
    harness.script.tool_calls = ()
    await harness.submit()
    assert harness.store.state(harness.request.id).status == "completed"
    assert harness.tool_store.saved == []
    assert harness.factory.opened == 2


@bounded_test
async def test_runner_without_tool_call_store_keeps_ai_memory_sink() -> None:
    harness = Harness()
    await harness.submit()
    assert harness.script.calls[0]["audit"] is None
    assert harness.factory.opened == 2


@pytest.mark.parametrize(
    ("app_env", "store"),
    [("dev", repository), ("prod", repository), ("local", None), ("test", None)],
)
async def test_lifespan_saves_tool_calls_in_db_only_on_dev_and_prod(
    app_env: str, store: object
) -> None:
    async with coaching_lifespan(
        FastAPI(),
        Settings(_env_file=None, app_env=app_env),
        Mock(),
        engine=Mock(),
        cleanup=AsyncMock(return_value=0),
    ) as runner:
        assert runner._tool_call_store is store
