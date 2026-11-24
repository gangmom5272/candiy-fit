from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import ChatGeneration, LLMResult

# 에이전트가 요청 단위로 묶어 두면 config metadata 에 request_id 를 넣지 않은 호출도 같은 요청으로 집계된다
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)


@dataclass
class LLMCallRecord:
    """LLM 호출 하나의 계측 기록. 프롬프트와 답변 원문은 저장하지 않는다 (건강 수치가 섞임)."""

    request_id: str | None
    node: str | None
    model: str | None
    # 요약 호출이면 True 다. 예전 기록과 같은 칸이고 call_kind 가 summary 인지와 같다
    background: bool
    stream: bool
    started_at: str
    queue_wait_s: float | None = None  # 동시성 슬롯을 얻기까지 기다린 시간
    # answer(답변) 또는 summary(요약). 슬롯 대기 시간을 종류별로 나눠 볼 때 쓴다. 이 칸이 없는 예전 기록은 답변이다
    call_kind: str = "answer"
    # 아래 두 값은 슬롯을 얻은 뒤부터 잰다. 체감 시간은 queue_wait_s 를 더한 값이다. ttft_s 는 내용이 있는
    # 첫 토큰까지이고 사고 채널 구간과 도구 호출 표기를 제외한 본문 기준이다. 도구 호출만 있는 응답은 비어 있다
    ttft_s: float | None = None
    total_s: float | None = None
    attempts: int = 1
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    finish_reason: str | None = None
    # 답변 앞에서 제외한 사고 채널 구간의 글자 수. 내용은 기록하지 않는다
    # 제외한 구간이 없으면 0 이다. None 은 이 처리를 거치지 않은 호출(이 필드가 없던 예전 기록 등)이다
    thought_chars: int | None = None
    # 답변에서 제외한 도구 호출 표기의 글자 수(표시 포함). 내용은 기록하지 않는다
    # 제외한 표기가 없으면 0 이다. None 은 이 처리를 거치지 않은 호출이다
    tool_call_text_chars: int | None = None
    error_code: str | None = None
    # 서버 인스턴스 구분용. 같은 주소 뒤에 여러 인스턴스가 있으면 응답 시간이 인스턴스별로 갈린다
    system_fingerprint: str | None = None
    instance: dict[str, str] | None = None  # 허용한 응답 헤더 이름 → 값
    kind: Literal["llm"] = "llm"


@dataclass
class StageRecord:
    name: str
    request_id: str | None
    node: str | None
    started_at: str
    duration_s: float = 0.0
    error: str | None = None  # 예외 타입 이름만 기록한다 (예외 메시지에는 값이 섞일 수 있음)
    kind: Literal["stage"] = "stage"


@dataclass
class SummaryRecord:
    """누적 요약 생성 하나의 결과. 요약·대화 원문은 저장하지 않는다."""

    started_at: str
    # "ok" 또는 SummaryError 코드. LLM 호출과 관계없는 예외로 끝나면 예외 타입 이름이다
    code: str
    request_id: str | None = None
    input_tokens: int | None = None  # 요약 요청의 추정 입력 (summary_request_tokens)
    output_tokens: int | None = None  # 새 요약의 count_tokens. 성공했을 때만 있다
    duration_s: float = 0.0
    kind: Literal["summary"] = "summary"


Record = LLMCallRecord | StageRecord | SummaryRecord


class MetricsRecorder:
    """최근 기록을 메모리에 들고, 경로가 있으면 JSONL 로도 이어 쓴다."""

    def __init__(self, jsonl_path: Path | None = None, *, max_records: int = 1000) -> None:
        self.jsonl_path = jsonl_path
        self._records: deque[Record] = deque(maxlen=max_records)
        # 동기 콜백은 스레드 풀에서 실행될 수 있어 파일 쓰기가 섞이지 않게 잠근다
        self._lock = threading.Lock()

    def add(self, record: Record) -> None:
        with self._lock:
            self._records.append(record)
            if self.jsonl_path is not None:
                self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with self.jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def records(self, kind: Literal["llm", "stage", "summary"] | None = None) -> list[Record]:
        with self._lock:
            return [r for r in self._records if kind is None or r.kind == kind]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_default_recorder: MetricsRecorder | None = None


def get_recorder() -> MetricsRecorder:
    global _default_recorder
    if _default_recorder is None:
        from coach_ai.config import get_settings

        _default_recorder = MetricsRecorder(get_settings().llm_metrics_path)
    return _default_recorder


def set_recorder(recorder: MetricsRecorder) -> None:
    global _default_recorder
    _default_recorder = recorder


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _round(sec: float | None) -> float | None:
    return None if sec is None else round(sec, 4)


@dataclass
class _Pending:
    run_id: UUID
    record: LLMCallRecord
    t_start: float
    t_slot: float | None = None
    t_first: float | None = None


# 채팅 모델의 astream 은 _astream 에 run_manager 를 넘기지 않는다. 그래서 콜백이 시작 시점에 여기에
# 진행 중인 호출을 올려 두고, CoachChatModel 은 note_slot·note_attempt 로 같은 컨텍스트의 호출에 값을 채운다.
# 콜백을 run_inline 으로 두어야 시작 콜백이 호출한 쪽과 같은 컨텍스트에서 실행된다
_active_calls: ContextVar[tuple[_Pending, ...]] = ContextVar("_active_llm_calls", default=())


def _current_calls() -> list[_Pending]:
    active = _active_calls.get()
    if not active:
        return []
    # 콜백 핸들러가 여러 개 붙으면 같은 호출에 기록이 여러 개 생기므로 모두 채운다
    run_id = active[-1].run_id
    return [p for p in active if p.run_id == run_id]


def note_slot(wait_s: float, *, kind: str, stream: bool) -> None:
    """동시성 슬롯을 얻은 직후에 부른다. 계측 콜백이 없으면 아무것도 하지 않는다."""
    now = time.perf_counter()
    for p in _current_calls():
        p.t_slot = now
        p.record.queue_wait_s = _round(wait_s)
        p.record.call_kind = kind
        p.record.background = kind == "summary"
        p.record.stream = stream


def note_attempt(attempt: int) -> None:
    for p in _current_calls():
        p.record.attempts = attempt


def note_thought(chars: int) -> None:
    """응답의 사고 채널 제외를 마친 뒤 부른다. 제외한 구간이 없으면 0 을 넘긴다."""
    for p in _current_calls():
        p.record.thought_chars = chars


def note_tool_call_text(chars: int) -> None:
    """응답의 도구 호출 표기 제외를 마친 뒤 부른다. 제외한 표기가 없으면 0 을 넘긴다."""
    for p in _current_calls():
        p.record.tool_call_text_chars = chars


# 허용 목록에 들어와도 기록하지 않는 헤더. 인증 정보나 세션 값이 담긴다
SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "www-authenticate"}
)


def allowed_headers(names: Iterable[str]) -> tuple[str, ...]:
    """허용 목록을 소문자로 맞추고 민감 헤더를 뺀다."""
    out = []
    for name in names:
        key = name.strip().lower()
        if key and key not in SENSITIVE_HEADERS and key not in out:
            out.append(key)
    return tuple(out)


def response_headers(response: LLMResult) -> dict[str, str]:
    """응답 헤더를 소문자 이름으로 반환한다. 채팅 모델에서 include_response_headers 를 켜야 들어온다."""
    gen = (response.generations or [[]])[0]
    first = gen[0] if gen else None
    if first is None:
        return {}
    meta = first.message.response_metadata if isinstance(first, ChatGeneration) else {}
    headers = (meta or {}).get("headers") or (first.generation_info or {}).get("headers") or {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def system_fingerprint(response: LLMResult) -> str | None:
    gen = (response.generations or [[]])[0]
    first = gen[0] if gen else None
    if first is None:
        return None
    meta = first.message.response_metadata if isinstance(first, ChatGeneration) else {}
    # 비스트리밍은 response_metadata, 스트리밍은 마지막 청크의 generation_info 에 들어온다
    return (meta or {}).get("system_fingerprint") or (first.generation_info or {}).get("system_fingerprint") or None


class LLMMetricsCallback(AsyncCallbackHandler):
    """채팅 모델 호출 하나마다 LLMCallRecord 하나를 기록한다."""

    # 계측이 실패해도 사용자 응답은 계속 나가야 한다
    raise_error = False
    run_inline = True

    def __init__(self, recorder: MetricsRecorder | None = None, *, instance_headers: Iterable[str] = ()) -> None:
        self._recorder = recorder
        self._pending: dict[UUID, _Pending] = {}
        self.instance_headers = allowed_headers(instance_headers)

    @property
    def recorder(self) -> MetricsRecorder:
        return self._recorder or get_recorder()

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        metadata = metadata or {}
        params = invocation_params or {}
        pending = _Pending(
            run_id=run_id,
            record=LLMCallRecord(
                request_id=metadata.get("request_id") or current_request_id.get(),
                # LangGraph 가 실행 중인 노드 이름을 langgraph_node 로 넣어 준다
                node=metadata.get("node") or metadata.get("langgraph_node"),
                model=params.get("model") or params.get("model_name"),
                background=False,
                stream=False,
                started_at=_now_iso(),
            ),
            t_start=time.perf_counter(),
        )
        self._pending[run_id] = pending
        _active_calls.set((*_active_calls.get(), pending))

    async def on_llm_new_token(self, token: str, *, run_id: UUID, **kwargs: Any) -> None:
        pending = self._pending.get(run_id)
        # 첫 청크는 role 만 있고 내용이 빈 문자열이라 첫 글자로 세지 않는다
        if pending is None or pending.t_first is not None or not token:
            return
        pending.t_first = time.perf_counter()

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        pending = self._pending.pop(run_id, None)
        if pending is None:
            return
        _fill_from_result(pending.record, response)
        pending.record.system_fingerprint = system_fingerprint(response)
        if self.instance_headers:
            headers = response_headers(response)
            picked = {h: headers[h] for h in self.instance_headers if h in headers}
            pending.record.instance = picked or None
        self._finish(pending)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        pending = self._pending.pop(run_id, None)
        if pending is None:
            return
        pending.record.error_code = getattr(error, "code", None) or type(error).__name__
        self._finish(pending)

    def _finish(self, pending: _Pending) -> None:
        now = time.perf_counter()
        _active_calls.set(tuple(p for p in _active_calls.get() if p is not pending))
        base = pending.t_slot if pending.t_slot is not None else pending.t_start
        rec = pending.record
        rec.total_s = _round(now - base)
        if pending.t_first is not None:
            rec.ttft_s = _round(pending.t_first - base)
        self.recorder.add(rec)


def _fill_from_result(rec: LLMCallRecord, response: LLMResult) -> None:
    gen = (response.generations or [[]])[0]
    first = gen[0] if gen else None
    if isinstance(first, ChatGeneration):
        msg = first.message
        usage = getattr(msg, "usage_metadata", None) or {}
        rec.input_tokens = usage.get("input_tokens")
        rec.output_tokens = usage.get("output_tokens")
        rec.cached_tokens = (usage.get("input_token_details") or {}).get("cache_read")
        rec.finish_reason = (msg.response_metadata or {}).get("finish_reason")
    if first is not None and rec.finish_reason is None:
        rec.finish_reason = (first.generation_info or {}).get("finish_reason")


@contextmanager
def stage(
    name: str,
    *,
    request_id: str | None = None,
    node: str | None = None,
    recorder: MetricsRecorder | None = None,
) -> Iterator[StageRecord]:
    """도구 실행처럼 LLM 이 아닌 구간의 소요 시간을 기록한다. async 함수 안에서도 그대로 쓴다."""
    rec = StageRecord(
        name=name,
        request_id=request_id or current_request_id.get(),
        node=node,
        started_at=_now_iso(),
    )
    t0 = time.perf_counter()
    try:
        yield rec
    except BaseException as e:
        rec.error = type(e).__name__
        raise
    finally:
        rec.duration_s = round(time.perf_counter() - t0, 4)
        (recorder or get_recorder()).add(rec)


def _recorders_of(model: Any) -> list[MetricsRecorder]:
    """model 에 붙은 계측 콜백의 기록기. 같은 기록기는 한 번만 넣는다."""
    callbacks = getattr(model, "callbacks", None)
    found: list[MetricsRecorder] = []
    for callback in getattr(callbacks, "handlers", callbacks) or []:
        if isinstance(callback, LLMMetricsCallback) and all(callback.recorder is not r for r in found):
            found.append(callback.recorder)
    return found


@contextmanager
def summary_record(model: Any, *, input_tokens: int) -> Iterator[SummaryRecord]:
    """요약 생성 하나의 결과를 model 의 LLM 호출 기록과 같은 기록기에 기록한다. 계측 콜백이 없으면 기록하지 않는다.

    성공하면 호출한 쪽이 output_tokens 를 채운다. 예외로 끝나면 예외의 code(SummaryError 코드) 또는 예외 타입 이름을
    기록한다. 파일 기록이 실패해도 요약 결과는 바뀌지 않는다.
    """
    rec = SummaryRecord(
        started_at=_now_iso(), code="ok", request_id=current_request_id.get(), input_tokens=input_tokens
    )
    t0 = time.perf_counter()
    try:
        yield rec
    except BaseException as e:
        rec.code = getattr(e, "code", None) or type(e).__name__
        raise
    finally:
        rec.duration_s = round(time.perf_counter() - t0, 4)
        for recorder in _recorders_of(model):
            with suppress(OSError):
                recorder.add(rec)
