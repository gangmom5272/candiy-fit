from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.health import service
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.rest_schemas import metric_response
from app.health.schemas import BODY_UNITS
from tests.health.test_body_daily import body_reads as body_reads
from tests.health.test_body_daily import record
from tests.health.test_calculations import TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


async def call(factory, metric="weights", start=None, end=TODAY):
    return await service.get_metric_result(
        USER,
        metric,
        start or TODAY - timedelta(days=3),
        end,
        session_factory=factory,
    )


@pytest.mark.parametrize("metric,unit", BODY_UNITS.items())
async def test_body_summary_keeps_missing_and_today_out_of_statistics(
    body_reads, metric, unit
):
    factory, states, rows, lifecycle = body_reads
    states.return_value = [state(metric=metric)]
    rows.return_value = [
        record(TODAY - timedelta(days=3), value=Decimal("1.751")),
        record(TODAY - timedelta(days=1), value=Decimal("1.779")),
        record(value=Decimal("9.999")),
    ]
    result = await call(factory, metric)
    assert result.unit == unit
    assert result.summary.avg == Decimal("1.77")
    assert result.summary.daily_min.value == Decimal("1.751")
    assert result.summary.daily_max.value == Decimal("1.779")
    assert result.summary.days_with_data == result.summary.days_used_for_summary == 2
    assert result.summary.completed_days == 3
    assert result.summary.total is result.summary.linked_from is None
    assert result.summary.missing_days == [TODAY - timedelta(days=2)]
    assert result.summary.unsynced_days == []
    assert result.days[1].reason == "NO_RECORD"
    assert result.days[1].value is None
    assert result.in_progress.value == Decimal("9.999")
    assert result.data_status.synced_until is None
    assert result.detail.model_dump() == dict(
        measured_min=None, measured_max=None, nights=[], daily_ranges=[], sessions=[]
    )
    payload = metric_response(result.to_public()).model_dump(mode="json")
    assert payload["metric"] == metric and payload["unit"] == unit
    assert payload["inProgress"]["value"] == 9.999
    assert payload["days"][1]["reason"] == "NO_RECORD"
    assert "user_id" not in payload
    assert lifecycle == ["transaction_closed", "session_closed"]
    rows.assert_awaited_once()


@pytest.mark.parametrize("today_only", [True, False])
@pytest.mark.parametrize("has_record", [True, False])
async def test_empty_or_today_only_never_fabricates_summary(
    body_reads, today_only, has_record
):
    factory, _, rows, _ = body_reads
    rows.return_value = [record()] if has_record else []
    result = await call(
        factory, start=TODAY if today_only else TODAY - timedelta(days=1)
    )
    assert (
        result.summary.avg
        is result.summary.daily_min
        is result.summary.daily_max
        is None
    )
    assert result.summary.days_used_for_summary == 0
    assert result.in_progress.missing is (not has_record)
    assert result.in_progress.value == (Decimal("70.25") if has_record else None)


async def test_past_tie_uses_earliest_date_without_progress(body_reads):
    factory, _, rows, _ = body_reads
    end = TODAY - timedelta(days=1)
    rows.return_value = [record(end - timedelta(days=1)), record(end)]
    result = await call(factory, start=end - timedelta(days=1), end=end)
    assert result.in_progress is None
    assert (
        result.summary.daily_min.date
        == result.summary.daily_max.date
        == end - timedelta(days=1)
    )


@pytest.mark.parametrize("history", [False, True])
async def test_initial_collection_is_distinct_from_lost_state(
    body_reads, monkeypatch, history
):
    factory, states, rows, _ = body_reads
    states.return_value = []
    monkeypatch.setattr(
        service.repository, "has_health_history", AsyncMock(return_value=history)
    )
    with pytest.raises(
        HealthDataLookupError if history else HealthDataNotReadyError
    ) as error:
        await call(factory)
    assert error.value.metrics == ("weights",)
    rows.assert_not_awaited()


@pytest.mark.parametrize("not_ready", [True, False])
async def test_body_error_contains_requested_metric(body_reads, not_ready):
    factory, states, rows, _ = body_reads
    if not_ready:
        states.return_value = [state(metric="weights", initial_sync_done=False)]
    else:
        rows.side_effect = OSError("synthetic")
    with pytest.raises(
        HealthDataNotReadyError if not_ready else HealthDataLookupError
    ) as error:
        await call(factory)
    assert error.value.metrics == ("weights",)


def test_readiness_scope_remains_existing_five_metrics():
    assert service.STATUS_METRICS == (
        "steps",
        "heart_rates",
        "oxygen_saturations",
        "sleeps",
        "exercises",
    )


async def test_request_crossing_kst_midnight_keeps_start_date(body_reads, monkeypatch):
    factory, _, rows, _ = body_reads
    before = datetime(2026, 10, 22, 14, 59, 59, tzinfo=UTC)
    after = before + timedelta(seconds=2)
    instants = iter([before, after, after])
    monkeypatch.setattr(service, "_now", lambda: next(instants))
    rows.return_value = [record()]
    result = await call(factory, start=TODAY)
    assert result.days[0].is_in_progress
    assert result.in_progress.value == Decimal("70.25")
    assert result.summary.avg is None
    assert result.data_status.checked_at == after


async def test_body_lookup_and_conversion_share_total_budget(body_reads, monkeypatch):
    factory, _, rows, lifecycle = body_reads
    elapsed = [0.0]
    monkeypatch.setattr(service, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 1.0)
    convert = service.calculate_body_metric

    async def read(*args):
        elapsed[0] += 0.6
        return [record()]

    def calculate(body):
        elapsed[0] += 0.6
        return convert(body)

    rows.side_effect = read
    monkeypatch.setattr(service, "calculate_body_metric", calculate)
    with pytest.raises(HealthDataLookupError) as error:
        await call(factory)
    assert error.value.metrics == ("weights",)
    assert lifecycle == ["transaction_closed", "session_closed"]
