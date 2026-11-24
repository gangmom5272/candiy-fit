"""전용 PostgreSQL에서 접수부터 생성·최종 저장까지 실제 commit으로 검사한다."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.contract import AuthenticatedUser
from app.coaching import repository
from app.coaching.agent import CoachAgent
from app.coaching.models import CoachingConversation, CoachingMessage, CoachingRequest
from app.coaching.runner import CoachingRunner
from app.coaching.schemas import Question, request_state_data
from app.coaching.service import CoachingQuestionHandler
from app.users.models import User
from coach_ai.agent.graph import CoachResult
from coach_ai.history.types import HistoryTurn
from tests.coaching.fakes import (
    NOW,
    CountingModelFactory,
    RecordingChannel,
    RecordingPortFactory,
    ScriptedRunCoach,
    fixed_now,
)

pytestmark = pytest.mark.db
LATER = NOW + timedelta(seconds=10)
ANSWER = "합성 DB 전체 답변"
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def database_url() -> URL:
    """환경변수에 명시된 asyncpg 전용 테스트 DB만 허용하고 없으면 실패한다."""
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
    """실제 건강데이터 없이 UUIDv4와 고정 시각으로 합성 회원을 만든다."""
    return User(
        id=uuid4(),
        social_type="GOOGLE",
        social_user_id=str(uuid4()),
        display_name="합성코칭회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def committed_members() -> AsyncIterator[tuple[AsyncEngine, tuple[UUID, UUID]]]:
    """합성 회원 두 명을 commit하고 종료 때 메시지·요청·대화·회원 순으로 삭제한다."""
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
                conversation_ids = select(CoachingConversation.id).where(
                    CoachingConversation.user_id.in_(user_ids)
                )
                await conn.execute(
                    delete(CoachingMessage).where(
                        CoachingMessage.conversation_id.in_(conversation_ids)
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


class TerminalChannel(RecordingChannel):
    """기존 채널 기록에 최종 이벤트 도착 알림을 추가한다."""

    def __init__(self) -> None:
        """열린 기록 채널과 현재 앱 루프에서 기다릴 종료 이벤트를 준비한다."""
        super().__init__()
        self.finished = asyncio.Event()

    def send(self, event: Mapping[str, Any]) -> None:
        """이벤트를 기록하고 completed·error 수신 시 대기 중인 테스트를 깨운다."""
        super().send(event)
        if event["type"] in {"completed", "error"}:
            self.finished.set()


class DatabaseFlow:
    """실제 저장소와 합성 에이전트를 사용하는 접수·생성 의존성을 보관한다."""

    def __init__(
        self,
        session_factory: SessionFactory,
        run: ScriptedRunCoach,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        """실제 세션 팩토리와 에이전트로 실행기·접수 처리기를 조립한다."""
        self.sessions = session_factory
        self.run = run
        self.agent = CoachAgent(
            model_factory=CountingModelFactory(),
            port_factory=RecordingPortFactory(),
            run=run,
        )
        self.runner = CoachingRunner(
            session_factory=session_factory, agent=self.agent, clock=clock
        )
        self.handler = CoachingQuestionHandler(
            session_factory=session_factory, runner=self.runner, clock=fixed_now
        )

    async def send(self, user_id: UUID, question: Question) -> TerminalChannel:
        """질문을 실제 접수 트랜잭션으로 처리하고 해당 연결의 기록을 반환한다."""
        channel = TerminalChannel()
        await self.handler.handle(AuthenticatedUser(user_id), question, channel)
        return channel

    async def finish(
        self, user_id: UUID, channel: TerminalChannel
    ) -> repository.RequestState:
        """최대 3초 동안 생성 종결을 기다리고 새 DB 세션으로 최종 상태를 읽는다."""
        async with asyncio.timeout(3):
            await channel.finished.wait()
        assert channel.events[0]["type"] == "accepted"
        assert channel.events[-1]["type"] in {"completed", "error"}
        async with self.sessions() as session:
            state = await repository.get_request_state(
                session,
                user_id=user_id,
                request_id=UUID(channel.events[0]["requestId"]),
            )
        assert state is not None
        return state


@asynccontextmanager
async def running_flow(
    engine: AsyncEngine,
    *,
    run: ScriptedRunCoach | None = None,
    session_factory: SessionFactory | None = None,
    clock: Callable[[], datetime] = lambda: LATER,
) -> AsyncIterator[DatabaseFlow]:
    """실제 commit하는 세션과 합성 AI를 조립하고 테스트 종료 전 작업을 정리한다."""
    sessions = (
        session_factory
        if session_factory is not None
        else async_sessionmaker(engine, expire_on_commit=False)
    )
    run = (
        run
        if run is not None
        else ScriptedRunCoach(
            [
                {"type": "status", "stage": "answering"},
                {"type": "token", "delta": ANSWER},
            ],
            result=CoachResult(answer=ANSWER, evidence=[], finish_reason="stop"),
        )
    )
    flow = DatabaseFlow(sessions, run, clock=clock)
    try:
        yield flow
    finally:
        try:
            await flow.runner.aclose()
        finally:
            await flow.agent.aclose()


def question(
    *, conversation_id: UUID | None = None, content: str = "합성 DB 질문"
) -> Question:
    """UUIDv4 전송 ID와 정규화된 합성 본문으로 질문을 만든다."""
    return Question(uuid4(), conversation_id, content)


async def storage_counts(
    sessions: SessionFactory, user_id: UUID
) -> tuple[int, int, int]:
    """새 세션에서 합성 회원의 대화·요청·메시지 행 수를 조회한다."""
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
            .join(CoachingRequest, CoachingMessage.request_id == CoachingRequest.id)
            .where(CoachingRequest.user_id == user_id)
        )
    return conversations, requests, messages


async def test_accept_complete_and_duplicate_match_committed_state(
    committed_members,
) -> None:
    """접수·완료·동일 전송 → DB 답변·완료 시각·대화 갱신과 snapshot이 일치한다."""
    engine, (user_id, _) = committed_members
    async with running_flow(engine) as flow:
        sent = question()
        channel = await flow.send(user_id, sent)
        state = await flow.finish(user_id, channel)
        assert state.status == "completed"
        assert state.assistant_message.content == ANSWER
        assert state.finished_at == LATER
        async with flow.sessions() as session:
            conversation = await session.get(
                CoachingConversation, state.conversation_id
            )
            assert conversation.created_at == NOW
            assert conversation.updated_at == LATER
            assert conversation.updated_at > conversation.created_at
        before = await storage_counts(flow.sessions, user_id)
        snapshot = await flow.send(user_id, sent)
        async with flow.sessions() as session:
            reread = await repository.get_request_state(
                session, user_id=user_id, request_id=state.id
            )
        assert reread is not None
        assert len(snapshot.events) == 1
        assert snapshot.events[0]["type"] == "request_snapshot"
        assert snapshot.events[0]["eventSeq"] == 0
        assert snapshot.events[0]["clientRequestId"] == str(sent.client_request_id)
        assert snapshot.events[0]["data"] == request_state_data(reread).model_dump(
            mode="json"
        )
        assert await storage_counts(flow.sessions, user_id) == before == (1, 1, 2)
        assert len(flow.run.calls) == 1


async def test_conflict_does_not_add_rows(committed_members) -> None:
    """같은 전송 ID·다른 본문 → REQUEST_ID_CONFLICT이며 기존 행을 보존한다."""
    engine, (user_id, _) = committed_members
    async with running_flow(engine) as flow:
        sent = question()
        accepted = await flow.send(user_id, sent)
        state = await flow.finish(user_id, accepted)
        before = await storage_counts(flow.sessions, user_id)
        conflict = await flow.send(
            user_id, Question(sent.client_request_id, None, "다른 합성 본문")
        )
        assert conflict.events[0]["data"]["code"] == "REQUEST_ID_CONFLICT"
        assert conflict.events[0]["data"]["accepted"] is False
        assert await storage_counts(flow.sessions, user_id) == before
        async with flow.sessions() as session:
            assert (
                await repository.get_request_state(
                    session, user_id=user_id, request_id=state.id
                )
                == state
            )
        assert len(flow.run.calls) == 1


async def test_busy_rolls_back_sequence_and_accepts_after_finish(
    committed_members,
) -> None:
    """생성 중 새 전송 → BUSY로 순번을 되돌리고 첫 생성 종료 후 접수한다."""
    engine, (user_id, _) = committed_members
    gate = asyncio.Event()
    run = ScriptedRunCoach(
        result=CoachResult(answer=ANSWER, evidence=[], finish_reason="stop"), hold=gate
    )
    async with running_flow(engine, run=run) as flow:
        first = await flow.send(user_id, question())
        conversation_id = UUID(first.events[0]["conversationId"])
        async with asyncio.timeout(3):
            await run.entered.wait()
        async with flow.sessions() as session:
            before = await session.get(CoachingConversation, conversation_id)
            next_sequence, updated_at = before.next_sequence, before.updated_at
        second_question = question(conversation_id=conversation_id)
        busy = await flow.send(user_id, second_question)
        assert busy.events[0]["data"]["code"] == "CONVERSATION_BUSY"
        async with flow.sessions() as session:
            after = await session.get(CoachingConversation, conversation_id)
            assert after.next_sequence == next_sequence == 3
            assert after.updated_at == updated_at
        assert await storage_counts(flow.sessions, user_id) == (1, 1, 2)
        assert len(run.calls) == 1
        gate.set()
        assert (await flow.finish(user_id, first)).status == "completed"
        second = await flow.send(user_id, second_question)
        assert second.events[0]["type"] == "accepted"
        assert second.events[0]["data"]["userSequenceNo"] == 3
        assert (await flow.finish(user_id, second)).status == "completed"


@pytest.mark.parametrize("missing", [False, True])
async def test_other_member_or_missing_conversation_is_hidden(
    committed_members, missing: bool
) -> None:
    """남의 대화·없는 대화 ID → CONVERSATION_NOT_FOUND이며 두 회원의 행 수가 같다."""
    engine, (owner_id, other_id) = committed_members
    async with running_flow(engine) as flow:
        owned = await flow.send(owner_id, question())
        state = await flow.finish(owner_id, owned)
        before = [
            await storage_counts(flow.sessions, uid) for uid in (owner_id, other_id)
        ]
        rejected = await flow.send(
            other_id,
            question(conversation_id=uuid4() if missing else state.conversation_id),
        )
        assert rejected.events[0]["data"]["code"] == "CONVERSATION_NOT_FOUND"
        assert rejected.events[0]["data"]["retryable"] is False
        assert rejected.events[0]["data"]["accepted"] is False
        assert [
            await storage_counts(flow.sessions, uid) for uid in (owner_id, other_id)
        ] == before
        assert len(flow.run.calls) == 1


async def test_failed_accept_commit_leaves_no_rows_and_same_id_can_retry(
    committed_members, monkeypatch: pytest.MonkeyPatch
) -> None:
    """첫 접수 commit 실패 → 빈 행 없이 PERSISTENCE_FAILED를 보내고 동일 ID 재접수가 성공한다."""
    engine, (user_id, _) = committed_members
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    failed = False

    @asynccontextmanager
    async def fail_first_commit() -> AsyncIterator[AsyncSession]:
        """첫 세션의 실제 commit 직전 오류를 주입하고 이후 세션은 정상 commit한다."""
        nonlocal failed
        async with sessions() as session:
            if not failed:
                failed = True
                with monkeypatch.context() as patch:
                    patch.setattr(
                        session,
                        "commit",
                        AsyncMock(
                            side_effect=OperationalError(
                                "synthetic commit", {}, Exception("synthetic failure")
                            )
                        ),
                    )
                    yield session
            else:
                yield session

    async with running_flow(engine, session_factory=fail_first_commit) as flow:
        sent = question()
        failure = await flow.send(user_id, sent)
        assert failure.events[0]["type"] == "error"
        assert failure.events[0]["data"]["code"] == "PERSISTENCE_FAILED"
        assert failure.events[0]["data"]["accepted"] is False
        assert failure.events[0]["eventSeq"] == 0
        assert failure.events[0]["clientRequestId"] == str(sent.client_request_id)
        assert await storage_counts(sessions, user_id) == (0, 0, 0)
        assert flow.run.calls == []
        retry = await flow.send(user_id, sent)
        assert retry.events[0]["type"] == "accepted"
        assert retry.events[0]["clientRequestId"] == str(sent.client_request_id)
        assert (await flow.finish(user_id, retry)).status == "completed"
        assert await storage_counts(sessions, user_id) == (1, 1, 2)
        assert len(flow.run.calls) == 1


async def test_generation_deadline_persists_partial_failure(committed_members) -> None:
    """접수 후 남은 기한 0.05초 초과 → 부분 답변과 GENERATION_TIMEOUT을 DB에 저장한다."""
    engine, (user_id, _) = committed_members
    partial = "합성 DB 기한 초과 부분 답변"
    run = ScriptedRunCoach([{"type": "token", "delta": partial}], forever=True)
    async with running_flow(
        engine, run=run, clock=lambda: NOW + timedelta(seconds=119.95)
    ) as flow:
        channel = await flow.send(user_id, question())
        state = await flow.finish(user_id, channel)
        assert state.status == "failed"
        assert state.error_code == "GENERATION_TIMEOUT"
        assert state.assistant_message.content == partial
        assert state.finished_at is not None
        assert channel.events[-1]["type"] == "error"
        assert channel.events[-1]["data"]["code"] == "GENERATION_TIMEOUT"
        assert channel.events[-1]["data"]["accepted"] is True
        assert run.cancelled is True


async def test_history_excludes_failure_and_includes_completed_pair(
    committed_members,
) -> None:
    """실패·성공·다음 질문 → 실패 이력은 제외하고 완료 질문·답변만 전달한다."""
    engine, (user_id, _) = committed_members
    run = ScriptedRunCoach(
        result=CoachResult(
            answer="합성 실패 부분 답변",
            evidence=[],
            finish_reason="error",
            error_code="llm_unavailable",
        )
    )
    async with running_flow(engine, run=run) as flow:
        first = await flow.send(user_id, question(content="실패할 합성 질문"))
        failed = await flow.finish(user_id, first)
        assert failed.status == "failed"
        assert failed.error_code == "AI_UNAVAILABLE"
        run.result = CoachResult(answer=ANSWER, evidence=[], finish_reason="stop")
        second_question = question(
            conversation_id=failed.conversation_id, content="성공할 합성 둘째 질문"
        )
        second = await flow.send(user_id, second_question)
        completed = await flow.finish(user_id, second)
        assert completed.status == "completed"
        assert run.calls[1]["ctx"].turns == ()
        assert run.calls[1]["ctx"].summary is None
        third = await flow.send(
            user_id,
            question(
                conversation_id=failed.conversation_id, content="성공할 합성 셋째 질문"
            ),
        )
        assert (await flow.finish(user_id, third)).status == "completed"
        assert run.calls[2]["ctx"].turns == (
            HistoryTurn(
                request_id=str(completed.id),
                question_message_id=str(completed.user_message.id),
                answer_message_id=str(completed.assistant_message.id),
                question_seq=completed.user_message.sequence_no,
                answer_seq=completed.assistant_message.sequence_no,
                question=second_question.content,
                answer=ANSWER,
                answered_on=NOW.date(),
            ),
        )
        assert "history" not in run.calls[2] and "summary" not in run.calls[2]
        assert len(run.calls) == 3
