from __future__ import annotations

import asyncio
import random
import re
import time
import weakref
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import dataclass
from typing import Any, Literal, get_args

import httpx
import openai
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import Field, model_validator

from coach_ai.config import Settings, get_settings
from coach_ai.llm.metrics import (
    LLMMetricsCallback,
    MetricsRecorder,
    note_attempt,
    note_slot,
    note_thought,
    note_tool_call_text,
)
from coach_ai.security.tracing import ensure_tracing_disabled

ErrorCode = Literal["llm_unavailable", "llm_timeout", "rate_limited", "invalid_request"]

USER_MESSAGES: dict[ErrorCode, str] = {
    "llm_unavailable": "지금은 답변을 만들 수 없어요. 잠시 후 다시 시도해 주세요.",
    "llm_timeout": "답변이 너무 오래 걸려 중단했어요. 다시 시도해 주세요.",
    "rate_limited": "요청이 많아 잠시 처리할 수 없어요. 잠시 후 다시 시도해 주세요.",
    "invalid_request": "요청을 처리할 수 없어요.",
}
CONTEXT_MESSAGE = "대화가 너무 길어 답변을 만들 수 없어요. 새 대화로 시작해 주세요."


class LLMError(Exception):
    """WS error 이벤트로 바로 옮길 수 있는 LLM 호출 실패.

    message 는 사용자에게 보여도 되는 문장이고, 서버 응답 본문은 담지 않는다.
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        retryable: bool,
        detail: str,
        status_code: int | None = None,
        stream_started: bool = False,
        message: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.detail = detail
        self.status_code = status_code
        self.stream_started = stream_started
        self.message = message or USER_MESSAGES[code]
        super().__init__(f"{code}: {detail}")

    def to_ws(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class LLMContextLengthError(LLMError):
    """입력+출력이 컨텍스트 상한을 넘은 경우. 다시 보내도 같으므로 요약·자르기로 줄여야 한다."""

    reason = "context_length_exceeded"

    def __init__(self, *, detail: str, status_code: int | None = None, stream_started: bool = False) -> None:
        super().__init__(
            "invalid_request",
            retryable=False,
            detail=detail,
            status_code=status_code,
            stream_started=stream_started,
            message=CONTEXT_MESSAGE,
        )


class LLMEmptyResponseError(LLMError):
    """도구를 묶은 호출이 글(공백 제외)도 도구 호출도 없이 끝난 경우.

    모델 호출 노드가 같은 요청을 한 번 다시 보내도 비었을 때 발생시킨다.
    사고 채널 구간만 오고 닫는 표시 없이 끝난 응답도 사고 구간을 모두 제외해 빈 응답이 된다.
    """

    reason = "empty_response"

    def __init__(self) -> None:
        super().__init__("llm_unavailable", retryable=True, detail=self.reason)


# vLLM 은 상한 초과를 400 과 "maximum context length is N" 문장으로 알린다
_CONTEXT_RE = re.compile(r"maximum (?:context|model) length|context_length_exceeded|context window", re.IGNORECASE)


def _is_context_overflow(e: BaseException) -> bool:
    return isinstance(e, ContextOverflowError) or bool(_CONTEXT_RE.search(str(e)))


def to_llm_error(e: BaseException, *, stream_started: bool = False) -> LLMError | None:
    """LLM 호출 중 난 예외를 LLMError 로 바꾼다. LLM 호출과 무관한 예외(코드 버그 등)는 None 을 반환한다."""
    if isinstance(e, LLMError):
        return e
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    detail = f"{name}" + (f" status={status}" if status else "")

    def make(code: ErrorCode, retryable: bool) -> LLMError:
        return LLMError(code, retryable=retryable, detail=detail, status_code=status, stream_started=stream_started)

    # APITimeoutError 는 APIConnectionError 의 하위 클래스라 먼저 검사한다
    if isinstance(e, openai.APITimeoutError):
        return make("llm_timeout", True)
    if isinstance(e, openai.APIConnectionError):
        return make("llm_unavailable", True)
    if isinstance(e, openai.RateLimitError):
        return make("rate_limited", True)
    if isinstance(e, openai.APIStatusError):
        if _is_context_overflow(e):
            return LLMContextLengthError(detail=detail, status_code=status, stream_started=stream_started)
        if e.status_code >= 500:
            return make("llm_unavailable", True)
        # 인증·모델 이름 오류는 서버 설정 문제라 사용자가 다시 보내도 같다
        if e.status_code in (401, 403, 404):
            return make("llm_unavailable", False)
        return make("invalid_request", False)
    if isinstance(e, openai.APIError):
        # 스트리밍 도중 서버가 SSE 로 보낸 오류 이벤트가 여기로 온다
        if _is_context_overflow(e):
            return LLMContextLengthError(detail=detail, stream_started=stream_started)
        return make("llm_unavailable", True)
    # 스트림을 읽는 중에는 SDK 가 감싸지 않은 전송 계층 예외가 그대로 올라온다
    if isinstance(e, TimeoutError | httpx.TimeoutException):
        return make("llm_timeout", True)
    if isinstance(e, httpx.TransportError):
        return make("llm_unavailable", True)
    return None


# 응답을 받기 전에 실패해 다시 보내도 서버에 중복 생성이 생기지 않는 경우다
def _is_transient(e: BaseException) -> bool:
    if isinstance(e, openai.APITimeoutError):
        # 연결 단계 타임아웃만 재시도한다. 응답 대기 중 타임아웃은 서버가 이미 생성 중일 수 있다
        return any(isinstance(c, httpx.ConnectTimeout) for c in _causes(e))
    if isinstance(e, openai.APIConnectionError | httpx.ConnectError | httpx.ConnectTimeout):
        return True
    if isinstance(e, openai.RateLimitError):
        return True
    return isinstance(e, openai.APIStatusError) and e.status_code >= 500 and not _is_context_overflow(e)


def _causes(e: BaseException) -> Iterator[BaseException]:
    # SDK 예외를 LangChain 이 한 번 더 감싸므로 원인 사슬을 끝까지 따라간다
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def _retry_after(e: BaseException) -> float | None:
    response = getattr(e, "response", None)
    if response is None:
        return None
    try:
        return float(response.headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


# thinking 을 꺼도 도구 결과가 들어간 호출에서는 답변 앞 본문(content)에 Gemma 사고 채널이 섞여 올 수 있다
THOUGHT_OPEN = "<|channel>"
THOUGHT_CLOSE = "<channel|>"
# 일부 서버는 도구 없는 요청에 표시 없이 사고 채널 머리글 줄만 본문 맨 앞에 보낸다
THOUGHT_HEADER = "thought\n"


class ThoughtFilter:
    """답변 앞부분의 사고 채널 구간을 제외하고 표시를 뺀 글자 수를 thought_chars 에 기록한다.

    사고 채널 구간은 THOUGHT_OPEN 부터 THOUGHT_CLOSE 까지이거나, 표시 없이 온 머리글 줄(THOUGHT_HEADER)이다.
    표시나 머리글이 여러 청크로 쪼개져 와도 되도록 앞부분을 모아 둔다. 답변 중간의 표시와 머리글은 그대로 둔다.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._state: Literal["head", "thought", "body"] = "head"
        self.thought_chars = 0

    def feed(self, text: str) -> str:
        """받은 조각을 넣고 본문으로 내보낼 문자열을 반환한다."""
        if self._state == "body":
            return text
        self._buf += text
        if self._state == "head":
            lead = self._buf.lstrip()
            if lead.startswith(THOUGHT_OPEN):
                self._state = "thought"
                self._buf = lead[len(THOUGHT_OPEN) :]
            elif lead.startswith(THOUGHT_HEADER):
                self.thought_chars = len(THOUGHT_HEADER)
                self._buf, self._state = "", "body"
                return lead[len(THOUGHT_HEADER) :]
            elif THOUGHT_OPEN.startswith(lead) or THOUGHT_HEADER.startswith(lead):
                return ""  # 표시나 머리글의 앞부분일 수 있어 다음 조각까지 기다린다
            else:
                return self._release()
        end = self._buf.find(THOUGHT_CLOSE)
        if end < 0:
            return ""
        self.thought_chars = end
        rest = self._buf[end + len(THOUGHT_CLOSE) :]
        self._buf, self._state = "", "body"
        return rest

    def finish(self) -> str:
        """응답이 끝났을 때 모아 둔 문자열 중 본문인 것을 반환한다."""
        if self._state == "head":
            return self._release()
        if self._state == "thought":
            # 닫는 표시 없이 끝났으면 남은 것은 모두 사고 내용이다
            self.thought_chars = len(self._buf)
            self._buf, self._state = "", "body"
        return ""

    def _release(self) -> str:
        out, self._buf, self._state = self._buf, "", "body"
        return out


# 도구 정의 없이 보낸 요청의 응답에 도구 호출이 생성되면 서버가 그 표기를 본문(content)으로 보낸다.
# 표기는 TOOL_CALL_OPEN 으로 시작해 <tool_call|> 로 끝난다
TOOL_CALL_OPEN = "<|tool_call>"
# 사고 채널 표시를 지워 보내는 서버는 도구 호출 표시도 지워 "call:이름{…}" 만 보낼 수 있다(실제 응답은 확인 전).
# 일반 답변과 구분하기 어려워 drop_unmarked_tool_call 로 요청한 호출에서 답변 맨 앞에 올 때만 표기로 센다
UNMARKED_TOOL_CALL = re.compile(r"call:[A-Za-z_]\w*\{", re.ASCII)
# UNMARKED_TOOL_CALL 이 되기 전의 앞부분 ("c" ~ "call:", "call:get_x")
_UNMARKED_TOOL_CALL_HEAD = re.compile(r"c(a(l(l(:([A-Za-z_]\w*)?)?)?)?)?", re.ASCII)


class ToolCallTextFilter:
    """답변에 섞인 도구 호출 표기부터 응답 끝까지 제외하고 제외한 글자 수를 tool_call_text_chars 에 기록한다.

    표기 앞의 답변 글은 그대로 둔다. 표기 뒤의 글은 도구 결과 없이 생성된 글이라 함께 제외한다.
    여는 표시가 여러 청크로 쪼개져 와도 되도록 표시의 앞부분일 수 있는 끝부분은 다음 조각까지 내보내지 않는다.
    unmarked 이면 답변 맨 앞(앞쪽 공백 제외)이 표시 없는 표기(UNMARKED_TOOL_CALL)일 때도 같은 방식으로 제외한다.
    답변 중간의 표시 없는 표기는 그대로 둔다.
    """

    def __init__(self, *, unmarked: bool = False) -> None:
        self._held = ""
        # head 는 답변 맨 앞이 표시 없는 표기인지 아직 판단하지 못한 상태다
        self._state: Literal["head", "body", "dropping"] = "head" if unmarked else "body"
        self.tool_call_text_chars = 0

    def feed(self, text: str) -> str:
        """받은 조각을 넣고 본문으로 내보낼 문자열을 반환한다."""
        if self._state == "dropping":
            self.tool_call_text_chars += len(text)
            return ""
        text = self._held + text
        if self._state == "head":
            lead = text.lstrip()
            if UNMARKED_TOOL_CALL.match(lead):
                return self._drop(text, len(text) - len(lead))
            if not lead or _UNMARKED_TOOL_CALL_HEAD.fullmatch(lead):
                self._held = text
                return ""  # 표시 없는 표기의 앞부분일 수 있어 다음 조각까지 기다린다
            self._state = "body"
        start = text.find(TOOL_CALL_OPEN)
        if start >= 0:
            return self._drop(text, start)
        cut = len(text) - _open_prefix_len(text)
        self._held = text[cut:]
        return text[:cut]

    def finish(self) -> str:
        """응답이 끝났을 때 모아 둔 문자열을 반환한다. 표시나 표기가 끝까지 오지 않았으면 본문이다."""
        out, self._held = self._held, ""
        return out

    def _drop(self, text: str, start: int) -> str:
        self.tool_call_text_chars = len(text) - start
        self._held, self._state = "", "dropping"
        return text[:start]


def _open_prefix_len(text: str) -> int:
    """text 끝이 여는 표시의 앞부분과 겹치는 길이. 표시 전체는 text 에 없다."""
    for n in range(min(len(text), len(TOOL_CALL_OPEN) - 1), 0, -1):
        if text.endswith(TOOL_CALL_OPEN[:n]):
            return n
    return 0


def _with_content(chunk: ChatGenerationChunk, content: str) -> ChatGenerationChunk:
    if content == chunk.message.content:
        return chunk
    # text 는 생성 시점에 message 에서 채워지므로 새로 만든다
    return ChatGenerationChunk(
        message=chunk.message.model_copy(update={"content": content}), generation_info=chunk.generation_info
    )


def _drop_non_answer(result: ChatResult, *, unmarked: bool) -> ChatResult:
    """스트리밍과 같은 순서로 사고 채널 구간과 도구 호출 표기를 제외한다."""
    generations = []
    for gen in result.generations:
        content = gen.message.content
        if isinstance(content, str):
            filt, tool_text = ThoughtFilter(), ToolCallTextFilter(unmarked=unmarked)
            body = tool_text.feed(filt.feed(content) + filt.finish()) + tool_text.finish()
            note_thought(filt.thought_chars)
            note_tool_call_text(tool_text.tool_call_text_chars)
            if body != content:
                gen = ChatGeneration(
                    message=gen.message.model_copy(update={"content": body}), generation_info=gen.generation_info
                )
        generations.append(gen)
    return ChatResult(generations=generations, llm_output=result.llm_output)


# 답변은 사용자가 기다리는 호출(run_coach), 요약은 답변을 저장한 뒤 백그라운드로 실행하는 호출(generate_summary)이다
CallKind = Literal["answer", "summary"]


class _PrioritySlots:
    """답변과 요약이 함께 쓰는 슬롯. 슬롯이 비면 기다리는 답변이 기다리는 요약보다 먼저 얻는다.

    같은 종류끼리는 먼저 기다린 호출이 먼저 얻는다. 이미 슬롯을 얻은 호출은 중단하지 않는다.
    """

    def __init__(self, limit: int) -> None:
        self._free = limit
        self._waiters: dict[CallKind, deque[asyncio.Future[None]]] = {"answer": deque(), "summary": deque()}

    async def acquire(self, kind: CallKind) -> None:
        queued = self._waiters["answer"] or (kind == "summary" and self._waiters["summary"])
        if self._free > 0 and not queued:
            self._free -= 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters[kind].append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # 슬롯을 넘겨받은 뒤 취소됐으면 다음 대기자에게 넘긴다
                self.release()
            else:
                with suppress(ValueError):
                    self._waiters[kind].remove(waiter)
            raise

    def release(self) -> None:
        for kind in get_args(CallKind):
            queue = self._waiters[kind]
            while queue:
                waiter = queue.popleft()
                if not waiter.done():
                    waiter.set_result(None)
                    return
        self._free += 1


@dataclass
class _Pool:
    slots: _PrioritySlots
    # 요약은 한 번에 하나만 실행해 나머지 슬롯을 답변에 남긴다
    summary: asyncio.Semaphore


class ConcurrencyLimiter:
    """프로세스 전체가 공유하는 LLM 동시 호출 제한.

    asyncio 동기화 객체는 처음 쓰인 이벤트 루프에 묶이므로 루프마다 따로 만든다 (서비스는 루프가 하나다).
    """

    def __init__(self) -> None:
        self._pools: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Pool] = weakref.WeakKeyDictionary()

    def _pool(self, limit: int) -> _Pool:
        loop = asyncio.get_running_loop()
        pool = self._pools.get(loop)
        if pool is None:
            pool = _Pool(slots=_PrioritySlots(limit), summary=asyncio.Semaphore(1))
            self._pools[loop] = pool
        return pool

    @asynccontextmanager
    async def acquire(self, limit: int, *, kind: CallKind) -> AsyncIterator[None]:
        pool = self._pool(limit)
        # 요약 차례를 먼저 얻어야 대기 중인 요약 호출이 공용 슬롯 대기열을 채우지 않는다
        async with pool.summary if kind == "summary" else nullcontext():
            await pool.slots.acquire(kind)
            try:
                yield
            finally:
                pool.slots.release()


LIMITER = ConcurrencyLimiter()


class CoachChatModel(ChatOpenAI):
    """동시성 제한·재시도·오류 변환을 더한 ChatOpenAI. 비동기 호출만 지원한다.

    호출마다 call_kind 로 종류를 받는다 (기본 answer). 요약(summary) 호출은 답변 호출보다 슬롯을 늦게 얻고
    다시 보내지 않는다. call_kind 와 drop_unmarked_tool_call 은 이 래퍼의 호출 옵션이라 요청 본문에 넣지 않는다.
    """

    max_concurrency: int = 1
    llm_retries: int = 2
    retry_base_delay: float = 0.5
    retry_max_delay: float = 8.0
    limiter: Any = Field(default=LIMITER, exclude=True)
    # 재시도는 이 래퍼에서만 한다. 요약 호출은 래퍼도 다시 보내지 않으므로 SDK 안의 재시도는 늘 0 이다
    max_retries: int | None = 0

    @model_validator(mode="after")
    def _sdk_retries_off(self) -> CoachChatModel:
        if self.max_retries != 0:
            raise ValueError("CoachChatModel 은 SDK 재시도를 쓰지 않는다 (max_retries=0)")
        return self

    @property
    def _llm_type(self) -> str:
        return "coach-openai-compatible"

    def _get_request_payload(self, input_: LanguageModelInput, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # ChatOpenAI 는 max_completion_tokens 로 바꿔 보내지만 사내 서버 동작 확인은 max_tokens 로 했다
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload

    @asynccontextmanager
    async def _slot(self, *, stream: bool, kind: CallKind) -> AsyncIterator[None]:
        if kind not in get_args(CallKind):
            raise ValueError(f"알 수 없는 호출 종류: {kind!r}")
        t0 = time.perf_counter()
        async with self.limiter.acquire(self.max_concurrency, kind=kind):
            note_slot(time.perf_counter() - t0, kind=kind, stream=stream)
            yield

    def _retries(self, kind: CallKind) -> int:
        # 요약은 대화 API 의 작업 관리가 다시 시도한다
        return 0 if kind == "summary" else self.llm_retries

    def _backoff(self, attempt: int, e: BaseException) -> float:
        hinted = _retry_after(e)
        if hinted is not None:
            return min(hinted, self.retry_max_delay)
        delay = min(self.retry_base_delay * 2 ** (attempt - 1), self.retry_max_delay)
        # 여러 요청이 같은 순간에 다시 몰리지 않게 지연을 흩뜨린다
        return delay * random.uniform(0.5, 1.0)

    # drop_unmarked_tool_call·call_kind 는 이 래퍼의 호출 옵션이다. kwargs 는 요청 본문에 들어가므로 따로 받는다
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        drop_unmarked_tool_call: bool = False,
        call_kind: CallKind = "answer",
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        async with self._slot(stream=True, kind=call_kind):
            attempt = 0
            while True:
                attempt += 1
                note_attempt(attempt)
                started = False
                filt = ThoughtFilter()
                tool_text = ToolCallTextFilter(unmarked=drop_unmarked_tool_call)
                try:
                    # 토큰 콜백은 표시를 제외한 청크로 여기서 부른다. 첫 글자 시간이 표시가 아닌 본문 기준이 된다
                    async for chunk in super()._astream(messages, stop, None, **kwargs):
                        started = True
                        if isinstance(chunk.message.content, str):
                            chunk = _with_content(chunk, tool_text.feed(filt.feed(chunk.message.content)))
                        if run_manager:
                            await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
                        yield chunk
                    tail = tool_text.feed(filt.finish()) + tool_text.finish()
                    if tail:
                        chunk = ChatGenerationChunk(message=AIMessageChunk(content=tail))
                        if run_manager:
                            await run_manager.on_llm_new_token(tail, chunk=chunk)
                        yield chunk
                    note_thought(filt.thought_chars)
                    note_tool_call_text(tool_text.tool_call_text_chars)
                    return
                except Exception as e:
                    err = to_llm_error(e, stream_started=started)
                    if err is None:
                        raise
                    # 청크를 이미 내보냈으면 다시 시작할 때 답변이 중복되므로 재시도하지 않는다
                    if started or attempt > self._retries(call_kind) or not _is_transient(e):
                        raise err from e
                    await asyncio.sleep(self._backoff(attempt, e))

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        drop_unmarked_tool_call: bool = False,
        call_kind: CallKind = "answer",
        **kwargs: Any,
    ) -> ChatResult:
        async with self._slot(stream=False, kind=call_kind):
            attempt = 0
            while True:
                attempt += 1
                note_attempt(attempt)
                try:
                    result = await super()._agenerate(messages, stop, run_manager, **kwargs)
                    return _drop_non_answer(result, unmarked=drop_unmarked_tool_call)
                except Exception as e:
                    err = to_llm_error(e)
                    if err is None:
                        raise
                    if attempt > self._retries(call_kind) or not _is_transient(e):
                        raise err from e
                    await asyncio.sleep(self._backoff(attempt, e))

    # 동기 호출은 차단한다 (asyncio 세마포어를 거치지 않아 동시성 한도를 넘길 수 있음)
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("CoachChatModel 은 ainvoke·astream 같은 비동기 호출만 지원한다")

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        raise NotImplementedError("CoachChatModel 은 ainvoke·astream 같은 비동기 호출만 지원한다")


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    connect = settings.llm_connect_timeout
    # httpx 의 read 는 바이트 사이 대기라 비스트리밍 응답 전체 생성 시간도 이 값 안에 들어와야 한다
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=connect, read=settings.llm_read_timeout, write=connect, pool=connect)
    )


def create_chat_model(
    settings: Settings | None = None,
    *,
    recorder: MetricsRecorder | None = None,
    http_client: httpx.AsyncClient | None = None,
    **overrides: Any,
) -> CoachChatModel:
    """사내 LLM 채팅 모델을 만든다. 모델마다 HTTP 연결 풀이 있어 앱 시작 시 한 번 만들어 재사용한다.

    답변(run_coach)과 요약(generate_summary)이 같은 모델을 쓴다. 호출 종류는 두 함수가 호출마다 정한다.
    LangSmith 추적이 켜져 있으면 모델을 만들지 않고 TracingEnabledError 를 발생시킨다.
    """
    ensure_tracing_disabled()
    settings = settings or get_settings()
    instance_headers = settings.instance_header_names
    extra_body = {"chat_template_kwargs": {"enable_thinking": False}, **overrides.pop("extra_body", {})}
    params: dict[str, Any] = {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "api_key": settings.llm_api_key,
        "max_tokens": settings.llm_max_tokens,
        "stream_usage": True,
        "extra_body": extra_body,
        # SDK 의 자체 재시도는 끄고 재시도는 이 래퍼에서만 한다. 그래야 스트리밍 시작 여부와 시도 횟수를 알 수 있다
        "max_retries": 0,
        "stream_chunk_timeout": settings.llm_chunk_timeout,
        "http_async_client": http_client or make_http_client(settings),
        "max_concurrency": settings.llm_max_concurrency,
        "llm_retries": settings.llm_max_retries,
        "callbacks": [LLMMetricsCallback(recorder, instance_headers=instance_headers)],
        # 헤더를 받으려면 SDK 의 원시 응답 경로를 거쳐야 해서 기록할 헤더가 있을 때만 켠다
        "include_response_headers": bool(instance_headers),
    }
    params.update(overrides)
    return CoachChatModel(**params)
