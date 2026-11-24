from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from coach_ai.agent.context import CoachContext
from coach_ai.agent.state import Evidence

Stage = Literal["thinking", "tool_call", "tool_done", "answering"]


class EventSink(Protocol):
    """이벤트를 받는 쪽. WS 연결, CLI 출력, 테스트 목록 등이 구현한다."""

    async def emit(self, event: dict[str, Any]) -> None: ...


class ListSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class EventEmitter:
    """요청 하나의 WS 이벤트를 만든다. token 으로 내보낸 글이 최종 답변(answer)이다.

    종료 이벤트(done, error)는 요청마다 하나다. 두 번째 종료 이벤트는 내보내지 않고 RuntimeError 를 발생시킨다.
    """

    def __init__(self, ctx: CoachContext, sink: EventSink) -> None:
        self._ctx = ctx
        self._sink = sink
        self._seq = 0
        self._sent_evidence = 0
        self._deltas: list[str] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        # 종료 이벤트를 내보내려 한 적이 있는지. sink 가 실패했어도 True 다
        self.finished = False

    @property
    def answer(self) -> str:
        return "".join(self._deltas)

    async def _emit(self, type_: str, **fields: Any) -> None:
        event = {"type": type_, "conversation_id": self._ctx.conversation_id, "request_id": self._ctx.request_id}
        await self._sink.emit({**event, **fields})

    async def status(self, stage: Stage, message: str, tool: str | None = None) -> None:
        fields: dict[str, Any] = {"stage": stage, "message": message}
        if tool is not None:
            fields["tool"] = tool
        await self._emit("status", **fields)

    async def evidence(self, items: Sequence[Evidence]) -> None:
        """누적 근거 목록에서 새로 생긴 근거만 내보낸다."""
        new = list(items[self._sent_evidence :])
        if not new:
            return
        self._sent_evidence = len(items)
        await self._emit("evidence", items=[e.event_item() for e in new])

    async def token(self, delta: str) -> None:
        await self._emit("token", seq=self._seq, delta=delta)
        self._seq += 1
        self._deltas.append(delta)

    def add_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    async def done(self, message_id: str | None, finish_reason: Literal["stop", "length"]) -> None:
        self._finish()
        fields: dict[str, Any] = {"message_id": message_id, "finish_reason": finish_reason}
        if self.prompt_tokens or self.completion_tokens:
            fields["usage"] = {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens}
        await self._emit("done", **fields)

    async def error(self, code: str, message: str, retryable: bool, metrics: Sequence[str] = ()) -> None:
        """message 는 사용자에게 보여도 되는 정해진 문장이다. 예외 문구를 넣지 않는다."""
        self._finish()
        await self._emit("error", code=code, message=message, retryable=retryable, metrics=list(metrics))

    def _finish(self) -> None:
        if self.finished:
            raise RuntimeError("종료 이벤트(done, error)는 요청마다 하나만 낸다")
        self.finished = True
