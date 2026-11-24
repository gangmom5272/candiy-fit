"""질문 접수 트랜잭션과 첫 이벤트를 처리하고 생성 작업을 실행기에 넘긴다."""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.contract import AuthenticatedUser
from app.coaching import repository
from app.coaching.connection import EventChannel, log_failure
from app.coaching.repository import (
    Accepted,
    AcceptResult,
    Busy,
    Conflict,
    ConversationKey,
    ConversationNotFound,
    Duplicate,
)
from app.coaching.runner import CoachingRunner, GenerationJob, utc_now
from app.coaching.schemas import (
    ConversationPageData,
    MessagePageData,
    Question,
    RequestStateData,
    accepted_event,
    conversation_page_data,
    message_page_data,
    pre_accept_error,
    request_snapshot_event,
    request_state_data,
)
from app.core.storage import STORAGE_ERRORS

ACCEPT_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)


class AcceptStore(Protocol):
    """호출자가 트랜잭션을 소유하는 질문 접수 저장소 계약을 정의한다."""

    async def accept(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        client_request_id: UUID,
        conversation_id: UUID | None,
        content: str,
        now: datetime,
    ) -> AcceptResult:
        """질문을 접수하거나 중복·거절 결과를 반환하며 commit하지 않는다."""
        ...


class CoachingQuestionHandler:
    """WS 질문을 접수하고 결과에 맞는 첫 이벤트를 보낸다(QuestionHandler 구현).

    접수 트랜잭션은 이 처리기가 commit한다. 접수되면 생성 작업을 넘기고 반환한다.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        runner: CoachingRunner,
        store: AcceptStore = repository,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        accept_timeout: float = ACCEPT_TIMEOUT_SECONDS,
    ) -> None:
        """접수용 세션·저장소·실행기와 시각·시간 제한을 보관한다."""
        self._session_factory = session_factory
        self._runner = runner
        self._store = store
        self._clock = clock
        self._monotonic = monotonic
        self._accept_timeout = accept_timeout

    async def handle(
        self, user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None:
        """질문을 접수하고 accepted·request_snapshot·접수 전 error 중 하나를 보낸다.

        저장소 오류·시간 초과는 PERSISTENCE_FAILED로 보내며 다른 예외는 전파한다.
        commit 뒤 세션 정리 오류·시간 초과는 접수로 처리한다.
        """
        started_at = self._monotonic()
        if not self._runner.is_ready():
            channel.send(pre_accept_error("INTERNAL_ERROR", question.client_request_id))
            return

        committed = False
        try:
            async with asyncio.timeout(self._accept_timeout):
                async with self._session_factory() as session:
                    result = await self._store.accept(
                        session,
                        user_id=user.user_id,
                        client_request_id=question.client_request_id,
                        conversation_id=question.conversation_id,
                        content=question.content,
                        now=self._clock(),
                    )
                    if isinstance(result, Accepted):
                        await session.commit()
                        committed = True
                    else:
                        await session.rollback()
        except STORAGE_ERRORS as error:
            if not committed:
                log_failure(logger, "accept", error)
                channel.send(
                    pre_accept_error("PERSISTENCE_FAILED", question.client_request_id)
                )
                return
            # commit이 끝났으므로 세션 정리 오류는 기록만 하고 접수로 이어 간다.
            log_failure(logger, "accept close", error)

        if isinstance(result, Accepted):
            channel.send(accepted_event(result))
            self._runner.submit(
                GenerationJob(
                    user_id=user.user_id,
                    request=result.request,
                    question=question.content,
                    channel=channel,
                    started_at=started_at,
                    accepted_at=self._monotonic(),
                )
            )
        elif isinstance(result, Duplicate):
            channel.send(request_snapshot_event(result.request))
        elif isinstance(result, Conflict):
            channel.send(
                pre_accept_error("REQUEST_ID_CONFLICT", question.client_request_id)
            )
        elif isinstance(result, Busy):
            channel.send(
                pre_accept_error("CONVERSATION_BUSY", question.client_request_id)
            )
        elif isinstance(result, ConversationNotFound):
            channel.send(
                pre_accept_error("CONVERSATION_NOT_FOUND", question.client_request_id)
            )


class RequestNotFoundError(Exception):
    """요청이 없거나 다른 회원의 것임을 나타내며 둘을 구분하지 않는다."""


async def get_request_status(
    db_session: AsyncSession, *, user_id: UUID, request_id: UUID
) -> RequestStateData:
    """본인 요청의 저장 상태를 명세 §6.4 data로 돌려준다.

    없거나 다른 회원의 요청이면 RequestNotFoundError를 던진다.
    읽기만 하며 commit·rollback하지 않고 DB 예외는 그대로 전파한다.
    """
    state = await repository.get_request_state(
        db_session, user_id=user_id, request_id=request_id
    )
    if state is None:
        raise RequestNotFoundError
    return request_state_data(state)


class ConversationNotFoundError(Exception):
    """대화가 없거나 다른 회원의 것임을 나타내며 둘을 구분하지 않는다."""


async def get_conversation_messages(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    limit: int,
    before_sequence_no: int | None,
) -> MessagePageData:
    """본인 대화의 메시지 페이지를 읽고 없으면 ConversationNotFoundError를 던진다.

    commit·rollback하지 않으며 DB 예외는 공통 HTTP 경계로 전파한다.
    """
    page = await repository.find_message_page(
        db_session,
        user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
        before_sequence_no=before_sequence_no,
    )
    if page is None:
        raise ConversationNotFoundError
    return message_page_data(page)


async def list_conversations(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    limit: int,
    cursor: ConversationKey | None,
) -> ConversationPageData:
    """본인 대화 목록을 읽어 UTC 시각과 다음 페이지 커서가 있는 응답으로 바꾼다.

    commit·rollback하지 않으며 DB 예외는 공통 HTTP 경계로 전파한다.
    """
    page = await repository.list_conversations(
        db_session, user_id=user_id, limit=limit, after=cursor
    )
    return conversation_page_data(page)
