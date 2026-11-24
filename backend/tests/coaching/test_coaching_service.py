"""DB·LLM 없이 질문 접수의 트랜잭션·첫 이벤트·작업 제출을 검사한다."""

import asyncio
import logging
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.auth.contract import AuthenticatedUser
from app.coaching.schemas import Question, parse_question, request_state_data
from app.coaching.service import CoachingQuestionHandler
from tests.coaching.fakes import (
    NOW,
    FakeSessionFactory,
    FakeStore,
    RecordingChannel,
    RecordingRunner,
    fixed_now,
)

USER = AuthenticatedUser(uuid4())
CONTENT = "합성 접수 질문 비공개 문자열"


@pytest.fixture
def question() -> Question:
    """공백이 있는 합성 프레임 → 정규화된 질문을 제공한다."""
    return parse_question(
        {
            "type": "question",
            "clientRequestId": str(uuid4()),
            "conversationId": None,
            "content": f"  {CONTENT}  ",
        }
    )


def assert_error(
    channel: RecordingChannel,
    question: Question,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    """접수 전 오류 → 질문 ID·공개 리터럴·빈 서버 ID·순번 0을 확인한다."""
    assert channel.events == [
        {
            "type": "error",
            "clientRequestId": str(question.client_request_id),
            "requestId": None,
            "conversationId": None,
            "eventSeq": 0,
            "data": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "accepted": False,
            },
        }
    ]


async def test_accepted_commits_then_sends_before_submit(question: Question) -> None:
    """정상 접수 → commit·accepted·submit 순서와 작업 인자를 보존한다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    order = []
    ticks = iter((10.0, 10.25))

    def on_commit() -> None:
        """commit 시점 → 아직 accepted와 작업이 없음을 기록한다."""
        assert channel.events == []
        assert runner.jobs == []
        order.append("commit")

    def on_submit() -> None:
        """submit 시점 → commit 뒤 accepted가 먼저 들어왔음을 기록한다."""
        assert factory.commits == 1
        assert [event["type"] for event in channel.events] == ["accepted"]
        order.extend(("accepted", "submit"))

    runner = RecordingRunner(on_submit=on_submit)
    factory.on_commit = on_commit
    handler = CoachingQuestionHandler(
        session_factory=factory,
        runner=runner,
        store=store,
        clock=fixed_now,
        monotonic=lambda: next(ticks),
    )
    await handler.handle(USER, question, channel)

    assert (factory.commits, factory.rollbacks, factory.active) == (1, 0, 0)
    assert order == ["commit", "accepted", "submit"]
    assert len(runner.jobs) == 1
    job = runner.jobs[0]
    assert job.user_id == USER.user_id
    assert job.request == store.state(job.request.id)
    assert job.question == CONTENT == question.content
    assert job.channel is channel
    assert (job.started_at, job.accepted_at) == (10.0, 10.25)
    assert job.request.created_at == NOW
    event = channel.events[0]
    assert event["eventSeq"] == 1
    assert event["clientRequestId"] == str(question.client_request_id)
    assert event["requestId"] == str(job.request.id)
    assert store.calls[0][1]["content"] == CONTENT
    assert store.calls[0][1]["now"] == NOW


@pytest.mark.parametrize("completed", [False, True])
async def test_duplicate_sends_snapshot_without_commit_or_submit(
    question: Question, completed: bool
) -> None:
    """진행 중·완료 요청 재전송 → rollback·snapshot만 수행한다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    runner = RecordingRunner()
    state = store.seed_accepted(
        user_id=USER.user_id,
        client_request_id=question.client_request_id,
        content=question.content,
    )
    if completed:
        async with factory() as session:
            await store.complete(
                session, request_id=state.id, answer="합성 답변", now=NOW
            )
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store, clock=fixed_now
    )
    await handler.handle(USER, question, channel)

    assert (factory.commits, factory.rollbacks) == (0, 1)
    assert runner.jobs == []
    assert len(channel.events) == 1
    event = channel.events[0]
    assert event["type"] == "request_snapshot"
    assert event["eventSeq"] == 0
    assert event["clientRequestId"] == str(question.client_request_id)
    assert event["data"] == request_state_data(store.state(state.id)).model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    ("case", "code", "message", "retryable"),
    [
        ("conflict", "REQUEST_ID_CONFLICT", "이미 사용한 전송 ID입니다.", False),
        ("busy", "CONVERSATION_BUSY", "답변이 생성 중입니다.", True),
        ("missing", "CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다.", False),
        ("other_owner", "CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다.", False),
    ],
)
async def test_rejection_rolls_back_and_sends_public_error(
    question: Question, case: str, code: str, message: str, retryable: bool
) -> None:
    """ID 충돌·진행 중·없는 대화·남의 대화 → rollback하고 명세 오류를 보낸다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    runner = RecordingRunner()
    state = store.seed_accepted(
        user_id=uuid4() if case == "other_owner" else USER.user_id,
        client_request_id=question.client_request_id,
        content=question.content,
    )
    if case == "conflict":
        question = replace(question, content="다른 합성 질문")
    else:
        question = replace(
            question,
            client_request_id=uuid4(),
            conversation_id=uuid4() if case == "missing" else state.conversation_id,
        )
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store, clock=fixed_now
    )
    await handler.handle(USER, question, channel)

    assert_error(channel, question, code, message, retryable)
    assert (factory.rollbacks, factory.commits) == (1, 0)
    assert runner.jobs == []
    assert len(store.requests) == 1
    assert store.state(state.id) == state


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        ("accept", "ConnectionError"),
        ("commit", "OperationalError"),
        ("timeout", "TimeoutError"),
    ],
)
async def test_storage_failure_does_not_submit_or_log_question(
    question: Question,
    failure: str,
    error_type: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """접수 쿼리·commit·시간 초과 실패 → PERSISTENCE_FAILED와 안전한 로그만 남긴다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    runner = RecordingRunner()
    if failure == "accept":
        store.errors["accept"] = ConnectionError(CONTENT)
    elif failure == "commit":
        factory.commit_errors.append(OperationalError(CONTENT, {}, Exception(CONTENT)))
    else:

        async def slow_accept(*args: Any, **kwargs: Any) -> None:
            """지연 접수 → 처리기의 짧은 시간 제한으로 취소되게 한다."""
            await asyncio.sleep(1)

        monkeypatch.setattr(store, "accept", AsyncMock(side_effect=slow_accept))
    handler = CoachingQuestionHandler(
        session_factory=factory,
        runner=runner,
        store=store,
        clock=fixed_now,
        accept_timeout=0.01,
    )
    with caplog.at_level(logging.DEBUG):
        await handler.handle(USER, question, channel)

    assert_error(
        channel, question, "PERSISTENCE_FAILED", "요청을 처리하지 못했습니다.", True
    )
    assert runner.jobs == []
    assert factory.rollbacks == 1
    assert factory.active == 0
    assert error_type in caplog.text
    assert CONTENT not in caplog.text
    records = [r for r in caplog.records if r.name == "app.coaching.service"]
    assert len(records) == 1
    assert (
        records[0].getMessage().startswith(f"coaching accept failed: {error_type} at ")
    )
    assert records[0].exc_info is None


async def test_accept_close_timeout_after_commit_still_accepts(
    question: Question,
) -> None:
    """S1 commit 뒤 세션 닫기 시간 초과 → accepted·submit 1회, PERSISTENCE_FAILED 없음."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    factory.exit_gate = asyncio.Event()
    runner = RecordingRunner()
    handler = CoachingQuestionHandler(
        session_factory=factory,
        runner=runner,
        store=store,
        clock=fixed_now,
        accept_timeout=0.01,
    )
    await handler.handle(USER, question, channel)

    assert factory.commits == 1
    assert [event["type"] for event in channel.events] == ["accepted"]
    assert channel.events[0]["eventSeq"] == 1
    assert len(runner.jobs) == 1
    assert factory.active == 0


async def test_accept_close_error_after_commit_still_accepts_and_logs_once(
    question: Question, caplog: pytest.LogCaptureFixture
) -> None:
    """S2 commit 뒤 세션 닫기 ConnectionError → accepted·submit 1회, 로그 1줄, 질문 내용 없음."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    factory.exit_errors.append(ConnectionError(CONTENT))
    runner = RecordingRunner()
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store, clock=fixed_now
    )
    with caplog.at_level(logging.DEBUG):
        await handler.handle(USER, question, channel)

    assert factory.commits == 1
    assert [event["type"] for event in channel.events] == ["accepted"]
    assert len(runner.jobs) == 1
    records = [r for r in caplog.records if r.name == "app.coaching.service"]
    assert len(records) == 1
    assert (
        records[0]
        .getMessage()
        .startswith("coaching accept close failed: ConnectionError at ")
    )
    assert CONTENT not in caplog.text


async def test_busy_rollback_then_close_error_reports_persistence_failed(
    question: Question,
) -> None:
    """S4 Busy 결과 rollback 뒤 세션 닫기 ConnectionError → PERSISTENCE_FAILED, submit 0회."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    first = store.seed_accepted(user_id=USER.user_id)
    factory.exit_errors.append(ConnectionError(CONTENT))
    runner = RecordingRunner()
    busy_question = parse_question(
        {
            "type": "question",
            "clientRequestId": str(uuid4()),
            "conversationId": str(first.conversation_id),
            "content": CONTENT,
        }
    )
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store, clock=fixed_now
    )
    await handler.handle(USER, busy_question, channel)

    assert factory.rollbacks == 1
    assert runner.jobs == []
    assert [event["data"]["code"] for event in channel.events] == ["PERSISTENCE_FAILED"]
    assert channel.events[0]["data"]["accepted"] is False


async def test_not_ready_never_opens_session(question: Question) -> None:
    """실행 준비 불가 → 세션·접수 없이 INTERNAL_ERROR를 보낸다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    runner = RecordingRunner(ready=False)
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store
    )
    await handler.handle(USER, question, channel)

    assert_error(
        channel, question, "INTERNAL_ERROR", "요청을 처리하지 못했습니다.", True
    )
    assert factory.opened == 0
    assert store.calls == []
    assert runner.jobs == []


async def test_unexpected_accept_error_propagates_to_router(question: Question) -> None:
    """접수 중 ValueError → rollback 뒤 라우터 경계로 예외를 전파한다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    runner = RecordingRunner()
    error = ValueError(CONTENT)
    store.errors["accept"] = error
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store
    )
    with pytest.raises(ValueError) as caught:
        await handler.handle(USER, question, channel)

    assert caught.value is error
    assert factory.rollbacks == 1
    assert factory.active == 0
    assert factory.commits == 0
    assert channel.events == []
    assert runner.jobs == []


async def test_closed_channel_still_submits_committed_request(
    question: Question,
) -> None:
    """채널이 이미 닫힌 정상 접수 → commit한 요청을 반드시 실행기에 넘긴다."""
    factory, store, channel = FakeSessionFactory(), FakeStore(), RecordingChannel()
    runner = RecordingRunner()
    channel.close()
    handler = CoachingQuestionHandler(
        session_factory=factory, runner=runner, store=store, clock=fixed_now
    )
    await handler.handle(USER, question, channel)

    assert factory.commits == 1
    assert channel.events == []
    assert len(runner.jobs) == 1
    assert runner.jobs[0].request.client_request_id == question.client_request_id
