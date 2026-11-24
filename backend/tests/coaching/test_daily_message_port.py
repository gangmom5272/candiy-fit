"""한마디 백엔드·AI 경계의 직렬화와 오류 계약. 실제 DB·LLM은 사용하지 않는다."""

import asyncio
from datetime import date
from typing import get_args
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from app.coaching import health_port
from app.coaching.health_port import HealthQueryPort
from app.health import daily_message, errors
from app.health.daily_message_read import ReadCleanupError
from app.health.rest_schemas import daily_message_response
from coach_ai import ports

OWNER = UUID("00000000-0000-4000-8000-000000000001")
DAY = date(2026, 10, 29)


def message(status="READY"):
    return daily_message.DailyMessageResult(
        user_id=OWNER,
        date=DAY,
        status=status,
        code={
            "READY": "BALANCED_RECORDS",
            "PARTIAL": "ACTIVE_DAY",
            "EMPTY": "NO_RECORDS",
            "WAITING": "PENDING_RECORDS",
        }[status],
        area="activity" if status == "PARTIAL" else None,
        title="합성 한마디",
        description="합성 기록 안내",
        used_metrics=("steps", "exercises") if status in ("READY", "PARTIAL") else (),
        data_as_of=dict.fromkeys(("steps", "exercises", "sleeps"), None),
        score_computed_at=None,
        notices=[{"metric": "sleeps", "code": "LOOKUP_FAILED", "message": "조회 실패"}]
        if status == "PARTIAL"
        else [],
    )


@pytest.mark.parametrize("status", ["READY", "PARTIAL", "EMPTY", "WAITING"])
async def test_message_snapshot_matches_public_converter_and_preserves_arguments(
    status,
):
    result = message(status)
    lookup = AsyncMock(return_value=result)
    engine = Mock()
    factory = Mock()
    port = HealthQueryPort(factory, engine=engine, message_lookup=lookup)
    assert port.engine is engine
    factory.assert_not_called()
    engine.connect.assert_not_called()
    actual = await port.get_daily_message(
        str(OWNER), DAY, request_id="synthetic-request"
    )
    lookup.assert_awaited_once_with(
        OWNER, today=DAY, request_id="synthetic-request", engine=engine
    )
    assert actual.user_id == str(OWNER)
    assert actual.snapshot == daily_message_response(result).model_dump(
        mode="json", by_alias=False
    ) | {"used_metrics": list(result.used_metrics)}
    assert actual.snapshot["status"] == status
    assert "user_id" not in actual.snapshot
    assert "dataAsOf" not in actual.snapshot


@pytest.mark.parametrize(
    "name",
    [
        "DailyMessageStatus",
        "DailyMessageArea",
        "DailyMessageMetric",
        "DailyMessageNoticeCode",
    ],
)
def test_backend_and_ai_literal_contracts_match(name):
    assert set(get_args(getattr(daily_message, name))) == set(
        get_args(getattr(ports, name))
    )


@pytest.mark.parametrize(
    "source,target,metrics,expected",
    [
        (
            errors.HealthDataNotReadyError,
            ports.HealthDataNotReadyError,
            ("sleeps",),
            ("sleep",),
        ),
        (
            errors.HealthDataLookupError,
            ports.HealthDataUnavailableError,
            ("steps", "exercises", "sleeps"),
            ("steps", "exercise", "sleep"),
        ),
        (errors.HealthDataLookupError, ports.HealthDataUnavailableError, (), ()),
        (errors.HealthDataForbiddenError, ports.HealthDataForbiddenError, (), ()),
        (errors.HealthDataNotLinkedError, ports.HealthDataNotLinkedError, (), ()),
    ],
)
async def test_message_errors_convert_metrics_without_leaking_cause(
    source, target, metrics, expected
):
    original = source(metrics)
    original.__cause__ = RuntimeError("synthetic-sensitive-sql")
    port = HealthQueryPort(
        Mock(), engine=Mock(), message_lookup=AsyncMock(side_effect=original)
    )
    with pytest.raises(target) as caught:
        await port.get_daily_message(str(OWNER), DAY, request_id="r")
    assert caught.value.metrics == expected
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "synthetic-sensitive" not in str(caught.value)


def validation_error():
    return ValidationError.from_exception_data(
        "Synthetic", [{"type": "missing", "loc": ("field",), "input": {}}]
    )


@pytest.mark.parametrize(
    "error", [validation_error(), PydanticSerializationError("synthetic")]
)
async def test_response_conversion_failure_is_three_metric_unavailable(
    monkeypatch, error
):
    monkeypatch.setattr(health_port, "daily_message_response", Mock(side_effect=error))
    port = HealthQueryPort(
        Mock(), engine=Mock(), message_lookup=AsyncMock(return_value=message())
    )
    with pytest.raises(ports.HealthDataUnavailableError) as caught:
        await port.get_daily_message(str(OWNER), DAY, request_id="r")
    assert caught.value.metrics == ("steps", "exercise", "sleep")
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "error",
    [
        ReadCleanupError(),
        validation_error(),
        RuntimeError("synthetic"),
        asyncio.CancelledError(),
    ],
)
async def test_service_internal_errors_and_cancellation_propagate(error):
    port = HealthQueryPort(
        Mock(), engine=Mock(), message_lookup=AsyncMock(side_effect=error)
    )
    with pytest.raises(type(error)) as caught:
        await port.get_daily_message(str(OWNER), DAY, request_id="r")
    assert caught.value is error


@pytest.mark.parametrize(
    "owner,day,engine,error",
    [
        (str(OWNER), DAY, None, RuntimeError),
        ("invalid", DAY, Mock(), ValueError),
        (str(OWNER), date.max, Mock(), ValueError),
    ],
)
async def test_invalid_inputs_do_not_query(owner, day, engine, error):
    lookup = AsyncMock()
    port = HealthQueryPort(Mock(), engine=engine, message_lookup=lookup)
    with pytest.raises(error):
        await port.get_daily_message(owner, day, request_id="r")
    lookup.assert_not_awaited()


async def test_unknown_public_metric_is_not_silently_dropped():
    lookup = AsyncMock(side_effect=errors.HealthDataLookupError(("unknown",)))
    port = HealthQueryPort(Mock(), engine=Mock(), message_lookup=lookup)
    with pytest.raises(ValueError, match="지표"):
        await port.get_daily_message(str(OWNER), DAY, request_id="r")
