from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import uuid
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from coach_ai.agent.context import CoachContext
from coach_ai.agent.evidence import KindEvidenceDraft, emitted_evidence, evidence_snapshot
from coach_ai.agent.executor import (
    EvidenceDraft,
    ToolCall,
    ToolHandler,
    ToolOutcome,
    ToolReply,
    ToolRequest,
    not_supported,
)
from coach_ai.agent.state import Evidence
from coach_ai.ports import (
    DailyMessageResult,
    DailyMessageStatus,
    HealthDataError,
    HealthDataForbiddenError,
    HealthDataLimitError,
    HealthDataPort,
    HealthDataTargetNotFoundError,
    HealthDataUnavailableError,
    LifestyleScoreResult,
    MetricResult,
    MissingReason,
    SleepSessionsResult,
)
from coach_ai.security.audit import DEFAULT_AUDIT_SINK, AuditSink, ToolCallRecord
from coach_ai.tools.handlers import HANDLERS
from coach_ai.tools.outcomes import lookup_failure
from coach_ai.tools.periods import DateRange, period_range
from coach_ai.tools.schemas import (
    OVERVIEW_METRICS,
    TOOL_INPUTS,
    ComparePeriods,
    GetDailyMessage,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetLifestyleScore,
    GetMetricSummary,
    GetSleepRecords,
    Metric,
    ToolInput,
)

# API 명세: 조회가 1초를 넘으면 LOOKUP_FAILED 다
LOOKUP_TIMEOUT_S = 1.0
MAX_TOOL_CALLS = 6
# 거부 사유는 LLM 에 알리지 않는다 (검사 규칙을 떠보는 데 쓰이지 않게)
DENIED_MESSAGE = "이 요청은 처리할 수 없습니다"


def _call_limit_message(max_calls: int) -> str:
    return f"한 답에서 조회는 {max_calls}번까지라 이 호출은 실행하지 않았다. 기록이 없다는 뜻은 아니다"


# 호출 횟수 한도만 사유를 알린다. 실행하지 않은 호출을 기록이 없다는 결과로 읽은 답을 방지한다
CALL_LIMIT_MESSAGE = _call_limit_message(MAX_TOOL_CALLS)
# 등록되지 않은 도구 이름은 LLM 출력 그대로라 호출 로그 칸 길이로 자른다
TOOL_NAME_MAX = 64

DenyReason = Literal["unknown_tool", "extra_field", "invalid_args", "call_limit", "duplicate", "owner_mismatch"]
# 정상 결과의 기록 유무. 값이 있는 날이 하루라도 있으면 ok 다
DataStatus = Literal["ok", "unsynced", "no_record", "before_link"]
# 값이 있는 결과가 없을 때 이 순서로 앞선 상태를 쓴다
_EMPTY_STATUS_ORDER: tuple[DataStatus, ...] = ("unsynced", "no_record", "before_link")
# 결측 사유를 도구 결과의 결측 구분(기록 없음, 동기화 전, 연동 전)과 같게 나눈다
_MISSING_STATUS: dict[MissingReason, DataStatus] = {
    "NO_RECORD": "no_record",
    "NULL_VALUE": "no_record",
    "ZERO_STEPS": "no_record",
    "UNSYNCED": "unsynced",
    "BEFORE_LINK": "before_link",
}
_MESSAGE_STATUS: dict[DailyMessageStatus, DataStatus] = {
    "READY": "ok",
    "PARTIAL": "ok",
    "EMPTY": "no_record",
    "WAITING": "unsynced",
}
# 기간 표현을 받는 인자. 기간 비교는 두 기간을 받는다
_PERIOD_FIELDS = ("period", "current", "baseline")

_PortResult = MetricResult | SleepSessionsResult | LifestyleScoreResult | DailyMessageResult


class _OwnerMismatchError(Exception):
    pass


class _OwnerCheckedPort:
    def __init__(self, port: HealthDataPort, user_id: str) -> None:
        self._port = port
        self._user_id = user_id
        self.queried_days = 0
        # 소유자 확인을 통과한 결과. 호출 로그의 결과 상태를 정할 때만 읽는다
        self.results: list[_PortResult] = []

    async def get_metric_result(self, user_id: str, metric: Metric, start: dt.date, end: dt.date) -> MetricResult:
        result = await self._port.get_metric_result(user_id, metric, start, end)
        self._check_owner(result)
        self.queried_days += len(result.days)
        self.results.append(result)
        return result

    async def get_sleep_sessions(self, user_id: str, start: dt.date, end: dt.date) -> SleepSessionsResult:
        result = await self._port.get_sleep_sessions(user_id, start, end)
        self._check_owner(result)
        # 수면 기록 도구의 상세 결과라 조회한 날짜 수(result_count)는 기본 결과만 센다
        self.results.append(result)
        return result

    async def get_lifestyle_score(self, user_id: str, day: dt.date) -> LifestyleScoreResult:
        result = await self._port.get_lifestyle_score(user_id, day)
        self._check_owner(result)
        self.queried_days += 1
        self.results.append(result)
        return result

    async def get_daily_message(self, user_id: str, day: dt.date, *, request_id: str) -> DailyMessageResult:
        result = await self._port.get_daily_message(user_id, day, request_id=request_id)
        self._check_owner(result)
        self.queried_days += 1
        self.results.append(result)
        return result

    def _check_owner(self, result: _PortResult) -> None:
        # 소유자가 비어 있어도 다른 회원의 결과로 간주한다
        if getattr(result, "user_id", None) != self._user_id:
            raise _OwnerMismatchError


@dataclass(eq=False)
class _Call:
    call: ToolCall
    received_at: dt.datetime
    sequence_no: int
    round_no: int
    parsed: ToolInput | None = None
    deny: DenyReason | None = None
    outcome: ToolOutcome | None = None
    error: Exception | None = None
    timed_out: bool = False
    queried_days: int = 0
    latency_ms: int = 0
    evidence: tuple[Evidence, ...] = ()
    results: tuple[_PortResult, ...] = ()

    def failure_reason(self) -> str | None:
        if self.error is None:
            if self.outcome is None:
                return "cancelled"
            # 조회하지 않은 안내(invalid_periods, not_supported)를 정상 결과와 구분한다
            return None if self.outcome.status == "ok" else self.outcome.status
        if self.timed_out:
            return "timeout"
        if isinstance(self.error, HealthDataError):
            return self.error.code
        return "internal"

    def result_status(self) -> str | None:
        if self.deny is not None:
            return None
        return self.failure_reason() or _data_status(self.results)


def _statuses(result: _PortResult) -> set[DataStatus]:
    if isinstance(result, MetricResult):
        if any(not day.missing for day in result.days):
            return {"ok"}
        # 값이 없는 결과는 사유가 비어 있어도 기록 없음으로 센다
        return {_MISSING_STATUS[day.reason] for day in result.days if day.reason is not None} or {"no_record"}
    if isinstance(result, SleepSessionsResult):
        # 결측 사유가 없는 결과라 세션이 있을 때만 값이 있는 결과로 센다. 없으면 기본 결과의 상태를 따른다
        return {"ok"} if result.sessions else set()
    if isinstance(result, LifestyleScoreResult):
        return {"ok" if result.reason is None else _MISSING_STATUS[result.reason]}
    return {_MESSAGE_STATUS[result.status]}


def _data_status(results: Sequence[_PortResult]) -> DataStatus:
    """여러 결과(기간 비교, 전체 요약)는 하나라도 값이 있으면 ok 다. 포트를 부르지 않은 정상 결과도 ok 다."""
    found = set().union(*(_statuses(result) for result in results))
    return next((status for status in ("ok", *_EMPTY_STATUS_ORDER) if status in found), "ok")


def _period_dates(inp: ToolInput | None, today: dt.date) -> dict[str, DateRange]:
    """기간 인자마다 그 표현이 가리키는 날짜. 조회 한도는 검사하지 않아 조회하지 않은 기간도 날짜가 있다."""
    dates: dict[str, DateRange] = {}
    for name in _PERIOD_FIELDS:
        expression = getattr(inp, name, None)
        if isinstance(expression, str):
            # 없는 날짜, 뒤바뀐 날짜처럼 날짜를 정할 수 없는 표현은 넣지 않는다
            with suppress(ValueError, OverflowError):
                dates[name] = period_range(expression, today)
    return dates


def _metrics_of(inp: ToolInput | None) -> tuple[Metric, ...]:
    match inp:
        case GetMetricSummary() | GetDailyTrend() | ComparePeriods():
            return (inp.metric,)
        case GetSleepRecords():
            return ("sleep",)
        case GetExerciseSessions():
            return ("exercise",)
        case GetHealthOverview():
            return OVERVIEW_METRICS
        case GetLifestyleScore():
            # 점수 조회 실패는 한 지표의 실패가 아니라 지표 목록을 비운다 (API 명세 5.3 오류의 metrics 는 [])
            return ()
        case GetDailyMessage():
            # 홈 한마디 API 가 응답 변환 실패를 한마디가 읽는 세 지표의 조회 실패로 보내는 것과 같다
            return ("steps", "exercise", "sleep")
    return ()


def _caused(error: Exception, cause: BaseException | None) -> Exception:
    error.__cause__ = cause
    return error


class ToolGateway:
    """모든 도구 호출을 검사하고 실행하는 인가 게이트웨이. 호출 횟수·중복을 요청 단위로 세므로 요청마다 만든다."""

    def __init__(
        self,
        port: HealthDataPort,
        today: dt.date,
        *,
        handlers: Mapping[str, ToolHandler] | None = None,
        audit: AuditSink | None = None,
        timeout: float = LOOKUP_TIMEOUT_S,
        max_calls: int = MAX_TOOL_CALLS,
        evidence_enabled: Collection[str] | None = None,
    ) -> None:
        self._port = port
        self._today = today
        self._handlers = dict(HANDLERS if handlers is None else handlers)
        self._audit = DEFAULT_AUDIT_SINK if audit is None else audit
        self._timeout = timeout
        self._max_calls = max_calls
        self._call_limit_message = _call_limit_message(max_calls)
        # 근거 허용 목록. 없으면 설정(COACH_EVIDENCE_ENABLED)을 읽는다
        self._evidence_enabled = evidence_enabled
        self._executed = 0
        self._seen: set[tuple[str, str]] = set()
        self._evidence_count = 0
        # 도구 메시지의 근거 번호(E1 …). 근거 이벤트로만 보내는 근거는 세지 않는다
        self._ref_count = 0
        # 호출 로그의 순서. 거부한 호출도 센다
        self._received = 0
        self._rounds = 0

    async def execute(
        self,
        calls: Sequence[ToolCall],
        ctx: CoachContext,
        *,
        added: Sequence[ToolCall | None] = (),
        asks_exercise: bool = False,
    ) -> list[ToolReply]:
        self._rounds += 1
        checked = [self._check(call) for call in calls]
        if added:
            checked += self._check_added(checked, added)
        allowed = [c for c in checked if c.deny is None]
        try:
            await asyncio.gather(*(self._run(c, ctx, asks_exercise) for c in allowed))
        except asyncio.CancelledError:
            await self._write_log(checked, ctx)
            raise
        failure = self._failure(allowed)
        replies = [] if failure is not None else [self._reply(c, ctx) for c in checked]
        await self._write_log(checked, ctx)
        if failure is not None:
            raise failure
        return replies

    def _check(self, call: ToolCall) -> _Call:
        self._received += 1
        c = _Call(call, received_at=dt.datetime.now(dt.UTC), sequence_no=self._received, round_no=self._rounds)
        c.parsed, c.deny, key = self._inspect(call)
        if c.deny is None and key is not None:
            self._executed += 1
            self._seen.add(key)
        return c

    def _inspect(self, call: ToolCall) -> tuple[ToolInput | None, DenyReason | None, tuple[str, str] | None]:
        """검사 1~3번의 결과(검증한 입력, 거부 사유, 중복을 가리는 키). 호출 횟수와 중복 기록은 바꾸지 않는다."""
        schema = TOOL_INPUTS.get(call.name)
        if schema is None:
            return None, "unknown_tool", None
        if not isinstance(call.args, Mapping):
            return None, "invalid_args", None
        try:
            parsed = schema.model_validate(dict(call.args))
        except ValidationError as e:
            extra = any(err["type"] == "extra_forbidden" for err in e.errors())
            return None, "extra_field" if extra else "invalid_args", None
        key = (call.name, json.dumps(parsed.model_dump(mode="json"), sort_keys=True))
        if self._executed >= self._max_calls:
            return parsed, "call_limit", key
        if key in self._seen:
            return parsed, "duplicate", key
        return parsed, None, key

    def _check_added(self, checked: Sequence[_Call], added: Sequence[ToolCall | None]) -> list[_Call]:
        """에이전트가 더한 호출 가운데 실행할 호출. 같은 자리 호출이 검사를 통과했고 더한 호출도 통과하는 호출만 받는다.

        거부될 더한 호출은 받은 순서(sequence_no)도 쓰지 않아 실행하지도 기록하지도 않는다.
        """
        accepted = []
        for parent, call in zip(checked, added, strict=True):
            if call is not None and parent.deny is None and self._inspect(call)[1] is None:
                accepted.append(self._check(call))
        return accepted

    async def _run(self, c: _Call, ctx: CoachContext, asks_exercise: bool) -> None:
        port = _OwnerCheckedPort(self._port, ctx.user_id)
        # 보조 조회도 소유자를 확인한다. 결과 상태와 조회한 날짜 수는 port 의 조회로만 정한다
        aux = _OwnerCheckedPort(self._port, ctx.user_id)
        request = ToolRequest(
            user_id=ctx.user_id,
            today=self._today,
            port=port,
            request_id=ctx.request_id,
            aux_port=aux,
            asks_exercise=asks_exercise,
        )
        handler = self._handlers.get(c.call.name)
        started = time.perf_counter()
        limit = asyncio.timeout(self._timeout)
        try:
            async with limit:
                c.outcome = not_supported(c.call.name) if handler is None else await handler(c.parsed, request)
        except _OwnerMismatchError as e:
            c.deny = "owner_mismatch"
            c.error = e
        except (HealthDataLimitError, HealthDataTargetNotFoundError) as e:
            # 처리 함수가 잡지 않은 한도 초과·대상 없음도 요청을 끝내지 않고 도구 결과로 전달한다
            c.outcome = lookup_failure(c.call.name, e)
        except Exception as e:
            c.error = e
            # 처리기 안에서 난 TimeoutError 는 조회 기한 초과가 아니다
            c.timed_out = isinstance(e, TimeoutError) and limit.expired()
        else:
            c.queried_days = port.queried_days
            c.results = tuple(port.results)
        finally:
            c.latency_ms = round((time.perf_counter() - started) * 1000)

    def _failure(self, allowed: Sequence[_Call]) -> Exception | None:
        # 무결성 위반이 다른 실패에 가려지지 않게 소유자 불일치를 먼저 확인한다
        for c in allowed:
            if c.deny == "owner_mismatch":
                return _caused(HealthDataForbiddenError(metrics=_metrics_of(c.parsed)), c.error)
        for c in allowed:
            if c.error is not None:
                if c.timed_out:
                    return _caused(HealthDataUnavailableError(metrics=_metrics_of(c.parsed)), c.error)
                return c.error
        return None

    def _reply(self, c: _Call, ctx: CoachContext) -> ToolReply:
        if c.deny == "call_limit":
            return ToolReply(c.call.id, c.call.name, self._call_limit_message)
        if c.deny is not None or c.outcome is None:
            return ToolReply(c.call.id, c.call.name, DENIED_MESSAGE)
        enabled = self._evidence_enabled
        evidence = []
        for draft in emitted_evidence(c.outcome.evidence, request_id=ctx.request_id, enabled=enabled):
            self._ref_count += 1
            evidence.append(self._evidence(draft, f"E{self._ref_count}"))
        for draft in emitted_evidence(c.outcome.event_only_evidence, request_id=ctx.request_id, enabled=enabled):
            evidence.append(self._evidence(draft, None))
        c.evidence = tuple(evidence)
        refs = [e.ref for e in c.evidence if e.ref is not None]
        content = json.dumps(c.outcome.to_message(refs), ensure_ascii=False)
        return ToolReply(c.call.id, c.call.name, content, c.evidence, c.outcome.extra_period)

    def _evidence(self, draft: EvidenceDraft | KindEvidenceDraft, ref: str | None) -> Evidence:
        """근거 하나. 순서(position)는 근거 이벤트로만 보내는 근거까지 모든 근거를 센다."""
        self._evidence_count += 1
        # 대화 API 가 이 UUID 로 coaching_evidence 를 저장하므로 호출 로그의 evidence_ids 와 같은 값이 된다
        return Evidence(
            id=str(uuid.uuid4()),
            position=self._evidence_count,
            tool=draft.tool,
            metric=draft.metric,
            period=draft.period,
            summary=draft.summary,
            snapshot=evidence_snapshot(draft),
            ref=ref,
        )

    def _record(self, c: _Call, ctx: CoachContext) -> ToolCallRecord:
        period_dates = _period_dates(c.parsed, self._today)
        # 처리 함수가 인자의 기간 밖으로 계산에 쓴 기간은 인자의 기간과 함께 그 이름으로 기록한다
        extra = None if c.outcome is None else c.outcome.extra_period
        if extra is not None:
            period_dates[extra.name] = extra.period
        return ToolCallRecord(
            request_id=ctx.request_id,
            user_id=ctx.user_id,
            sequence_no=c.sequence_no,
            round_no=c.round_no,
            tool_name=c.call.name[:TOOL_NAME_MAX],
            # 기본값과 같은 선택 인자(include_non_main=false)는 넣지 않아 그 인자를 적지 않은 호출과 같게 기록한다
            args=None if c.parsed is None else c.parsed.model_dump(mode="json", exclude_defaults=True),
            period_dates=period_dates,
            decision="deny" if c.deny is not None else "allow",
            reason=c.deny if c.deny is not None else c.failure_reason(),
            result_status=c.result_status(),
            latency_ms=c.latency_ms,
            result_count=c.queried_days,
            evidence_ids=tuple(e.id for e in c.evidence),
            created_at=c.received_at,
        )

    async def _write_log(self, checked: Sequence[_Call], ctx: CoachContext) -> None:
        for c in checked:
            # 감사 기록이 실패해도 응답은 그대로 나가야 한다
            with suppress(Exception):
                await self._audit.write(self._record(c, ctx))
