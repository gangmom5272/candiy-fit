"""합성 PostgreSQL로 한마디 포트·게이트웨이와 취소 정리를 함께 검증한다."""

import asyncio
from time import monotonic

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.coaching.agent import default_agent
from app.core.config import Settings
from app.health import daily_message_service, repository
from app.health.models import (
    ActivityDaily,
    ExerciseSession,
    LifestyleScore,
    SleepSession,
    SyncState,
)
from app.health.rest_schemas import daily_message_response
from coach_ai import ports
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.daily_message import get_daily_message
from coach_ai.tools.schemas import TOOL_INPUTS, GetDailyMessage
from tests.health.test_daily_message_repository import NOW, TODAY
from tests.health.test_daily_message_service_db import seed_message

pytestmark = pytest.mark.db


@pytest.fixture
async def message_port(health_db):
    factory, (owner, other) = health_db
    engine = factory.kw["bind"]
    agent = default_agent(
        Settings(_env_file=None, app_env="dev"), factory, engine=engine
    )
    try:
        yield agent._port_factory(str(owner), NOW), engine, factory, owner, other
    finally:
        await agent.aclose()


@pytest.mark.parametrize(
    "case,status",
    [
        ("ready", "READY"),
        ("partial", "PARTIAL"),
        ("empty", "EMPTY"),
        ("waiting", "WAITING"),
    ],
)
async def test_db_snapshot_matches_direct_service_and_other_owner_is_isolated(
    message_port, case, status
):
    port, engine, factory, owner, other = message_port
    await seed_message(factory, owner)
    await seed_message(factory, other)
    async with factory.begin() as session:
        if case == "partial":
            await session.execute(
                update(SyncState)
                .where(SyncState.end_user_id == owner, SyncState.metric == "sleeps")
                .values(initial_sync_done=False)
            )
        if case in ("empty", "waiting"):
            for model in (ActivityDaily, ExerciseSession, LifestyleScore, SleepSession):
                await session.execute(delete(model).where(model.end_user_id == owner))
            if case == "empty":
                await session.execute(
                    update(SyncState)
                    .where(SyncState.end_user_id == owner)
                    .values(first_data_at=None)
                )
            if case == "waiting":
                from datetime import timedelta

                await session.execute(
                    update(SyncState)
                    .where(SyncState.end_user_id == owner)
                    .values(
                        last_success_at=NOW - timedelta(days=1),
                        last_data_at=NOW - timedelta(days=1),
                    )
                )
    result = await port.get_daily_message(
        str(owner), TODAY, request_id="synthetic-port"
    )
    direct = await daily_message_service.get_daily_message(
        owner, today=TODAY, request_id="synthetic-direct", engine=engine
    )
    assert result.snapshot == daily_message_response(direct).model_dump(
        mode="json", by_alias=False
    ) | {"used_metrics": list(direct.used_metrics)}
    assert result.user_id == str(owner)
    assert result.snapshot["status"] == status
    other_result = await port.get_daily_message(
        str(other), TODAY, request_id="synthetic-other"
    )
    assert other_result.user_id == str(other)
    assert other_result.snapshot["status"] == "READY"


@pytest.mark.parametrize("unlinked", [False, True])
async def test_db_not_ready_and_unlinked_are_not_empty_messages(message_port, unlinked):
    port, _, factory, owner, _ = message_port
    await seed_message(factory, owner)
    changes = {"initial_sync_done": False}
    if unlinked:
        changes.update(last_status="fail", last_error="http_404")
    async with factory.begin() as session:
        await session.execute(
            update(SyncState).where(SyncState.end_user_id == owner).values(**changes)
        )
    expected = (
        ports.HealthDataNotLinkedError if unlinked else ports.HealthDataNotReadyError
    )
    with pytest.raises(expected) as caught:
        await port.get_daily_message(str(owner), TODAY, request_id="synthetic-error")
    assert caught.value.metrics == (() if unlinked else ("steps", "exercise", "sleep"))


@pytest.mark.parametrize(
    "slow_block,slow_cleanup",
    [(1, False), (2, False), (3, False), (4, False), (4, True), (0, False)],
)
async def test_gateway_total_deadline_cancels_slow_db_and_releases_connections(
    message_port, monkeypatch, slow_block, slow_cleanup, record_property
):
    port, engine, factory, owner, _ = message_port
    await seed_message(factory, owner)
    monkeypatch.setitem(TOOL_INPUTS, "get_daily_message", GetDailyMessage)
    original = repository.list_sync_states
    calls = 0
    checkouts = []
    event.listen(engine.sync_engine, "checkout", lambda *args: checkouts.append(True))

    async def slow_states(session, user_id):
        nonlocal calls
        calls += 1
        if slow_block == 0:
            await session.execute(text("SELECT pg_sleep(0.3)"))
        if calls == slow_block:
            await session.execute(text("SELECT pg_sleep(10)"))
        return await original(session, user_id)

    monkeypatch.setattr(repository, "list_sync_states", slow_states)
    original_close = AsyncSession.close

    async def slow_close(session):
        if calls == slow_block and isinstance(session.bind, AsyncConnection):
            await asyncio.Event().wait()
        await original_close(session)

    if slow_cleanup:
        monkeypatch.setattr(AsyncSession, "close", slow_close)
    audit = MemoryAuditSink()
    gateway = ToolGateway(
        port, TODAY, handlers={"get_daily_message": get_daily_message}, audit=audit
    )
    started = monotonic()
    async with asyncio.timeout(4):
        with pytest.raises(ports.HealthDataUnavailableError) as caught:
            await gateway.execute(
                [ToolCall("message", "get_daily_message", {})],
                CoachContext(
                    user_id=str(owner),
                    conversation_id="synthetic",
                    request_id="synthetic-timeout",
                ),
            )
    elapsed = monotonic() - started
    record_property("elapsed_seconds", elapsed)
    assert caught.value.metrics == ("steps", "exercise", "sleep")
    if slow_block:
        assert calls == slow_block
    else:
        # 누적 1초 예산은 DB/스케줄링 비용도 포함하므로 취소 블록은 고정되지 않는다.
        assert 1 <= calls <= 4
    assert len(checkouts) == calls
    assert elapsed < 3.5  # 조회 1초 + 정리·폐기 각 1초 + CI 스케줄링 여유
    assert engine.pool.checkedout() == 0
    assert not [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("daily-message-cleanup") and not t.done()
    ]
    assert audit.records()[0].reason == "timeout"
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
