from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.core.validation import validation_details
from app.health import rest_schemas
from app.health.rest_schemas import MetricPeriodRequest

TODAY = date(2026, 10, 26)


def period(**changes):
    return MetricPeriodRequest.model_validate(
        {"start": "2026-10-26", "end": "2026-10-26"} | changes,
        context={"today": TODAY},
    )


@pytest.mark.parametrize("days", [1, 366])
def test_period_accepts_inclusive_limit(days):
    start = TODAY - timedelta(days=days - 1)
    assert period(start=start.isoformat()).start == start


def test_period_has_no_recent_date_lower_bound():
    assert period(start="2000-01-01", end="2000-01-02").start == date(2000, 1, 1)


@pytest.mark.parametrize(
    "value",
    [
        "20261026",
        "2026-1-01",
        "2026-10-26T00:00:00Z",
        "1792972800",
        " 2026-10-26",
        "2026-02-29",
        "2024-02-30",
        "",
        "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff12\uff16",
    ],
)
@pytest.mark.parametrize("field", ["start", "end"])
def test_date_rejects_noncanonical_or_impossible_values(field, value):
    with pytest.raises(ValidationError) as raised:
        period(**{field: value})
    assert [
        detail.model_dump() for detail in validation_details(raised.value.errors())
    ] == [{"field": field, "reason": "invalid_format"}]


def test_date_accepts_real_leap_day():
    assert period(start="2024-02-29", end="2024-02-29").start == date(2024, 2, 29)


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"start": "2026-10-27"}, "start"),
        ({"end": "2026-10-27"}, "end"),
        ({"start": (TODAY - timedelta(days=366)).isoformat()}, "end"),
    ],
)
def test_period_range_errors_have_public_field_names(changes, field):
    with pytest.raises(ApiError) as raised:
        period(**changes)
    assert raised.value.status_code == 422
    assert raised.value.code == "INVALID_REQUEST"
    assert [d.model_dump() for d in raised.value.details] == [
        {"field": field, "reason": "out_of_range"}
    ]


def test_period_missing_fields_use_standard_validation_details():
    with pytest.raises(ValidationError) as raised:
        MetricPeriodRequest.model_validate({})
    assert [d.model_dump() for d in validation_details(raised.value.errors())] == [
        {"field": "start", "reason": "missing"},
        {"field": "end", "reason": "missing"},
    ]


@pytest.mark.parametrize(
    "instant,allowed",
    [
        (datetime(2026, 10, 25, 14, 59, 59, tzinfo=UTC), False),
        (datetime(2026, 10, 25, 15, tzinfo=UTC), True),
    ],
)
def test_period_uses_kst_midnight(monkeypatch, instant, allowed):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(rest_schemas, "datetime", Clock)
    if allowed:
        assert MetricPeriodRequest(start="2026-10-26", end="2026-10-26").end == TODAY
    else:
        with pytest.raises(ApiError):
            MetricPeriodRequest(start="2026-10-26", end="2026-10-26")


def test_period_openapi_contract_is_two_required_dates():
    schema = MetricPeriodRequest.model_json_schema()
    assert schema["required"] == ["start", "end"]
    assert schema["additionalProperties"] is False
    assert all(
        schema["properties"][field]["format"] == "date" for field in ("start", "end")
    )
