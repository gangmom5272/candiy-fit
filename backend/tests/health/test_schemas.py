import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.health.schemas import HealthMetricResult, HealthQuery

TODAY = date(2026, 10, 22)
USER = UUID("00000000-0000-4000-8000-000000000001")


def query(**changes):
    values = dict(user_id=USER, metric="steps", start=TODAY, end=TODAY)
    return HealthQuery.model_validate(values | changes, context={"today": TODAY})


@pytest.mark.parametrize("length", [1, 366])
def test_query_accepts_inclusive_limit(length):
    assert query(start=TODAY - timedelta(days=length - 1)).end == TODAY


@pytest.mark.parametrize(
    "changes",
    [
        {"start": TODAY + timedelta(days=1)},
        {"end": TODAY + timedelta(days=1)},
        {"start": TODAY - timedelta(days=366)},
        {"metric": "heart_rate"},
        {"metric": "exercise_sessions"},
        {"user_id": "synth-01"},
    ],
)
def test_query_rejects_invalid_input(changes):
    with pytest.raises(ValidationError):
        query(**changes)


def test_query_accepts_old_short_period():
    assert query(start=date(2020, 1, 1), end=date(2020, 1, 2)).start.year == 2020


def test_public_result_excludes_owner_preserves_keys_and_json_numbers():
    result = HealthMetricResult(
        user_id=USER,
        metric="steps",
        period=dict(start=TODAY, end=TODAY),
        unit="steps",
        summary=dict(
            avg=Decimal("95.73"),
            daily_min=None,
            daily_max=None,
            total=None,
            days_with_data=0,
            days_used_for_summary=0,
            completed_days=0,
            missing_days=[],
            unsynced_days=[],
            linked_from=None,
        ),
        days=[],
        in_progress=None,
        data_status=dict(
            last_data_at=None,
            synced_until=None,
            last_sync_status=None,
            last_success_at=None,
            checked_at=datetime(2026, 10, 22, tzinfo=UTC),
        ),
        detail=dict(
            measured_min=None,
            measured_max=None,
            nights=[],
            daily_ranges=[],
            sessions=[],
        ),
    )
    public = json.loads(result.to_public().model_dump_json())
    assert "user_id" not in public
    assert result.user_id == USER
    assert set(public) == {
        "metric",
        "period",
        "unit",
        "summary",
        "days",
        "in_progress",
        "data_status",
        "detail",
    }
    assert public["summary"]["avg"] == 95.73
    assert public["data_status"]["checked_at"].endswith("Z")
    assert set(public["detail"]) == {
        "measured_min",
        "measured_max",
        "nights",
        "daily_ranges",
        "sessions",
    }
