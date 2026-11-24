"""건강 진단 전용 출력. 원문을 마스킹하지 않고 허용한 값만 기록한다."""

import json
import logging
import math
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from threading import RLock
from time import monotonic
from typing import TextIO
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.health.errors import HealthDataLookupError, InvalidHealthData

LOGGER = logging.getLogger("app.health.diagnostics")
_LOCK = RLock()
_users = 0
_handler: logging.Handler | None = None
_previous: tuple[int, bool] | None = None

_KINDS = frozenset(
    {
        "HealthDataLookupError",
        "HealthDataNotReadyError",
        "HealthDataForbiddenError",
        "HealthDataNotLinkedError",
        "ReadCleanupError",
        "CancelledError",
        "TimeoutError",
        "OSError",
        "OperationalError",
        "InterfaceError",
        "DBAPIError",
        "InvalidHealthData",
        "ValidationError",
        "PydanticSerializationError",
        "unexpected",
    }
)
_RESULTS = frozenset(
    {
        "SYNCED",
        "SKIPPED_RECENT",
        "BUSY",
        "SYNTHETIC",
        "DISABLED",
        "CONFIGURATION_ERROR",
        "CANDIY_AUTH_FAILED",
        "METRIC_FAILED",
        "AGGREGATE_FAILED",
        "STORAGE_UNAVAILABLE",
        "TIMEOUT",
        "CAPACITY_EXCEEDED",
        "SHUTTING_DOWN",
        "CANDIY_USER_NOT_LINKED",
        "MEMBER_REMOVED",
        "CANCELLED",
        "unexpected",
        "unknown",
    }
)
_TIMES = frozenset(
    {
        "cleanup_seconds",
        "disposal_seconds",
        "cleanup_limit_seconds",
        "disposal_limit_seconds",
        "block_seconds",
        "required_lookup_seconds",
        "score_lookup_seconds",
        "block_started_at",
        "cleanup_started_at",
        "disposal_started_at",
    }
)
_BOOLS = frozenset({"driver_closed", "pool_blocked", "tasks_done"})
_ERRORS = frozenset(
    {"original_kind", "score_error_kind", "cleanup_kind", "disposal_kind"}
)
_FIELDS = {
    "health_query_failed": frozenset({"route", "stage", "cause", "seconds"}),
    "daily_message_read_finished": _TIMES
    | _BOOLS
    | _ERRORS
    | {"metric", "score_error_reason"},
    "daily_message_score_unavailable": frozenset({"reason"}),
    "member_sync": frozenset({"result", "seconds"}),
}


def _payload(event: object, fields: dict) -> dict | None:
    if type(event) is not str or event not in _FIELDS:
        return None
    payload = {"event": event}
    request_id = fields.get("request_id")
    if type(request_id) is str and len(request_id) == 36:
        try:
            parsed = UUID(request_id)
        except ValueError:
            pass
        else:
            if parsed.version == 4 and str(parsed) == request_id:
                payload["request_id"] = request_id
    for name in _FIELDS[event]:
        if name not in fields:
            continue
        value = fields[name]
        if name in _TIMES or name == "seconds":
            if value is None:
                payload[name] = None
            elif (
                type(value) in (int, float)
                and 0 <= value <= 1e12
                and math.isfinite(value)
            ):
                payload[name] = value
        elif name in _BOOLS:
            if type(value) is bool:
                payload[name] = value
        elif name in _ERRORS:
            payload[name] = (
                value
                if type(value) is str and value in _KINDS
                else "unexpected"
                if value is not None
                else None
            )
        else:
            allowed = {
                "route": {"metric", "timeline"},
                "stage": {"lookup", "response"},
                "cause": {"timeout", "database", "validation", "state", "unknown"},
                "metric": {"steps", "exercises", "sleeps"},
                "reason": {"MISMATCH", "INVALID_VALUE"},
                "score_error_reason": {"LOOKUP_FAILED", "DATA_NOT_READY"},
                "result": _RESULTS,
            }[name]
            payload[name] = (
                value
                if type(value) is str and value in allowed
                else "unknown"
                if value is not None
                else None
            )
    return payload


def emit_health_event(event: str, **fields: object) -> None:
    """관찰 실패가 원래 결과/예외를 바꾸지 않으며 원본 예외를 재기록하지 않는다."""
    try:
        payload = _payload(event, fields)
        if payload is not None:
            payload.pop("event")
            LOGGER.info(event, extra=payload)
    except Exception:
        # 진단 전용 최외곽: handler 실패도 업무 경로로 전파하거나 원문으로 출력하지 않는다.
        return


class _SafeHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # getMessage/formatException을 호출하지 않는다. args와 예외는 읽지 않는다.
        try:
            payload = _payload(record.msg, record.__dict__)
            if payload is not None:
                self.stream.write(
                    json.dumps(payload, ensure_ascii=True, allow_nan=False) + "\n"
                )
                self.flush()
        except Exception:
            # 기본 handleError는 원본 LogRecord/예외를 stderr에 출력할 수 있다.
            return


@contextmanager
def health_logging(stream: TextIO | None = None) -> Iterator[None]:
    """앱 수명 동안만 전용 stdout 출력을 소유한다. 중첩/동시 앱 수명도 중복 출력하지 않는다."""
    global _users, _handler, _previous
    with _LOCK:
        if _users == 0:
            _previous = (LOGGER.level, LOGGER.propagate)
            _handler = _SafeHandler(stream if stream is not None else sys.stdout)
            LOGGER.addHandler(_handler)
            LOGGER.setLevel(logging.INFO)
            LOGGER.propagate = False
        _users += 1
    try:
        yield
    finally:
        with _LOCK:
            _users -= 1
            if _users == 0:
                LOGGER.removeHandler(_handler)
                _handler.close()
                LOGGER.setLevel(_previous[0])
                LOGGER.propagate = _previous[1]
                _handler = None
                _previous = None


def _query_failure_cause(error: BaseException) -> str:
    # 예외 원문·SQL·params를 읽지 않고 알려진 래퍼의 명시적 원인만 확인한다.
    for _ in range(8):
        if not isinstance(error, HealthDataLookupError):
            break
        if error.__cause__ is None:
            return "state"
        error = error.__cause__
    if isinstance(error, (TimeoutError, PoolTimeoutError)):
        return "timeout"
    if isinstance(error, DBAPIError):
        return (
            "timeout"
            if getattr(error.orig, "sqlstate", None) == "57014"
            else "database"
        )
    if isinstance(error, OSError):
        return "database"
    if isinstance(
        error, (InvalidHealthData, ValidationError, PydanticSerializationError)
    ):
        return "validation"
    return "unknown"


@contextmanager
def query_failure_diagnostics(
    request_id: str, *, route: str, stage: str
) -> Iterator[None]:
    """REST 경계에서 알려진 조회 실패만 기록하고 같은 예외를 다시 전달한다."""
    started = monotonic()
    try:
        yield
    except (
        HealthDataLookupError,
        ValidationError,
        PydanticSerializationError,
    ) as error:
        # 진단 분류/출력 실패도 기존 공개 결과에 영향을 주지 않는다.
        with suppress(Exception):
            emit_health_event(
                "health_query_failed",
                request_id=request_id,
                route=route,
                stage=stage,
                cause=_query_failure_cause(error),
                seconds=monotonic() - started,
            )
        raise
