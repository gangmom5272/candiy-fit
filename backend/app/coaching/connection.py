"""연결별 이벤트 송신과 질문 접수 처리기 계약."""

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Literal, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from app.auth.contract import AuthenticatedUser
from app.coaching.schemas import Question

MAX_PENDING_EVENTS = 1000
CLOSE_SEND_FAILED = 1011  # 송신 불가 시 종료 코드(API 명세 v3.1 §7.5).
CLOSE_WAIT_SECONDS = 1.0
logger = logging.getLogger(__name__)


def log_failure(log: logging.Logger, operation: str, error: Exception) -> None:
    traceback = error.__traceback__
    location = "unknown:0"
    if traceback is not None:
        while traceback.tb_next is not None:
            traceback = traceback.tb_next
        filename = os.path.basename(traceback.tb_frame.f_code.co_filename)
        location = f"{filename}:{traceback.tb_lineno}"
    log.warning(
        "coaching %s failed: %s at %s", operation, type(error).__name__, location
    )


class EventChannel:
    """연결 하나의 송신 큐. send는 막히지 않고 예외를 던지지 않는다."""

    def __init__(self, websocket: WebSocket, *, max_pending: int | None = None) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue(
            maxsize=MAX_PENDING_EVENTS if max_pending is None else max_pending
        )
        self._is_open = True
        self._failure: Literal["overflow", "send_failed"] | None = None
        self._task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def failure(self) -> Literal["overflow", "send_failed"] | None:
        return self._failure

    def start(self) -> None:
        if self._is_open and self._task is None:
            self._task = asyncio.create_task(self._send_events())

    def send(self, event: Mapping[str, Any]) -> None:
        if not self._is_open:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._fail("overflow")
            if self._task is not None:
                self._task.cancel()

    def _fail(self, reason: Literal["overflow", "send_failed"]) -> None:
        if not self._is_open:
            return
        self._failure = reason
        self._is_open = False
        self._discard_pending()
        logger.warning("coaching channel closed: %s", reason)
        self._close_task = asyncio.create_task(self._close_send_failed())

    async def _close_send_failed(self) -> None:
        with suppress(WebSocketDisconnect, RuntimeError, OSError):
            await self._websocket.close(code=CLOSE_SEND_FAILED)

    async def flush(self, timeout: float) -> None:  # noqa: ASYNC109 - 계약상 큐 대기 기한을 받는다.
        with suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                await self._queue.join()

    async def aclose(self) -> None:
        self._is_open = False
        try:
            if self._task is not None:
                self._task.cancel()
                # 송신 태스크의 취소만 수거하고 aclose 호출자의 취소는 전파한다.
                await asyncio.gather(self._task, return_exceptions=True)
            if self._close_task is not None and not self._close_task.done():
                await asyncio.wait({self._close_task}, timeout=CLOSE_WAIT_SECONDS)
        finally:
            self._discard_pending()
            if self._close_task is not None and not self._close_task.done():
                # 기한 초과 또는 호출자 취소일 때만 남은 close를 취소한다.
                self._close_task.cancel()
                await asyncio.gather(self._close_task, return_exceptions=True)

    def _discard_pending(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    async def _send_events(self) -> None:
        try:
            while self._is_open:
                event = await self._queue.get()
                try:
                    await self._websocket.send_text(
                        json.dumps(event, ensure_ascii=False)
                    )
                finally:
                    self._queue.task_done()
        except Exception:
            # 송신/직렬화 실패를 실행 쪽으로 올리지 않는 연결 경계다.
            self._fail("send_failed")
        finally:
            self._is_open = False
            self._discard_pending()


class QuestionHandler(Protocol):
    """접수까지만 수행하고 반환한다. 이후 이벤트는 해당 channel로 보낸다."""

    async def handle(
        self, user: AuthenticatedUser, question: Question, channel: EventChannel
    ) -> None: ...
