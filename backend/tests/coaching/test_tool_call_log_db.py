"""전용 PostgreSQL에서 도구 호출 기록의 저장·조회·제약과 요청이 끝날 때의 저장을 검사한다."""

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.connection import EventChannel
from app.coaching.models import CoachingRequest, CoachingToolCall
from app.coaching.runner import CoachingRunner, GenerationJob
from app.coaching.tool_call_log import ToolCallLog
from coach_ai.agent.executor import ToolCall
from coach_ai.security.audit import ToolCallRecord
from tests.coaching.fakes import (
    NOW,
    CountingModelFactory,
    RecordingChannel,
    RecordingPortFactory,
    ScriptedRunCoach,
    synthetic_tool_call,
)
from tests.coaching.test_coaching_evidence_db import LATER, create_request
from tests.coaching.test_coaching_evidence_db import (
    committed_members as committed_members,
)
from tests.coaching.test_tool_call_log import (
    UUID_TEXT,
    expected_rows,
    gateway_log,
    steps_on,
    synthetic_records,
)

pytestmark = pytest.mark.db
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Members = tuple[AsyncEngine, tuple[UUID, UUID]]
# 저장 함수를 거치지 않는 거부된 호출 한 행. created_at은 넣지 않는다
RAW_ROW = text(
    "INSERT INTO coaching_tool_call (request_id, user_id, sequence_no, round_no, "
    "tool_name, period_dates, decision, deny_reason, latency_ms, result_count, "
    "evidence_ids) VALUES (:request_id, :user_id, 1, 1, 'export_all_users', '{}', "
    "'deny', 'unknown_tool', 0, 0, '{}') RETURNING created_at = now()"
)


@pytest.fixture
async def members(committed_members: Members) -> AsyncIterator[Members]:
    """근거 DB 테스트의 합성 회원을 쓰고, 회원·요청 정리보다 먼저 호출 기록을 지운다."""
    engine, user_ids = committed_members
    try:
        yield engine, user_ids
    finally:
        async with engine.begin() as conn:
            requests = select(CoachingRequest.id).where(
                CoachingRequest.user_id.in_(user_ids)
            )
            await conn.execute(
                delete(CoachingToolCall).where(
                    CoachingToolCall.request_id.in_(requests)
                )
            )


def sessions_of(engine: AsyncEngine) -> SessionFactory:
    """테스트 엔진으로 commit 뒤에도 값을 읽을 수 있는 세션 팩토리를 만든다."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def stored_rows_text(sessions: SessionFactory, request_id: UUID) -> str:
    """저장된 행을 DB가 만든 JSON 글자로 읽고 무작위 UUID는 지운다."""
    async with sessions() as session:
        rows = await session.scalars(
            text(
                "SELECT row_to_json(t)::text FROM coaching_tool_call t "
                "WHERE request_id = :request_id ORDER BY sequence_no"
            ),
            {"request_id": request_id},
        )
        return UUID_TEXT.sub("", "\n".join(rows))


async def stored_user_ids(sessions: SessionFactory, request_id: UUID) -> list[UUID]:
    """요청의 저장된 행마다 회원 ID를 회차 순으로 읽는다."""
    async with sessions() as session:
        rows = await session.scalars(
            text(
                "SELECT user_id FROM coaching_tool_call "
                "WHERE request_id = :request_id ORDER BY sequence_no"
            ),
            {"request_id": request_id},
        )
        return list(rows)


def records_naming(other: UUID) -> Callable[[UUID], list[ToolCallRecord]]:
    """synthetic_records 의 AI 기록 회원 ID만 다른 회원으로 바꾸는 함수를 만든다."""

    def records(request_id: UUID) -> list[ToolCallRecord]:
        """다른 회원 ID를 담은 세 기록."""
        return [
            replace(record, user_id=str(other))
            for record in synthetic_records(request_id)
        ]

    return records


async def run_with_tool_calls(
    sessions: SessionFactory,
    user_id: UUID,
    script: ScriptedRunCoach,
    records: Callable[[UUID], Sequence[ToolCallRecord]],
    *,
    question: str = "합성 호출 기록 질문",
) -> tuple[repository.RequestState, RecordingChannel]:
    """질문으로 요청을 만들고 실제 저장소를 연결한 실행기로 끝까지 실행한다."""
    async with sessions() as session:
        accepted = await repository.accept(
            session,
            user_id=user_id,
            client_request_id=uuid4(),
            conversation_id=None,
            content=question,
            now=NOW,
        )
        assert isinstance(accepted, repository.Accepted)
        await session.commit()
    request = accepted.request
    script.tool_calls = records(request.id)
    agent = CoachAgent(
        model_factory=CountingModelFactory(),
        port_factory=RecordingPortFactory(),
        run=script,
    )
    runner = CoachingRunner(
        session_factory=sessions,
        agent=agent,
        clock=lambda: LATER,
        tool_call_store=repository,
    )
    assert runner.is_ready()
    channel = RecordingChannel()
    job = GenerationJob(
        user_id, request, question, cast(EventChannel, channel), 0, 0.01
    )
    try:
        await runner.submit(job)
    finally:
        try:
            await runner.aclose()
        finally:
            await agent.aclose()
    return request, channel


async def test_saved_rows_are_listed_in_call_order(members: Members) -> None:
    engine, (user_id, _) = members
    sessions = sessions_of(engine)
    request = await create_request(sessions, user_id)
    rows = expected_rows(request.id)
    async with sessions() as session:
        await repository.save_tool_calls(session, rows=[rows[2], rows[0], rows[1]])
        await session.commit()
    async with sessions() as session:
        assert await repository.list_tool_calls(session, request_id=request.id) == rows
        assert await repository.list_tool_calls(session, request_id=uuid4()) == []


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        (
            {"decision": "skip", "deny_reason": None, "result_status": None},
            "ck_coaching_tool_call_decision",
        ),
        ({"deny_reason": "duplicate"}, "ck_coaching_tool_call_deny_reason"),
        (
            {"decision": "deny", "deny_reason": None, "result_status": None},
            "ck_coaching_tool_call_deny_reason",
        ),
        ({"result_status": None}, "ck_coaching_tool_call_result_status"),
        (
            {"decision": "deny", "deny_reason": "duplicate"},
            "ck_coaching_tool_call_result_status",
        ),
        ({"sequence_no": 0}, "ck_coaching_tool_call_sequence_no"),
        ({"round_no": 0}, "ck_coaching_tool_call_round_no"),
        ({"args": ["steps"]}, "ck_coaching_tool_call_args"),
        ({"period_dates": []}, "ck_coaching_tool_call_period_dates"),
    ],
)
async def test_constraints_reject_inconsistent_rows(
    members: Members, change: dict[str, Any], constraint: str
) -> None:
    engine, (user_id, _) = members
    sessions = sessions_of(engine)
    request = await create_request(sessions, user_id)
    row = replace(expected_rows(request.id)[0], **change)
    async with sessions() as session:
        with pytest.raises(IntegrityError, match=constraint):
            await repository.save_tool_calls(session, rows=[row])


async def test_call_order_is_unique_and_request_must_exist(members: Members) -> None:
    engine, (user_id, _) = members
    sessions = sessions_of(engine)
    request = await create_request(sessions, user_id)
    row = expected_rows(request.id)[0]
    async with sessions() as session:
        with pytest.raises(IntegrityError, match="uq_coaching_tool_call_request_id"):
            await repository.save_tool_calls(
                session, rows=[row, replace(row, tool_name="get_daily_trend")]
            )
    # 없는 요청에는 회원 ID를 가져올 행이 없어 외래 키보다 먼저 user_id NOT NULL에서 거부된다
    async with sessions() as session:
        with pytest.raises(IntegrityError, match=r'"user_id" .*not-null'):
            await repository.save_tool_calls(
                session, rows=[replace(row, request_id=uuid4())]
            )


async def test_saved_member_is_request_member_even_if_record_names_another(
    members: Members,
) -> None:
    engine, (first, second) = members
    sessions = sessions_of(engine)
    # 다른 회원도 users에 있어 외래 키로는 거부되지 않는다
    for owner, other in ((first, second), (second, first)):
        request = await create_request(sessions, owner)
        log = ToolCallLog()
        for record in records_naming(other)(request.id):
            await log.write(record)
        async with sessions() as session:
            await repository.save_tool_calls(session, rows=log.rows())
            await session.commit()
        assert await stored_user_ids(sessions, request.id) == [owner, owner, owner]


async def test_runner_saves_request_member_even_if_records_name_another(
    members: Members,
) -> None:
    engine, (user_id, other) = members
    sessions = sessions_of(engine)
    request, channel = await run_with_tool_calls(
        sessions,
        user_id,
        ScriptedRunCoach([{"type": "token", "delta": "합성 답변"}]),
        records_naming(other),
    )
    assert channel.events[-1]["type"] == "completed"
    assert await stored_user_ids(sessions, request.id) == [user_id, user_id, user_id]


async def test_member_and_request_must_exist_and_restrict_delete(
    members: Members,
) -> None:
    engine, (user_id, _) = members
    request = await create_request(sessions_of(engine), user_id)
    async with engine.connect() as conn:
        rules = await conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname IN ('fk_coaching_tool_call_user_id_users', "
                "'fk_coaching_request_conversation_id_coaching_conversation')"
            )
        )
        definitions = dict(rules.all())
    # 회원 외래 키의 삭제 규칙은 coaching_request.user_id가 든 외래 키와 같다
    assert definitions == {
        "fk_coaching_tool_call_user_id_users": (
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT"
        ),
        "fk_coaching_request_conversation_id_coaching_conversation": (
            "FOREIGN KEY (conversation_id, user_id) "
            "REFERENCES coaching_conversation(id, user_id) ON DELETE RESTRICT"
        ),
    }
    for change, constraint in (
        ({"user_id": uuid4()}, "fk_coaching_tool_call_user_id_users"),
        ({"request_id": uuid4()}, "fk_coaching_tool_call_request_id_coaching_request"),
    ):
        async with engine.connect() as conn:
            with pytest.raises(IntegrityError, match=constraint):
                await conn.execute(
                    RAW_ROW, {"request_id": request.id, "user_id": user_id, **change}
                )


async def test_created_at_defaults_to_now(members: Members) -> None:
    engine, (user_id, _) = members
    request = await create_request(sessions_of(engine), user_id)
    # 한 트랜잭션 안의 now()는 같은 값이고, commit하지 않아 행은 저장되지 않는다
    async with engine.connect() as conn:
        assert await conn.scalar(
            RAW_ROW, {"request_id": request.id, "user_id": user_id}
        )


async def test_retention_delete_uses_created_at_index(members: Members) -> None:
    engine, _ = members
    # 행이 적으면 순차 탐색을 고르므로 끄고 보관 기간 삭제 조건의 계획을 확인한다
    async with engine.connect() as conn:
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await conn.scalars(
            text(
                "EXPLAIN DELETE FROM coaching_tool_call "
                "WHERE created_at < now() - interval '30 days'"
            )
        )
        assert "ix_coaching_tool_call_created_at" in "\n".join(plan)


async def test_stored_gateway_rows_hold_no_health_values_or_exception_text(
    members: Members,
) -> None:
    async def broken(inp: Any, req: Any) -> Any:
        raise RuntimeError("수면 412분 조회 중 db-01 연결 끊김")

    engine, (user_id, _) = members
    sessions = sessions_of(engine)
    request = await create_request(sessions, user_id)
    log = await gateway_log(
        [
            steps_on("recent_days:7", "c1"),
            ToolCall("c2", "get_sleep_records", {"period": "days_ago:0"}),
        ],
        request_id=request.id,
        get_sleep_records=broken,
    )
    async with sessions() as session:
        await repository.save_tool_calls(session, rows=log.rows())
        await session.commit()
    stored = await stored_rows_text(sessions, request.id)
    assert '"result_status":"ok"' in stored
    assert '"result_status":"internal"' in stored
    for secret in ("7412", "7,412", "14824", "14,824", "412분", "db-01", "연결 끊김"):
        assert secret not in stored


async def test_runner_saves_calls_after_request_completes(members: Members) -> None:
    engine, (user_id, _) = members
    sessions = sessions_of(engine)
    question = "혈압 135/88인데 지난주 걸음 알려줘"
    script = ScriptedRunCoach([{"type": "token", "delta": "합성 답변 7,412보"}])
    request, channel = await run_with_tool_calls(
        sessions, user_id, script, synthetic_records, question=question
    )
    assert channel.events[-1]["type"] == "completed"
    async with sessions() as session:
        state = await repository.get_request_state(
            session, user_id=user_id, request_id=request.id
        )
        rows = await repository.list_tool_calls(session, request_id=request.id)
    assert state is not None and state.status == "completed"
    assert rows == expected_rows(request.id)
    stored = await stored_rows_text(sessions, request.id)
    for secret in (question, "135/88", "합성 답변", "7,412"):
        assert secret not in stored


async def test_failed_tool_call_save_keeps_completed_request(
    members: Members, caplog: pytest.LogCaptureFixture
) -> None:
    engine, (user_id, _) = members
    sessions = sessions_of(engine)
    caplog.set_level(logging.WARNING, logger="app.coaching.runner")

    def same_order(request_id: UUID) -> list[ToolCallRecord]:
        """회차가 겹쳐 저장이 실패하는 두 기록."""
        return [synthetic_tool_call(request_id, 1), synthetic_tool_call(request_id, 1)]

    request, channel = await run_with_tool_calls(
        sessions,
        user_id,
        ScriptedRunCoach([{"type": "token", "delta": "합성 답변"}]),
        same_order,
    )
    assert channel.events[-1]["type"] == "completed"
    async with sessions() as session:
        state = await repository.get_request_state(
            session, user_id=user_id, request_id=request.id
        )
        assert await repository.list_tool_calls(session, request_id=request.id) == []
    assert state is not None
    assert (state.status, state.assistant_message.content) == ("completed", "합성 답변")
    assert "coaching tool call log failed: IntegrityError at" in caplog.text
