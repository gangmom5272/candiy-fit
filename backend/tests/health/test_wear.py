from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.health import service
from app.health.calculations import DailyRecord
from app.health.rest_schemas import metric_response
from app.health.schemas import HourlyHeartRecord
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


@pytest.mark.parametrize(
    "offset,count,linked_offset,heart_changes,expected",
    [
        (-3, 12, -2, {}, (None, None)),
        (-1, 12, -30, None, (None, None)),
        (-1, 12, -30, {"initial_sync_done": False}, (None, None)),
        (
            -1,
            12,
            -30,
            {"last_status": "fail", "last_success_at": NOW - timedelta(days=1)},
            (None, None),
        ),
        (
            -2,
            9,
            -30,
            {"last_status": "fail", "last_success_at": NOW - timedelta(days=1)},
            (9, False),
        ),
        (0, 9, -30, {}, (9, None)),
        (0, 10, -30, {}, (10, True)),
        (0, None, -30, {}, (None, None)),
        (-1, 0, -30, {}, (0, False)),
        (-1, 9, -30, {}, (9, False)),
        (-1, 10, -30, {}, (10, True)),
        (-1, 24, -30, {}, (24, True)),
        (-1, None, -30, {}, (0, False)),
    ],
)
def test_rule_order(offset, count, linked_offset, heart_changes, expected):
    day = TODAY + timedelta(days=offset)
    heart = (
        None if heart_changes is None else state(metric="heart_rates", **heart_changes)
    )
    assert (
        service._wear_values(
            day,
            {} if count is None else {day: count},
            heart,
            TODAY + timedelta(days=linked_offset),
            NOW,
            TODAY,
        )
        == expected
    )


@pytest.mark.parametrize(
    "count,expected", [(None, (None, None)), (9, (9, None)), (10, (10, True))]
)
def test_unsynced_past_date_is_incomplete(count, expected):
    day = TODAY - timedelta(days=2)
    assert (
        service._wear_values(
            day,
            {} if count is None else {day: count},
            state(metric="heart_rates"),
            TODAY - timedelta(days=30),
            NOW - timedelta(days=2),
            TODAY,
        )
        == expected
    )


def test_failure_cutoff_uses_kst_date():
    success = datetime(2026, 10, 20, 16, tzinfo=UTC)
    heart = state(metric="heart_rates", last_status="fail", last_success_at=success)
    assert service._wear_values(
        date(2026, 10, 21), {}, heart, date(2026, 10, 1), NOW, TODAY
    ) == (None, None)
    assert service._wear_values(
        date(2026, 10, 20), {}, heart, date(2026, 10, 1), NOW, TODAY
    ) == (0, False)


async def test_service_passes_wear_to_public_without_filtering_values(
    reads, monkeypatch
):
    factory, states, _records, lifecycle = reads
    states.return_value = [state(), state(metric="heart_rates")]
    hearts = AsyncMock(
        return_value=[
            HourlyHeartRecord(
                date=TODAY, hourly_hr={str(hour): {} for hour in range(10)}
            )
        ]
    )
    monkeypatch.setattr(service.repository, "list_hourly_hearts", hearts)
    result = await service.get_metric_result(
        USER, "steps", TODAY, TODAY, session_factory=factory
    )
    assert result.days[0].wear_hours == 10
    assert result.days[0].is_well_worn is True
    assert result.days[0].value == 0
    assert result.to_public().model_dump()["days"][0]["wear_hours"] == 10
    hearts.assert_awaited_once()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "metric",
    [
        "steps",
        "heart_rates",
        "oxygen_saturations",
        "sleeps",
        "exercises",
        "weights",
        "body_fats",
        "basal_metabolic_rates",
        "heights",
    ],
)
async def test_all_common_metrics_use_same_day_wear(reads, monkeypatch, metric):
    factory, states, records, _ = reads
    states.return_value = [
        state(metric="exercise_sessions" if metric == "exercises" else metric)
    ]
    if metric != "heart_rates":
        states.return_value.append(state(metric="heart_rates"))
    records.return_value = []
    monkeypatch.setattr(
        service.repository, "list_body_daily", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        service.repository,
        "list_hourly_hearts",
        AsyncMock(
            return_value=[
                HourlyHeartRecord(
                    date=TODAY, hourly_hr={str(hour): {} for hour in range(10)}
                )
            ]
        ),
    )
    result = await service.get_metric_result(
        USER, metric, TODAY, TODAY, session_factory=factory
    )
    assert (result.days[0].wear_hours, result.days[0].is_well_worn) == (10, True)
    public = metric_response(result.to_public()).model_dump(mode="json", by_alias=True)
    assert public["days"][0]["wearHours"] == 10
    assert public["days"][0]["isWellWorn"] is True


async def test_short_wear_does_not_filter_summary(reads, monkeypatch):
    factory, states, records, _ = reads
    day = TODAY - timedelta(days=1)
    states.return_value = [state(), state(metric="heart_rates")]
    records.return_value = [DailyRecord(day, 5000)]
    monkeypatch.setattr(
        service.repository, "list_hourly_hearts", AsyncMock(return_value=[])
    )
    result = await service.get_metric_result(
        USER, "steps", day, day, session_factory=factory
    )
    assert (result.days[0].wear_hours, result.days[0].is_well_worn) == (0, False)
    assert result.summary.avg == 5000
    assert result.summary.total == 5000


async def test_failed_heart_is_unknown_even_when_steps_sync_advances(
    reads, monkeypatch
):
    factory, states, _, _ = reads
    states.return_value = [
        state(),
        state(
            metric="heart_rates",
            last_status="fail",
            last_success_at=NOW - timedelta(days=2),
        ),
    ]
    monkeypatch.setattr(
        service.repository,
        "list_hourly_hearts",
        AsyncMock(
            return_value=[
                HourlyHeartRecord(
                    date=TODAY, hourly_hr={str(hour): {} for hour in range(24)}
                )
            ]
        ),
    )
    result = await service.get_metric_result(
        USER, "steps", TODAY, TODAY, session_factory=factory
    )
    assert result.days[0].wear_hours is None
    assert result.days[0].is_well_worn is None


@pytest.mark.parametrize("hourly", [None, {}])
async def test_empty_stored_hours_count_as_zero(reads, monkeypatch, hourly):
    factory, states, _, _ = reads
    states.return_value = [state(), state(metric="heart_rates")]
    day = TODAY - timedelta(days=1)
    monkeypatch.setattr(
        service.repository,
        "list_hourly_hearts",
        AsyncMock(return_value=[HourlyHeartRecord(date=day, hourly_hr=hourly)]),
    )
    result = await service.get_metric_result(
        USER, "steps", day, day, session_factory=factory
    )
    assert (result.days[0].wear_hours, result.days[0].is_well_worn) == (0, False)


async def test_wear_query_failure_is_not_reported_as_no_wear(reads, monkeypatch):
    from app.health.errors import HealthDataLookupError

    factory, states, _, lifecycle = reads
    states.return_value = [state(), state(metric="heart_rates")]
    monkeypatch.setattr(
        service.repository,
        "list_hourly_hearts",
        AsyncMock(side_effect=OSError("synthetic")),
    )
    with pytest.raises(HealthDataLookupError):
        await service.get_metric_result(
            USER, "steps", TODAY, TODAY, session_factory=factory
        )
    assert lifecycle == ["transaction_closed", "session_closed"]
