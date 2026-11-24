"""코칭 대화 REST·WebSocket(담당 개발팀), 명세는 backend/local/docs/api-spec.md."""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.auth.dependencies import get_current_user
from app.auth.tokens import authenticate_token
from app.coaching import service
from app.coaching.connection import EventChannel, QuestionHandler, log_failure
from app.coaching.schemas import (
    PUBLIC_ERRORS,
    UUID_PATTERN,
    ConversationPageData,
    ConversationPageQuery,
    FrameError,
    MessagePageData,
    MessagePageQuery,
    RequestStateData,
    connection_error_event,
    decode_frame,
    parse_auth,
    parse_question,
    pre_accept_error,
    read_client_request_id,
    ready_event,
)
from app.coaching.ws_auth import (
    WebSessionUnavailableError,
    authenticate_web_cookie,
    has_web_cookie,
    is_web_origin_allowed,
)
from app.core.config import Settings
from app.core.db import get_session
from app.core.errors import ApiError
from app.core.routing import ApiRoute
from app.core.schemas import DataResponse

router = APIRouter(prefix="/api/v1/coaching", tags=["coaching"], route_class=ApiRoute)

AUTH_TIMEOUT_SECONDS = 5.0
CLOSE_FLUSH_SECONDS = 1.0
CLOSE_POLICY_VIOLATION = 1008
CLOSE_AUTH_UNAVAILABLE = 1011
logger = logging.getLogger(__name__)


async def _close_auth(websocket: WebSocket, code: str) -> None:
    """connection_error(AUTH_REQUIRED 또는 AUTH_EXPIRED)를 보내고 close 1008로 닫는다."""
    public_code = "AUTH_EXPIRED" if code == "AUTH_EXPIRED" else "AUTH_REQUIRED"
    await websocket.send_json(connection_error_event(public_code))
    await websocket.close(code=1008)


async def _close_unavailable(websocket: WebSocket) -> None:
    """connection_error(AUTH_UNAVAILABLE)를 보내고 close 1011로 닫는다."""
    await websocket.send_json(connection_error_event("AUTH_UNAVAILABLE"))
    await websocket.close(code=CLOSE_AUTH_UNAVAILABLE)


@router.websocket("/ws")
async def coaching_ws(websocket: WebSocket) -> None:
    """코칭 WS 연결을 처리한다.

    쿠키 있는 연결은 웹 세션 쿠키로 인증해 바로 ready를 보내고 질문마다 세션을 다시 확인한다.
    쿠키 없는 연결은 첫 auth 프레임의 액세스 토큰으로 인증한다.
    """
    settings: Settings = websocket.app.state.settings
    web = has_web_cookie(websocket)
    if web:
        if not is_web_origin_allowed(websocket, settings):
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
    else:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in settings.cors_allowed_origins:
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return

    await websocket.accept()
    channel: EventChannel | None = None
    try:
        token = ""
        if web:
            try:
                user = await authenticate_web_cookie(websocket)
            except AuthenticationError:
                await _close_auth(websocket, "AUTH_REQUIRED")
                return
            except WebSessionUnavailableError:
                await _close_unavailable(websocket)
                return
            except Exception as error:
                log_failure(logger, "web auth", error)
                await _close_unavailable(websocket)
                return
        else:
            try:
                async with asyncio.timeout(AUTH_TIMEOUT_SECONDS):
                    message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") is None:
                    raise FrameError("INVALID_MESSAGE")
                auth = parse_auth(decode_frame(message["text"]))
            except (FrameError, TimeoutError):
                await _close_auth(websocket, "AUTH_REQUIRED")
                return

            token = auth.access_token
            try:
                user = authenticate_token(token, settings)
            except AuthenticationError as error:
                await _close_auth(websocket, error.code)
                return

        await websocket.send_json(ready_event())
        channel = EventChannel(websocket)
        channel.start()
        while True:
            message = await websocket.receive()
            if not channel.is_open:
                break
            if message["type"] == "websocket.disconnect":
                break
            try:
                if message.get("text") is None:
                    raise FrameError("INVALID_MESSAGE")
                obj = decode_frame(message["text"])
                if obj.get("type") != "question":
                    raise FrameError("INVALID_MESSAGE", read_client_request_id(obj))
                question = parse_question(obj)
            except FrameError as error:
                channel.send(pre_accept_error(error.code, error.client_request_id))
                continue

            if web:
                try:
                    user = await authenticate_web_cookie(websocket)
                except AuthenticationError:
                    channel.send(
                        pre_accept_error("AUTH_REQUIRED", question.client_request_id)
                    )
                    continue
                except WebSessionUnavailableError:
                    channel.send(
                        pre_accept_error("AUTH_UNAVAILABLE", question.client_request_id)
                    )
                    continue
                except Exception as error:
                    log_failure(logger, "web auth", error)
                    channel.send(
                        pre_accept_error("INTERNAL_ERROR", question.client_request_id)
                    )
                    continue
            else:
                try:
                    authenticate_token(token, settings)
                except AuthenticationError as error:
                    await channel.flush(CLOSE_FLUSH_SECONDS)
                    await channel.aclose()
                    await _close_auth(websocket, error.code)
                    return

            handler: QuestionHandler | None = getattr(
                websocket.app.state, "coaching_question_handler", None
            )
            if handler is None:
                channel.send(
                    pre_accept_error("INTERNAL_ERROR", question.client_request_id)
                )
                continue
            if not channel.is_open:
                break
            try:
                await handler.handle(user, question, channel)
            except Exception as error:
                # 접수 처리기 경계에서 공개 오류로 바꾸고 연결을 유지한다.
                log_failure(logger, "handler", error)
                channel.send(
                    pre_accept_error("INTERNAL_ERROR", question.client_request_id)
                )
    except WebSocketDisconnect:
        return
    finally:
        if channel is not None:
            await channel.aclose()


@router.get("/requests/{requestId}", response_model=DataResponse[RequestStateData])
async def read_request_status(
    request_id: Annotated[str, Path(alias="requestId", pattern=UUID_PATTERN)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[RequestStateData]:
    """인증한 회원의 요청 상태를 조회하고 없거나 다른 회원의 요청은 404로 반환한다."""
    try:
        data = await service.get_request_status(
            db_session, user_id=user.user_id, request_id=UUID(request_id)
        )
    except service.RequestNotFoundError:
        raise ApiError(
            404, "REQUEST_NOT_FOUND", PUBLIC_ERRORS["REQUEST_NOT_FOUND"].message
        ) from None
    return DataResponse(data=data)


@router.get(
    "/conversations/{conversationId}/messages",
    response_model=DataResponse[MessagePageData],
)
async def read_conversation_messages(
    conversation_id: Annotated[str, Path(alias="conversationId", pattern=UUID_PATTERN)],
    query: Annotated[MessagePageQuery, Query()],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[MessagePageData]:
    """토큰 회원의 대화 메시지를 조회하고 없거나 다른 회원의 대화면 같은 404를 반환한다."""
    try:
        data = await service.get_conversation_messages(
            db_session,
            user_id=user.user_id,
            conversation_id=UUID(conversation_id),
            limit=query.limit,
            before_sequence_no=query.before_sequence_no,
        )
    except service.ConversationNotFoundError:
        raise ApiError(
            404,
            "CONVERSATION_NOT_FOUND",
            PUBLIC_ERRORS["CONVERSATION_NOT_FOUND"].message,
        ) from None
    return DataResponse(data=data)


@router.get("/conversations", response_model=DataResponse[ConversationPageData])
async def read_conversations(
    query: Annotated[ConversationPageQuery, Query()],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[ConversationPageData]:
    """토큰으로 인증한 회원의 대화 목록을 최근 활동순으로 반환한다."""
    data = await service.list_conversations(
        db_session, user_id=user.user_id, limit=query.limit, cursor=query.cursor
    )
    return DataResponse(data=data)
