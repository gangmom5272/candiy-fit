"""전용 PostgreSQL에서 최종 근거의 원자적 저장·복구·제약을 검사한다."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, insert, select, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth import tokens
from app.auth.contract import AuthenticatedUser
from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.models import (
    CoachingConversation,
    CoachingEvidence,
    CoachingMessage,
    CoachingRequest,
)
from app.coaching.runner import CoachingRunner
from app.coaching.schemas import (
    Question,
    message_page_data,
    partition_evidence,
    request_state_data,
)
from app.coaching.service import CoachingQuestionHandler
from app.core.db import get_session
from app.health.calculations import sleep_record
from app.users.models import User
from coach_ai.agent.graph import CoachResult
from tests.coaching.fakes import (
    NOW,
    SYNTHETIC_SNAPSHOT,
    CountingModelFactory,
    RecordingChannel,
    RecordingPortFactory,
    ScriptedRunCoach,
    fixed_now,
    synthetic_evidence,
    synthetic_evidence_info,
)
from tests.coaching.kind_fixtures import kind_snapshot
from tests.health.test_calculations import calculate
from tests.health.test_exercise import activity_result
from tests.health.test_rest_response import result_for
from tests.health.test_sleep_detail import DETAIL, detailed_night

pytestmark = pytest.mark.db
LATER = NOW + timedelta(seconds=10)
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def database_url() -> URL:
    """환경변수의 전용 asyncpg 테스트 DB만 허용하며 .env는 읽지 않는다."""
    raw_url = os.environ.get("AUTH_TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail("AUTH_TEST_DATABASE_URL에 Alembic을 적용한 전용 DB를 지정하세요")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "candiy_auth_test"
    ):
        pytest.fail("postgresql+asyncpg 및 candiy_auth_test로 시작하는 DB가 필요합니다")
    return url


def member() -> User:
    """UUIDv4 식별자와 고정 시각의 합성 회원을 만든다."""
    return User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id=str(uuid4()),
        display_name="합성근거회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def committed_members() -> AsyncIterator[tuple[AsyncEngine, tuple[UUID, UUID]]]:
    """합성 회원 둘을 commit하고 근거·메시지·요청·대화·회원 순으로 정리한다."""
    engine = create_async_engine(database_url())
    users = (member(), member())
    user_ids = users[0].id, users[1].id
    try:
        async with AsyncSession(engine) as session:
            session.add_all(users)
            await session.commit()
        yield engine, user_ids
    finally:
        try:
            async with engine.begin() as conn:
                request_ids = select(CoachingRequest.id).where(
                    CoachingRequest.user_id.in_(user_ids)
                )
                assistant_ids = select(CoachingMessage.id).where(
                    CoachingMessage.request_id.in_(request_ids),
                    CoachingMessage.role == "assistant",
                )
                await conn.execute(
                    delete(CoachingEvidence).where(
                        CoachingEvidence.assistant_message_id.in_(assistant_ids)
                    )
                )
                await conn.execute(
                    delete(CoachingMessage).where(
                        CoachingMessage.request_id.in_(request_ids)
                    )
                )
                await conn.execute(
                    delete(CoachingRequest).where(CoachingRequest.user_id.in_(user_ids))
                )
                await conn.execute(
                    delete(CoachingConversation).where(
                        CoachingConversation.user_id.in_(user_ids)
                    )
                )
                await conn.execute(delete(User).where(User.id.in_(user_ids)))
        finally:
            await engine.dispose()


async def create_request(
    sessions: SessionFactory,
    user_id: UUID,
    *,
    conversation_id: UUID | None = None,
    generating: bool = True,
) -> repository.RequestState:
    """새 세션으로 합성 질문을 접수하고 선택적으로 생성 중 상태까지 commit한다."""
    async with sessions() as session:
        result = await repository.accept(
            session,
            user_id=user_id,
            client_request_id=uuid4(),
            conversation_id=conversation_id,
            content="합성 근거 질문",
            now=NOW,
        )
        assert isinstance(result, repository.Accepted)
        assert result.request.evidence == ()
        await session.commit()
    if generating:
        async with sessions() as session:
            assert await repository.mark_generating(
                session, request_id=result.request.id
            )
            await session.commit()
    return result.request


async def read_result(
    sessions: SessionFactory,
    user_id: UUID,
    request_id: UUID,
) -> tuple[repository.RequestState, repository.MessagePage]:
    """새 조회 세션에서 요청·메시지를 읽고 쓰기 호출과 ORM 변경이 없음을 검사한다."""
    async with sessions() as session:
        commit = session.commit = AsyncMock(wraps=session.commit)
        rollback = session.rollback = AsyncMock(wraps=session.rollback)
        flush = session.flush = AsyncMock(wraps=session.flush)
        state = await repository.get_request_state(
            session, user_id=user_id, request_id=request_id
        )
        assert state is not None
        page = await repository.find_message_page(
            session,
            user_id=user_id,
            conversation_id=state.conversation_id,
            limit=100,
            before_sequence_no=None,
        )
        assert page is not None
        commit.assert_not_called()
        rollback.assert_not_called()
        flush.assert_not_called()
        assert not session.new and not session.dirty and not session.deleted
    return state, page


async def evidence_count(sessions: SessionFactory, request_id: UUID) -> int:
    """새 세션에서 해당 요청의 AI 메시지에 속한 근거 행 수를 읽는다."""
    async with sessions() as session:
        return await session.scalar(
            select(func.count())
            .select_from(CoachingEvidence)
            .join(
                CoachingMessage,
                CoachingMessage.id == CoachingEvidence.assistant_message_id,
            )
            .where(CoachingMessage.request_id == request_id)
        )


@pytest.mark.parametrize("distance", ["legacy", None, 0, 1234.5])
async def test_exercise_distance_snapshot_survives_storage_and_history(
    committed_members, distance
):
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    snapshot = result_for("exercises").to_public().model_dump(mode="json")
    detail = snapshot["detail"]["sessions"][0]
    if distance == "legacy":
        del detail["distance_m"]
    else:
        detail["distance_m"] = distance
    item = replace(
        synthetic_evidence_info(),
        metric="exercises",
        period_start=date.fromisoformat(snapshot["period"]["start"]),
        period_end=date.fromisoformat(snapshot["period"]["end"]),
        snapshot=snapshot,
    )
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 운동 거리 답변",
            now=LATER,
            evidence=(item,),
        )
        await session.commit()
    state, page = await read_result(sessions, user_id, request.id)
    assert state.evidence[0].snapshot == snapshot
    assert page.messages[1].evidence[0].snapshot == snapshot
    assert (
        request_state_data(state).model_dump(mode="json")["evidence"][0]["snapshot"]
        == snapshot
    )
    assert (
        message_page_data(page).model_dump(mode="json")["items"][1]["evidence"][0][
            "snapshot"
        ]
        == snapshot
    )


async def test_exercise_activity_snapshot_survives_storage_and_history(
    committed_members,
) -> None:
    """exercise_activity 근거(새)와 구형 근거를 함께 저장 → 재조회에서 값·순서가 그대로이고 구형엔 키가 없다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    new = activity_result().to_public().model_dump(mode="json")
    activity_new = new["detail"]["exercise_activity"]
    activity_new["days"][0]["exercise_distance_m"] = 4200.5
    activity_new["summary"]["exercise_distance_m"]["total"] = 4200.5
    activity_new["summary"]["exercise_distance_m"]["avg"] = 4200.5
    legacy = result_for("exercises").to_public().model_dump(mode="json")
    assert "exercise_activity" not in legacy["detail"]
    items = []
    for position, snapshot in ((1, new), (2, legacy)):
        item = synthetic_evidence(position).event_item()
        item.update(metric="exercises", period=snapshot["period"], snapshot=snapshot)
        items.append(item)
    partition = partition_evidence(items)
    assert partition.skipped_metrics == ()
    assert len(partition.valid) == 2
    request = await create_request(sessions, user_id)
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 운동 활동 답변",
            now=LATER,
            evidence=partition.valid,
        )
        await session.commit()
    state, page = await read_result(sessions, user_id, request.id)
    assert await evidence_count(sessions, request.id) == 2
    assert [e.position for e in state.evidence] == [1, 2]
    for index in (0, 1):
        expected = partition.valid[index].snapshot
        assert state.evidence[index].snapshot == expected
        assert page.messages[1].evidence[index].snapshot == expected
    stored = state.evidence[0].snapshot
    assert (
        request_state_data(state).model_dump(mode="json")["evidence"][0]["snapshot"]
        == stored
    )
    assert (
        message_page_data(page).model_dump(mode="json")["items"][1]["evidence"][0][
            "snapshot"
        ]
        == stored
    )
    activity = stored["detail"]["exercise_activity"]
    days = activity["days"]
    assert days[0]["exercise_distance_m"] == 4200.5
    assert days[1]["exercise_calories_kcal"] == 0
    assert type(days[1]["exercise_calories_kcal"]) is int
    assert days[1]["exercise_distance_m"] is None
    assert days[2]["exercise_count"] is None
    assert days[2]["computed_at"] is None
    assert days[1]["distance_reason"] == "NULL_VALUE"
    assert days[2]["reason"] == "NO_RECORD"
    assert days[3]["is_in_progress"] is True
    assert activity["summary"]["exercise_count"]["avg"] == 0.5
    assert activity["summary"]["exercise_distance_m"]["total"] == 4200.5
    assert activity["source_statuses"]["distances"]["ready"] is None
    assert "exercise_activity" not in state.evidence[1].snapshot["detail"]


async def test_kind_snapshot_survives_storage_and_history(committed_members) -> None:
    """sleep_sessions kind 근거 저장 → 요청 상태·메시지 조회 모두 kind와 세션 수가 같다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    snapshot = kind_snapshot("sleep_sessions")
    item = replace(
        synthetic_evidence_info(),
        metric="sleeps",
        period_start=date(2026, 11, 5),
        period_end=date(2026, 11, 5),
        snapshot=snapshot,
    )
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 수면 답변",
            now=LATER,
            evidence=(item,),
        )
        await session.commit()
    state, page = await read_result(sessions, user_id, request.id)
    from_state = request_state_data(state).model_dump(mode="json")["evidence"][0]
    from_page = message_page_data(page).model_dump(mode="json")["items"][1]["evidence"][
        0
    ]
    for stored in (from_state, from_page):
        assert stored["snapshot"]["kind"] == "sleep_sessions"
        assert len(stored["snapshot"]["sessions"]) == len(snapshot["sessions"])


@pytest.mark.parametrize("kind", ["measured", "null", "legacy"])
async def test_sleep_detail_snapshot_survives_storage_without_backfill(
    committed_members, kind
):
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    result = (
        calculate([sleep_record(detailed_night())], metric="sleeps")
        if kind == "measured"
        else result_for("sleeps")
    )
    snapshot = result.to_public().model_dump(mode="json")
    if kind == "legacy":
        for key in DETAIL:
            del snapshot["detail"]["nights"][0][key]
    item = replace(
        synthetic_evidence_info(),
        metric="sleeps",
        period_start=date.fromisoformat(snapshot["period"]["start"]),
        period_end=date.fromisoformat(snapshot["period"]["end"]),
        snapshot=snapshot,
    )
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 수면 답변",
            now=LATER,
            evidence=(item,),
        )
        await session.commit()
    state, page = await read_result(sessions, user_id, request.id)
    assert (
        request_state_data(state).model_dump(mode="json")["evidence"][0]["snapshot"]
        == snapshot
    )
    assert (
        message_page_data(page).model_dump(mode="json")["items"][1]["evidence"][0][
            "snapshot"
        ]
        == snapshot
    )


def complete_after_select(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    sessions: SessionFactory,
    request_id: UUID,
    *,
    select_number: int,
) -> list[UUID]:
    """지정 SELECT 직후 별도 세션에서 완료를 한 번 커밋하도록 감싸고 실행 기록을 반환한다."""
    execute = session.execute
    selects = 0
    completed: list[UUID] = []

    async def execute_then_complete(statement: Any, *args: Any, **kwargs: Any) -> Any:
        """원래 SELECT 결과를 확보한 뒤 지정 순서에서 최종 본문·근거를 별도 커밋한다."""
        nonlocal selects
        result = await execute(statement, *args, **kwargs)
        if statement.is_select:
            selects += 1
            if selects == select_number:
                async with sessions() as writer:
                    assert await repository.complete(
                        writer,
                        request_id=request_id,
                        answer="합성 경합 완료 답변",
                        now=LATER,
                        evidence=(synthetic_evidence_info(),),
                    )
                    await writer.commit()
                completed.append(request_id)
        return result

    monkeypatch.setattr(session, "execute", execute_then_complete)
    return completed


async def test_request_state_complete_commits_between_selects_returns_no_evidence(
    committed_members, monkeypatch
) -> None:
    """R1 상태 SELECT 직후 완료 커밋 → generating·빈 본문·근거 없음을 반환하고 재조회는 완료를 반환한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id, generating=True)
    async with sessions() as session:
        completed = complete_after_select(
            monkeypatch, session, sessions, request.id, select_number=1
        )
        state = await repository.get_request_state(
            session, user_id=user_id, request_id=request.id
        )
        assert completed == [request.id]
        assert state is not None
        assert state.status == "generating"
        assert state.assistant_message.content == ""
        assert state.evidence == ()
    state, _ = await read_result(sessions, user_id, request.id)
    assert state.status == "completed"
    assert state.assistant_message.content == "합성 경합 완료 답변"
    assert state.evidence == (synthetic_evidence_info(),)
    assert await evidence_count(sessions, request.id) == 1


async def test_duplicate_complete_commits_between_selects_returns_no_evidence(
    committed_members, monkeypatch
) -> None:
    """R2 중복 요청 SELECT 직후 완료 커밋 → generating·빈 본문·근거 없는 Duplicate를 반환한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id, generating=True)
    async with sessions() as session:
        completed = complete_after_select(
            monkeypatch, session, sessions, request.id, select_number=1
        )
        result = await repository.accept(
            session,
            user_id=user_id,
            client_request_id=request.client_request_id,
            conversation_id=None,
            content=request.user_message.content,
            now=LATER,
        )
        assert completed == [request.id]
        assert isinstance(result, repository.Duplicate)
        assert result.request.status == "generating"
        assert result.request.assistant_message.content == ""
        assert result.request.evidence == ()
    state, _ = await read_result(sessions, user_id, request.id)
    assert state.status == "completed"
    assert state.assistant_message.content == "합성 경합 완료 답변"
    assert state.evidence == (synthetic_evidence_info(),)
    assert await evidence_count(sessions, request.id) == 1


async def test_message_page_complete_commits_between_selects_returns_no_evidence(
    committed_members, monkeypatch
) -> None:
    """R3 메시지 SELECT 직후 완료 커밋 → 생성 중 AI와 사용자 근거는 비어 있고 재조회는 완료를 반환한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id, generating=True)
    async with sessions() as session:
        completed = complete_after_select(
            monkeypatch, session, sessions, request.id, select_number=2
        )
        page = await repository.find_message_page(
            session,
            user_id=user_id,
            conversation_id=request.conversation_id,
            limit=50,
            before_sequence_no=None,
        )
        assert completed == [request.id]
        assert page is not None
        user, assistant = page.messages
        assert user.message.role == "user"
        assert user.evidence == ()
        assert assistant.message.role == "assistant"
        assert assistant.request_status == "generating"
        assert assistant.message.content == ""
        assert assistant.evidence == ()
    state, page = await read_result(sessions, user_id, request.id)
    assert state.status == "completed"
    assert state.assistant_message.content == "합성 경합 완료 답변"
    assert state.evidence == (synthetic_evidence_info(),)
    assert page.messages[0].evidence == ()
    assert page.messages[1].request_status == "completed"
    assert page.messages[1].message.content == "합성 경합 완료 답변"
    assert page.messages[1].evidence == (synthetic_evidence_info(),)
    assert await evidence_count(sessions, request.id) == 1


async def storage_counts(
    sessions: SessionFactory, user_id: UUID
) -> tuple[int, int, int, int]:
    """합성 회원의 대화·요청·메시지·근거 행 수를 새 세션에서 읽는다."""
    async with sessions() as session:
        conversations = await session.scalar(
            select(func.count())
            .select_from(CoachingConversation)
            .where(CoachingConversation.user_id == user_id)
        )
        requests = await session.scalar(
            select(func.count())
            .select_from(CoachingRequest)
            .where(CoachingRequest.user_id == user_id)
        )
        messages = await session.scalar(
            select(func.count())
            .select_from(CoachingMessage)
            .join(CoachingRequest, CoachingRequest.id == CoachingMessage.request_id)
            .where(CoachingRequest.user_id == user_id)
        )
        evidence = await session.scalar(
            select(func.count())
            .select_from(CoachingEvidence)
            .join(
                CoachingMessage,
                CoachingMessage.id == CoachingEvidence.assistant_message_id,
            )
            .join(CoachingRequest, CoachingRequest.id == CoachingMessage.request_id)
            .where(CoachingRequest.user_id == user_id)
        )
        return conversations, requests, messages, evidence


class TerminalChannel(RecordingChannel):
    """종료 이벤트 시도를 알리며 연결 종료 뒤 실제 송신은 버린다."""

    def __init__(self) -> None:
        """송신 기록과 종료 대기 이벤트를 준비한다."""
        super().__init__()
        self.finished = asyncio.Event()

    def send(self, value: Mapping[str, Any]) -> None:
        """열린 연결에 기록하고 종료 이벤트 시도 시 대기를 해제한다."""
        super().send(value)
        if value["type"] in {"completed", "error"}:
            self.finished.set()


class DatabaseFlow:
    """실제 접수·실행기·저장소와 합성 AI를 연결한다."""

    def __init__(self, sessions: SessionFactory, run: ScriptedRunCoach) -> None:
        """실제 트랜잭션 팩토리로 에이전트와 실행기·질문 처리기를 조립한다."""
        self.sessions = sessions
        self.run = run
        self.agent = CoachAgent(
            model_factory=CountingModelFactory(),
            port_factory=RecordingPortFactory(),
            run=run,
        )
        self.runner = CoachingRunner(
            session_factory=sessions, agent=self.agent, clock=lambda: LATER
        )
        self.handler = CoachingQuestionHandler(
            session_factory=sessions, runner=self.runner, clock=fixed_now
        )

    async def send(
        self, user_id: UUID, question: Question, channel: TerminalChannel | None = None
    ) -> TerminalChannel:
        """합성 질문을 실제 처리기로 접수하고 해당 연결 기록을 반환한다."""
        channel = channel or TerminalChannel()
        await self.handler.handle(AuthenticatedUser(user_id), question, channel)
        return channel

    async def finish(
        self, user_id: UUID, channel: TerminalChannel
    ) -> repository.RequestState:
        """최대 3초 내 종료를 기다리고 새 세션에서 저장 상태를 읽는다."""
        async with asyncio.timeout(3):
            await channel.finished.wait()
        state, _ = await read_result(
            self.sessions, user_id, UUID(channel.events[0]["requestId"])
        )
        return state


@asynccontextmanager
async def running_flow(
    sessions: SessionFactory, run: ScriptedRunCoach
) -> AsyncIterator[DatabaseFlow]:
    """실제 저장 실행기를 제공하고 종료 때 작업과 에이전트를 정리한다."""
    flow = DatabaseFlow(sessions, run)
    try:
        yield flow
    finally:
        try:
            await flow.runner.aclose()
        finally:
            await flow.agent.aclose()


async def test_runner_two_evidence_commits_and_restores_all_surfaces(
    committed_members,
) -> None:
    """E1 정상 근거 둘 → 저장·조회·이벤트의 최종 근거가 모두 같다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    evidence = [synthetic_evidence(), synthetic_evidence(2)]
    run = ScriptedRunCoach(
        [{"type": "evidence", "items": [item.event_item() for item in evidence]}],
        result=CoachResult("합성 근거 답변", evidence, "stop"),
    )
    sent = Question(uuid4(), None, "합성 근거 질문")
    expected = (synthetic_evidence_info(), synthetic_evidence_info(2))
    async with running_flow(sessions, run) as flow:
        channel = await flow.send(user_id, sent)
        state = await flow.finish(user_id, channel)
        assert state.status == "completed"
        assert state.evidence == expected
        assert state.assistant_message.content == "합성 근거 답변"
        assert await evidence_count(sessions, state.id) == 2
        async with sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(CoachingEvidence)
                        .where(
                            CoachingEvidence.assistant_message_id
                            == state.assistant_message.id
                        )
                        .order_by(CoachingEvidence.position)
                    )
                )
                .scalars()
                .all()
            )
            assert [
                (row.id, row.position, row.assistant_role, row.created_at, row.snapshot)
                for row in rows
            ] == [
                (
                    UUID("66666666-6666-4666-8666-666666666666"),
                    1,
                    "assistant",
                    LATER,
                    SYNTHETIC_SNAPSHOT,
                ),
                (
                    UUID("77777777-7777-4777-8777-777777777777"),
                    2,
                    "assistant",
                    LATER,
                    SYNTHETIC_SNAPSHOT,
                ),
            ]
        state, page = await read_result(sessions, user_id, state.id)
        assert page.messages[0].evidence == ()
        assert page.messages[1].evidence == expected
        completed = channel.events[-1]
        provisional = next(
            value for value in channel.events if value["type"] == "evidence"
        )
        assert completed["type"] == "completed"
        assert completed["data"]["evidence"] == provisional["data"]["items"]
        assert (
            request_state_data(state).model_dump(mode="json")["evidence"]
            == completed["data"]["evidence"]
        )
        assert (
            message_page_data(page).model_dump(mode="json")["items"][1]["evidence"]
            == completed["data"]["evidence"]
        )
        assert await storage_counts(sessions, user_id) == (1, 1, 2, 2)


async def test_runner_final_evidence_with_bad_middle_saves_positions_one_and_three(
    committed_members,
) -> None:
    """E3 최종 근거 [정상 1, 틀림 2, 정상 3] → completed, 실제 저장 position은 1·3이다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    bad = replace(
        synthetic_evidence(2),
        snapshot={
            **SYNTHETIC_SNAPSHOT,
            "user_id": "88888888-8888-4888-8888-888888888888",
        },
    )
    third_id = "55555555-5555-4555-8555-555555555555"
    evidence = [
        synthetic_evidence(),
        bad,
        synthetic_evidence(3, id=third_id),
    ]
    run = ScriptedRunCoach(result=CoachResult("합성 근거 답변", evidence, "stop"))
    sent = Question(uuid4(), None, "합성 근거 질문")
    async with running_flow(sessions, run) as flow:
        channel = await flow.send(user_id, sent)
        state = await flow.finish(user_id, channel)
        assert state.status == "completed"
        assert [item.position for item in state.evidence] == [1, 3]
        assert await evidence_count(sessions, state.id) == 2
        async with sessions() as session:
            positions = (
                (
                    await session.execute(
                        select(CoachingEvidence.position)
                        .where(
                            CoachingEvidence.assistant_message_id
                            == state.assistant_message.id
                        )
                        .order_by(CoachingEvidence.position)
                    )
                )
                .scalars()
                .all()
            )
        assert positions == [1, 3]
        completed = channel.events[-1]
        assert completed["type"] == "completed"
        assert [item["position"] for item in completed["data"]["evidence"]] == [1, 3]


async def test_duplicate_completed_request_restores_evidence_without_generation(
    committed_members,
) -> None:
    """E11 완료 뒤 같은 전송 재시도 → 저장 근거 snapshot만 보내고 행·AI 호출은 늘지 않는다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 완료 답변",
            now=LATER,
            evidence=(synthetic_evidence_info(),),
        )
        await session.commit()
    run = ScriptedRunCoach()
    assert await storage_counts(sessions, user_id) == (1, 1, 2, 1)
    async with running_flow(sessions, run) as flow:
        duplicate = await flow.send(
            user_id, Question(request.client_request_id, None, "합성 근거 질문")
        )
        assert [value["type"] for value in duplicate.events] == ["request_snapshot"]
        assert duplicate.events[0]["eventSeq"] == 0
        assert duplicate.events[0]["data"]["status"] == "completed"
        assert duplicate.events[0]["data"]["evidence"] == [
            {
                "id": "66666666-6666-4666-8666-666666666666",
                "position": 1,
                "metric": "steps",
                "periodStart": "2026-10-12",
                "periodEnd": "2026-10-18",
                "snapshot": SYNTHETIC_SNAPSHOT,
            },
        ]
        assert run.calls == []
        assert await storage_counts(sessions, user_id) == (1, 1, 2, 1)


async def test_runner_guard_without_evidence_stores_no_rows(committed_members) -> None:
    """E3 근거 없는 경계 답변 → 완료하고 모든 저장·조회 근거는 비어 있다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with running_flow(
        sessions, ScriptedRunCoach(result=CoachResult("합성 안내", [], "guard"))
    ) as flow:
        channel = await flow.send(user_id, Question(uuid4(), None, "합성 질문"))
        state = await flow.finish(user_id, channel)
        assert state.status == "completed"
        assert state.evidence == ()
        assert await evidence_count(sessions, state.id) == 0
        assert channel.events[-1]["data"]["evidence"] == []
        assert not any(value["type"] == "evidence" for value in channel.events)
        _, page = await read_result(sessions, user_id, state.id)
        assert all(item.evidence == () for item in page.messages)


async def test_runner_llm_failure_after_evidence_stores_no_rows(
    committed_members,
) -> None:
    """E4 잠정 근거 뒤 LLM 실패 → failed·부분 답변만 저장하고 근거는 없다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run = ScriptedRunCoach(
        [{"type": "evidence", "items": [synthetic_evidence().event_item()]}],
        result=CoachResult("합성 부분 답변", [], "error", error_code="llm_unavailable"),
    )
    async with running_flow(sessions, run) as flow:
        channel = await flow.send(user_id, Question(uuid4(), None, "합성 질문"))
        state = await flow.finish(user_id, channel)
        assert state.status == "failed"
        assert state.error_code == "AI_UNAVAILABLE"
        assert state.evidence == ()
        assert state.assistant_message.content == "합성 부분 답변"
        assert await evidence_count(sessions, state.id) == 0
        assert [value["type"] for value in channel.events] == [
            "accepted",
            "status",
            "evidence",
            "error",
        ]
        _, page = await read_result(sessions, user_id, state.id)
        assert all(item.evidence == () for item in page.messages)


async def test_runner_invalid_final_snapshot_completes_without_evidence(
    committed_members, caplog
) -> None:
    """E2 최종 근거 형식 오류만 있음 → completed·본문 저장, 근거 0행, 값은 로그에 없음."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    invalid = replace(
        synthetic_evidence(),
        snapshot={
            **SYNTHETIC_SNAPSHOT,
            "user_id": "88888888-8888-4888-8888-888888888888",
        },
    )
    async with running_flow(
        sessions,
        ScriptedRunCoach(result=CoachResult("합성 부분 답변", [invalid], "stop")),
    ) as flow:
        channel = await flow.send(user_id, Question(uuid4(), None, "합성 질문"))
        state = await flow.finish(user_id, channel)
        assert state.status == "completed"
        assert state.error_code is None
        assert state.assistant_message.content == "합성 부분 답변"
        assert state.evidence == ()
        assert await evidence_count(sessions, state.id) == 0
        assert channel.events[-1]["type"] == "completed"
        assert channel.events[-1]["data"]["evidence"] == []
        assert not any(value["type"] == "error" for value in channel.events)
        _, page = await read_result(sessions, user_id, state.id)
        assert all(item.evidence == () for item in page.messages)
        assert "coaching evidence skipped=1 metrics=steps" in caplog.text
        assert "88888888-8888-4888-8888-888888888888" not in caplog.text
        assert "합성 요약 문장" not in caplog.text


async def test_complete_duplicate_evidence_id_rolls_back_all_changes(
    committed_members,
) -> None:
    """E9 다른 요청의 근거 ID 재사용 → complete에서 제약 오류이며 롤백 후 본문·상태가 그대로다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first = await create_request(sessions, user_id)
    second = await create_request(sessions, user_id)
    item = synthetic_evidence_info()
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=first.id,
            answer="먼저 완료",
            now=LATER,
            evidence=(item,),
        )
        await session.commit()
    async with sessions() as session:
        with pytest.raises(IntegrityError):
            await repository.complete(
                session,
                request_id=second.id,
                answer="롤백할 답변",
                now=LATER,
                evidence=(item,),
            )
        await session.rollback()
    state, page = await read_result(sessions, user_id, second.id)
    assert state.status == "generating"
    assert state.assistant_message.content == ""
    assert state.finished_at is None
    assert state.evidence == ()
    assert page.conversation.updated_at == NOW
    assert await evidence_count(sessions, first.id) == 1
    assert await evidence_count(sessions, second.id) == 0


async def test_runner_disconnect_after_commit_recovers_stored_evidence(
    committed_members,
) -> None:
    """E10 완료 commit 직후 연결 종료 → 완료 이벤트 없이 새 세션에서 근거를 복구한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    channel = TerminalChannel()
    commits = 0

    @asynccontextmanager
    async def disconnecting_sessions() -> AsyncIterator[AsyncSession]:
        """접수·생성·최종 세 번째 commit 직후 연결을 닫는 세션을 제공한다."""
        async with sessions() as session:
            original_commit = session.commit

            async def commit() -> None:
                """실제 commit 뒤 최종 저장이면 송신 연결을 닫는다."""
                nonlocal commits
                await original_commit()
                commits += 1
                if commits == 3:
                    channel.close()

            session.commit = commit
            yield session

    run = ScriptedRunCoach(
        result=CoachResult("합성 완료 답변", [synthetic_evidence()], "stop")
    )
    async with running_flow(disconnecting_sessions, run) as flow:
        await flow.send(user_id, Question(uuid4(), None, "합성 질문"), channel)
        state = await flow.finish(user_id, channel)
        assert commits == 3
        assert not channel.is_open
        assert not any(
            value["type"] in {"completed", "error"} for value in channel.events
        )
        assert state.status == "completed"
        assert state.evidence == (synthetic_evidence_info(),)
        assert await evidence_count(sessions, state.id) == 1
        _, page = await read_result(sessions, user_id, state.id)
        assert page.messages[1].evidence == (synthetic_evidence_info(),)


async def test_duplicate_generating_request_has_empty_snapshot_and_no_new_run(
    committed_members,
) -> None:
    """E12 생성 중 동일 전송 → 근거 없는 snapshot만 반환하고 행·AI 호출은 늘지 않는다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    gate = asyncio.Event()
    run = ScriptedRunCoach(hold=gate)
    sent = Question(uuid4(), None, "합성 질문")
    async with running_flow(sessions, run) as flow:
        channel = await flow.send(user_id, sent)
        async with asyncio.timeout(3):
            await run.entered.wait()
        assert await storage_counts(sessions, user_id) == (1, 1, 2, 0)
        duplicate = await flow.send(user_id, sent)
        assert len(duplicate.events) == 1
        assert duplicate.events[0]["type"] == "request_snapshot"
        assert duplicate.events[0]["data"]["status"] == "generating"
        assert duplicate.events[0]["data"]["evidence"] == []
        assert len(run.calls) == 1
        assert await storage_counts(sessions, user_id) == (1, 1, 2, 0)
        assert await evidence_count(sessions, UUID(channel.events[0]["requestId"])) == 0
        gate.set()
        await flow.finish(user_id, channel)


async def test_complete_after_interrupt_inserts_no_evidence(committed_members) -> None:
    """E13 기동 정리가 먼저 요청을 닫음 → 완료는 False이고 근거 행도 생기지 않는다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    async with sessions() as session:
        assert (
            await repository.interrupt_in_progress(
                session, now=LATER, created_before=NOW + timedelta(seconds=1)
            )
            >= 1
        )
        await session.commit()
    async with sessions() as session:
        assert not await repository.complete(
            session,
            request_id=request.id,
            answer="늦은 답변",
            now=LATER,
            evidence=(synthetic_evidence_info(),),
        )
        await session.commit()
    state, _ = await read_result(sessions, user_id, request.id)
    assert state.status == "failed"
    assert state.error_code == "GENERATION_INTERRUPTED"
    assert state.assistant_message.content == ""
    assert state.evidence == ()
    assert await evidence_count(sessions, request.id) == 0


async def test_message_page_mixed_states_loads_ordered_evidence_once(
    committed_members,
) -> None:
    """E15 역할·완료·실패·생성 상태 혼합 → 완료 AI 근거만 순서대로 한 번 조회한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    completed = await create_request(sessions, user_id)
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=completed.id,
            answer="완료",
            now=LATER,
            evidence=(synthetic_evidence_info(2), synthetic_evidence_info()),
        )
        await session.commit()
    failed = await create_request(
        sessions, user_id, conversation_id=completed.conversation_id
    )
    async with sessions() as session:
        assert await repository.fail(
            session,
            request_id=failed.id,
            error_code="AI_UNAVAILABLE",
            error_context=None,
            partial_answer="부분",
            now=LATER,
        )
        await session.commit()
    await create_request(sessions, user_id, conversation_id=completed.conversation_id)
    queries = 0

    def count_evidence_queries(
        conn, cursor, statement, parameters, context, executemany
    ) -> None:
        """근거 테이블 SELECT 실행 횟수만 세고 쿼리 내용은 기록하지 않는다."""
        nonlocal queries
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "coaching_evidence" in statement
        ):
            queries += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_evidence_queries)
    try:
        async with sessions() as session:
            page = await repository.find_message_page(
                session,
                user_id=user_id,
                conversation_id=completed.conversation_id,
                limit=50,
                before_sequence_no=None,
            )
            assert not session.new and not session.dirty and not session.deleted
        assert page is not None
        assert queries == 1
        assert [item.message.sequence_no for item in page.messages] == [
            1,
            2,
            3,
            4,
            5,
            6,
        ]
        assert [item.request_status for item in page.messages] == [
            "completed",
            "completed",
            "failed",
            "failed",
            "generating",
            "generating",
        ]
        assert [item.evidence for item in page.messages] == [
            (),
            (synthetic_evidence_info(), synthetic_evidence_info(2)),
            (),
            (),
            (),
            (),
        ]
    finally:
        event.remove(
            engine.sync_engine, "before_cursor_execute", count_evidence_queries
        )


async def test_request_completed_before_feature_returns_empty_evidence(
    committed_members,
) -> None:
    """E16 근거 행 없이 완료한 기존 요청 → 요청·메시지 조회 근거가 모두 비어 있다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    async with sessions() as session:
        assert await repository.complete(
            session, request_id=request.id, answer="기존 합성 답변", now=LATER
        )
        await session.commit()
    state, page = await read_result(sessions, user_id, request.id)
    assert state.status == "completed"
    assert state.evidence == ()
    assert all(item.evidence == () for item in page.messages)
    assert request_state_data(state).model_dump(mode="json")["evidence"] == []
    assert all(
        item["evidence"] == []
        for item in message_page_data(page).model_dump(mode="json")["items"]
    )
    assert await evidence_count(sessions, request.id) == 0


async def test_legacy_snapshot_requests_and_messages_preserve_stored_dict(
    committed_members,
    app,
    client,
    settings,
    monkeypatch,
) -> None:
    """E18 현재 원형과 다른 옛 snapshot → 저장소·6.3·6.4 모두 재검증 없이 그대로 반환한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    snapshot = deepcopy(SYNTHETIC_SNAPSHOT)
    del snapshot["detail"]
    snapshot["legacy_field"] = {"snake_case": "합성 옛 값"}
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 답변",
            now=LATER,
            evidence=(synthetic_evidence_info(),),
        )
        await session.commit()
    async with sessions() as session:
        await session.execute(
            update(CoachingEvidence)
            .where(
                CoachingEvidence.assistant_message_id == request.assistant_message.id
            )
            .values(snapshot=snapshot)
        )
        await session.commit()
    state, page = await read_result(sessions, user_id, request.id)
    assert state.evidence[0].snapshot == snapshot
    assert page.messages[1].evidence[0].snapshot == snapshot
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        """각 HTTP 조회에 새 읽기 세션을 제공하고 쓰기 호출·ORM 변경이 없음을 확인한다."""
        async with sessions() as session:
            commit = session.commit = AsyncMock(wraps=session.commit)
            rollback = session.rollback = AsyncMock(wraps=session.rollback)
            flush = session.flush = AsyncMock(wraps=session.flush)
            yield session
            commit.assert_not_called()
            rollback.assert_not_called()
            flush.assert_not_called()
            assert not session.new and not session.dirty and not session.deleted

    app.dependency_overrides[get_session] = session_dependency
    headers = {
        "Authorization": "Bearer "
        + tokens.issue_access_token(user_id, settings, now=NOW).token
    }
    try:
        status = await client.get(
            f"/api/v1/coaching/requests/{request.id}", headers=headers
        )
        messages = await client.get(
            f"/api/v1/coaching/conversations/{request.conversation_id}/messages",
            headers=headers,
        )
        assert status.status_code == messages.status_code == 200
        assert status.json()["data"]["evidence"][0]["snapshot"] == snapshot
        assert (
            messages.json()["data"]["items"][1]["evidence"][0]["snapshot"] == snapshot
        )
    finally:
        app.dependency_overrides.pop(get_session)


async def test_complete_racing_interrupt_commits_one_consistent_outcome(
    committed_members,
) -> None:
    """O1 두 세션의 완료·중단 경쟁 → 완료와 근거 또는 실패와 빈 근거 중 하나만 남는다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    ready = 0
    start = asyncio.Event()

    async def arrive() -> None:
        """두 트랜잭션이 모두 준비된 뒤 동시에 갱신을 시작한다."""
        nonlocal ready
        ready += 1
        if ready == 2:
            start.set()
        await start.wait()

    async def finish_request() -> bool:
        """독립 세션에서 최종 답변·근거를 완료하고 commit한다."""
        async with sessions() as session:
            await arrive()
            changed = await repository.complete(
                session,
                request_id=request.id,
                answer="합성 경쟁 답변",
                now=LATER,
                evidence=(synthetic_evidence_info(), synthetic_evidence_info(2)),
            )
            await session.commit()
            return changed

    async def interrupt_request() -> None:
        """다른 독립 세션에서 기동 중단 정리를 commit한다."""
        async with sessions() as session:
            await arrive()
            await repository.interrupt_in_progress(
                session, now=LATER, created_before=NOW + timedelta(seconds=1)
            )
            await session.commit()

    async with asyncio.timeout(5):
        completed, _ = await asyncio.gather(finish_request(), interrupt_request())
    state, _ = await read_result(sessions, user_id, request.id)
    count = await evidence_count(sessions, request.id)
    assert (state.status, count) in {("completed", 2), ("failed", 0)}
    assert completed is (state.status == "completed")
    if completed:
        assert state.evidence == (synthetic_evidence_info(), synthetic_evidence_info(2))
        assert state.assistant_message.content == "합성 경쟁 답변"
        assert state.error_code is None
    else:
        assert state.evidence == ()
        assert state.assistant_message.content == ""
        assert state.error_code == "GENERATION_INTERRUPTED"


async def test_evidence_constraints_invalid_rows_raise_integrity_error(
    committed_members,
) -> None:
    """O3 FK·역할·순번·지표·기간·객체 CHECK 위반 → 각각 savepoint에서 제약 오류가 난다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    base = {
        "assistant_message_id": request.assistant_message.id,
        "assistant_role": "assistant",
        "position": 1,
        "metric": "steps",
        "period_start": date(2026, 10, 12),
        "period_end": date(2026, 10, 18),
        "snapshot": deepcopy(SYNTHETIC_SNAPSHOT),
        "created_at": LATER,
    }
    invalid_values = [
        {"assistant_message_id": request.user_message.id},
        {"assistant_message_id": request.user_message.id, "assistant_role": "user"},
        {"position": 0},
        {"metric": "unknown"},
        {"period_start": date(2026, 10, 19)},
        {"snapshot": []},
    ]
    async with sessions() as session:
        for invalid in invalid_values:
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        insert(CoachingEvidence).values(
                            **{**base, "id": uuid4(), **invalid}
                        )
                    )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CoachingEvidence)
                .where(
                    CoachingEvidence.assistant_message_id.in_(
                        [request.user_message.id, request.assistant_message.id]
                    )
                )
            )
            == 0
        )


@pytest.mark.parametrize(
    "metric",
    [
        "steps",
        "heart_rates",
        "oxygen_saturations",
        "sleeps",
        "exercises",
        "weights",
        "body_fats",
        "basal_metabolic_rates",
        "heights",
    ],
)
async def test_evidence_metric_constraint_public_values_are_accepted(
    committed_members, metric
) -> None:
    """지표 아홉 종류 → DB CHECK가 각각 허용하고 저장 지표를 보존한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    item = replace(synthetic_evidence_info(), metric=metric)
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="합성 답변",
            now=LATER,
            evidence=(item,),
        )
        await session.commit()
    state, _ = await read_result(sessions, user_id, request.id)
    assert state.evidence[0].metric == metric


async def test_complete_duplicate_position_raises_before_commit(
    committed_members,
) -> None:
    """한 답변의 중복 순번 → complete에서 제약 오류가 나고 rollback 후 근거는 없다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    async with sessions() as session:
        with pytest.raises(IntegrityError):
            await repository.complete(
                session,
                request_id=request.id,
                answer="합성 답변",
                now=LATER,
                evidence=(
                    synthetic_evidence_info(),
                    replace(synthetic_evidence_info(2), position=1),
                ),
            )
        await session.rollback()
    state, _ = await read_result(sessions, user_id, request.id)
    assert state.status == "generating"
    assert state.assistant_message.content == ""
    assert await evidence_count(sessions, request.id) == 0


async def test_complete_already_completed_keeps_first_evidence(
    committed_members,
) -> None:
    """이미 완료된 요청에 다른 근거로 재완료 → False이며 최초 본문·근거를 유지한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    request = await create_request(sessions, user_id)
    async with sessions() as session:
        assert await repository.complete(
            session,
            request_id=request.id,
            answer="첫 답변",
            now=LATER,
            evidence=(synthetic_evidence_info(),),
        )
        await session.commit()
    async with sessions() as session:
        assert not await repository.complete(
            session,
            request_id=request.id,
            answer="늦은 답변",
            now=LATER,
            evidence=(synthetic_evidence_info(2),),
        )
        await session.commit()
    state, _ = await read_result(sessions, user_id, request.id)
    assert state.assistant_message.content == "첫 답변"
    assert state.evidence == (synthetic_evidence_info(),)
    assert await evidence_count(sessions, request.id) == 1
