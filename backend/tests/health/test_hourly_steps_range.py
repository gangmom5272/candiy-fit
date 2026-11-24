from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import service
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)
from app.health.schemas import HourlyStepsRecord
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


async def test_steps_range_is_independent_and_preserves_missing(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value += [state(metric="heart_rates", initial_sync_done=False)]
    start = TODAY - timedelta(days=2)
    rows = AsyncMock(
        return_value=[
            HourlyStepsRecord(date=start, hourly_steps=[0] * 24),
            HourlyStepsRecord(date=TODAY, hourly_steps=list(range(24))),
        ]
    )
    monkeypatch.setattr(service.repository, "list_hourly_steps", rows)
    result = await service.get_hourly_steps_range(
        USER, start, TODAY, start_hour=8, end_hour=10, session_factory=factory
    )
    assert [day.values for day in result.days] == [[0, 0], None, [8, 9]]
    assert [day.total for day in result.days] == [0, None, 17]
    assert result.total is None
    assert result.days[-1].is_in_progress
    assert lifecycle == ["transaction_closed", "session_closed"]
    rows.assert_awaited_once()


@pytest.mark.parametrize(
    "first,last", [(-1, 1), (0, 25), (10, 10), (22, 2), (True, 2), (1.5, 2), ("1", 2)]
)
async def test_invalid_hours_fail_before_database(reads, first, last):
    factory, states, _, _ = reads
    with pytest.raises(ValidationError):
        await service.get_hourly_steps_range(
            USER, TODAY, TODAY, start_hour=first, end_hour=last, session_factory=factory
        )
    states.assert_not_awaited()


async def test_steps_readiness_still_required(reads):
    factory, states, _, _ = reads
    states.return_value = [state(initial_sync_done=False)]
    with pytest.raises(HealthDataNotReadyError):
        await service.get_hourly_steps_range(
            USER, TODAY, TODAY, start_hour=0, end_hour=24, session_factory=factory
        )


@pytest.mark.parametrize("count", [366, 367])
async def test_date_limit_and_full_day_sum(reads, monkeypatch, count):
    factory, states, _, _ = reads
    rows = AsyncMock(
        return_value=[HourlyStepsRecord(date=TODAY, hourly_steps=[1] * 24)]
    )
    monkeypatch.setattr(service.repository, "list_hourly_steps", rows)
    if count == 367:
        with pytest.raises(ValidationError):
            await service.get_hourly_steps_range(
                USER,
                TODAY - timedelta(days=count - 1),
                TODAY,
                start_hour=0,
                end_hour=24,
                session_factory=factory,
            )
        states.assert_not_awaited()
        return
    result = await service.get_hourly_steps_range(
        USER,
        TODAY - timedelta(days=count - 1),
        TODAY,
        start_hour=0,
        end_hour=24,
        session_factory=factory,
    )
    assert len(result.days) == 366 and result.days[-1].total == 24


@pytest.mark.parametrize(
    "start,end",
    [(TODAY, TODAY - timedelta(days=1)), (TODAY, TODAY + timedelta(days=1))],
)
async def test_invalid_dates_fail_before_database(reads, start, end):
    factory, states, _, _ = reads
    with pytest.raises(ValidationError):
        await service.get_hourly_steps_range(
            USER, start, end, start_hour=0, end_hour=24, session_factory=factory
        )
    states.assert_not_awaited()


async def test_recent_failure_preserves_totals_and_partial_null(reads, monkeypatch):
    factory, states, _, _ = reads
    states.return_value = [state(last_status="fail")]
    rows = AsyncMock(
        return_value=[HourlyStepsRecord(date=TODAY, hourly_steps=[1] * 24)]
    )
    monkeypatch.setattr(service.repository, "list_hourly_steps", rows)
    result = await service.get_hourly_steps_range(
        USER, TODAY, TODAY, start_hour=23, end_hour=24, session_factory=factory
    )
    assert result.total == 1 and result.days[0].values == [1]
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.last_success_at == NOW
    rows.return_value = [HourlyStepsRecord(date=TODAY, hourly_steps=None)]
    result = await service.get_hourly_steps_range(
        USER, TODAY, TODAY, start_hour=0, end_hour=24, session_factory=factory
    )
    assert result.total is None and result.days[0].reason == "NULL_VALUE"


@pytest.mark.parametrize(
    "states,error",
    [
        ([], HealthDataLookupError),
        ([state(last_success_at=None)], HealthDataLookupError),
        ([state(end_user_id=uuid4())], HealthDataForbiddenError),
        ([state(last_error="http_404")], HealthDataNotLinkedError),
    ],
)
async def test_status_errors_do_not_read_steps(reads, monkeypatch, states, error):
    factory, source, _, _ = reads
    source.return_value = states
    rows = AsyncMock()
    monkeypatch.setattr(service.repository, "list_hourly_steps", rows)
    with pytest.raises(error):
        await service.get_hourly_steps_range(
            USER, TODAY, TODAY, start_hour=0, end_hour=24, session_factory=factory
        )
    rows.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [OSError("synthetic"), TimeoutError(), RuntimeError("synthetic")]
)
async def test_query_failure_is_not_empty_data(reads, monkeypatch, error):
    factory, _, _, lifecycle = reads
    monkeypatch.setattr(
        service.repository, "list_hourly_steps", AsyncMock(side_effect=error)
    )
    expected = RuntimeError if type(error) is RuntimeError else HealthDataLookupError
    with pytest.raises(expected):
        await service.get_hourly_steps_range(
            USER, TODAY, TODAY, start_hour=0, end_hour=24, session_factory=factory
        )
    assert lifecycle == ["transaction_closed", "session_closed"]
