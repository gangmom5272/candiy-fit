import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import errors, repository, schemas, service
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state
from tests.health.test_sleep_detail import detailed_night


def values(**changes):
    return (
        detailed_night().model_dump()
        | dict(session_id="main", is_main=True, computed_at=NOW)
        | changes
    )


def stored(**changes):
    return dict(end_user_id=USER) | values(**changes)


def query(start=TODAY - timedelta(days=1), end=TODAY):
    return schemas.SleepSessionsQuery.model_validate(
        dict(user_id=USER, start=start, end=end), context={"today": TODAY}
    )


async def read_rows(rows):
    result = Mock()
    result.mappings.return_value = rows
    db = Mock(execute=AsyncMock(return_value=result))
    return await repository.list_sleep_sessions(db, query())


async def call(factory, start=TODAY - timedelta(days=1), end=TODAY):
    return await service.get_sleep_sessions(USER, start, end, session_factory=factory)


@pytest.fixture
def sleep_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric="sleeps")]
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(repository, "list_sleep_sessions", rows, raising=False)
    return factory, states, rows, lifecycle


@pytest.mark.parametrize("length", [1, 31, 32])
async def test_period_limit_before_database_and_empty_list(sleep_reads, length):
    factory, states, _, _ = sleep_reads
    start = TODAY - timedelta(days=length - 1)
    if length == 32:
        with pytest.raises(ValidationError):
            await call(factory, start)
        states.assert_not_awaited()
    else:
        result = await call(factory, start)
        assert result.sessions == [] and result.period.start == start


@pytest.mark.parametrize(
    "start,end",
    [(TODAY, TODAY - timedelta(days=1)), (TODAY, TODAY + timedelta(days=1))],
)
async def test_invalid_dates_before_database(sleep_reads, start, end):
    factory, states, _, _ = sleep_reads
    with pytest.raises(ValidationError):
        await call(factory, start, end)
    states.assert_not_awaited()


@pytest.mark.parametrize(
    "field",
    [
        "sleep_minutes",
        "awake_minutes",
        "light_minutes",
        "deep_minutes",
        "rem_minutes",
        "unspecified_minutes",
        "unknown_minutes",
    ],
)
@pytest.mark.parametrize("value", [-1, True, "1", 1.5])
def test_minutes_require_nonnegative_strict_integer(field, value):
    with pytest.raises(ValidationError):
        schemas.SleepSessionDetail.model_validate(values(**{field: value}))


@pytest.mark.parametrize("value", [None, 0])
def test_partial_missing_and_zero_minutes_preserved_without_sum_constraint(value):
    detail = schemas.SleepSessionDetail.model_validate(
        values(
            sleep_minutes=value,
            awake_minutes=value,
            light_minutes=value,
            deep_minutes=value,
            rem_minutes=value,
            unspecified_minutes=value,
            unknown_minutes=value,
            spo2_avg=None,
            min_heart_rate=None,
        )
    )
    assert detail.sleep_minutes == value and detail.deep_minutes == value
    assert detail.spo2_avg is None and detail.spo2_count == 25
    assert detail.session_minutes == 480


@pytest.mark.parametrize("sleep_minutes", [None, 0, 60, 61, 400])
def test_sleep_minutes_cannot_exceed_session_minutes(sleep_minutes):
    data = values(session_minutes=60, sleep_minutes=sleep_minutes)
    data["start_at"] = data["end_at"] - timedelta(hours=1)
    if sleep_minutes is not None and sleep_minutes > 60:
        with pytest.raises(
            ValidationError, match="sleep minutes exceed session minutes"
        ):
            schemas.SleepSessionDetail.model_validate(data)
    else:
        detail = schemas.SleepSessionDetail.model_validate(data)
        assert detail.sleep_minutes == sleep_minutes


@pytest.mark.parametrize(
    "changes",
    [
        dict(session_id=""),
        dict(session_id="x" * 65),
        dict(is_main=1),
        dict(computed_at=NOW.replace(tzinfo=None)),
        dict(start_at=NOW, end_at=NOW),
        dict(date=TODAY),
        dict(spo2_avg=Decimal("99.1")),
        dict(avg_heart_rate=Decimal("81.6")),
        dict(spo2_low_count=26),
    ],
)
def test_invalid_identity_time_and_measurement_ranges(changes):
    with pytest.raises(ValidationError):
        schemas.SleepSessionDetail.model_validate(values(**changes))


def test_heart_rounding_tolerance_and_kst_midnight_end():
    end = datetime(2026, 10, 21, 15, tzinfo=UTC)
    detail = schemas.SleepSessionDetail.model_validate(
        values(
            date=TODAY,
            start_at=end - timedelta(hours=1),
            end_at=end,
            session_minutes=60,
            sleep_minutes=50,
            deep_minutes=10,
            rem_minutes=10,
            light_minutes=30,
            awake_minutes=10,
            avg_heart_rate=Decimal("60.5"),
            min_heart_rate=60,
            max_heart_rate=60,
        )
    )
    assert detail.date == TODAY and detail.avg_heart_rate == Decimal("60.5")


def test_all_nullable_keys_still_required():
    for field in schemas.SleepSessionDetail.model_fields:
        data = values()
        del data[field]
        with pytest.raises(ValidationError):
            schemas.SleepSessionDetail.model_validate(data)


async def test_sessions_preserved_and_only_sleep_status_used(sleep_reads):
    factory, states, rows, lifecycle = sleep_reads
    states.return_value = [
        state(
            metric="sleeps", last_status="fail", last_data_at=NOW - timedelta(days=2)
        ),
        state(metric="heart_rates", initial_sync_done=False, last_data_at=NOW),
    ]
    rows.return_value = [
        schemas.SleepSessionDetail.model_validate(values()),
        schemas.SleepSessionDetail.model_validate(
            values(session_id="other", is_main=False, sleep_minutes=None)
        ),
    ]
    result = await call(factory)
    assert [s.is_main for s in result.sessions] == [True, False]
    assert result.sessions[1].sleep_minutes is None
    payload = result.model_dump(mode="json")
    assert set(payload) == {"user_id", "period", "sessions", "data_status"}
    assert payload["sessions"][0]["avg_heart_rate"] == 58.3
    assert payload["sessions"][0]["computed_at"] == "2026-10-22T05:00:00Z"
    assert set(payload["data_status"]) == {
        "last_sync_status",
        "last_data_at",
        "last_success_at",
        "checked_at",
    }
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.last_data_at == NOW - timedelta(days=2)
    assert result.data_status.checked_at == NOW
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "states,expected",
    [
        ([], errors.HealthDataLookupError),
        ([state(metric="steps")], errors.HealthDataLookupError),
        (
            [state(metric="sleeps", initial_sync_done=False)],
            errors.HealthDataNotReadyError,
        ),
        ([state(metric="sleeps", last_success_at=None)], errors.HealthDataLookupError),
        (
            [state(metric="sleeps", end_user_id=uuid4())],
            errors.HealthDataForbiddenError,
        ),
        (
            [state(metric="sleeps", last_error="http_404")],
            errors.HealthDataNotLinkedError,
        ),
    ],
)
async def test_status_errors_precede_session_read(sleep_reads, states, expected):
    factory, source, rows, lifecycle = sleep_reads
    source.return_value = states
    with pytest.raises(expected) as caught:
        await call(factory)
    assert caught.value.metrics == ("sleeps",)
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("count", [1000, 1001])
async def test_limit_before_detail_or_main_validation(count):
    rows = [stored(session_id=str(i), is_main=i == 0) for i in range(count)]
    if count == 1001:
        rows[0]["deep_minutes"] = -1
        rows[0]["is_main"] = False
        with pytest.raises(errors.SleepSessionLimitError):
            await read_rows(rows)
    else:
        assert len(await read_rows(rows)) == 1000


@pytest.mark.parametrize("main_flags", [[False], [True, True]])
async def test_missing_or_duplicate_main_is_invalid(main_flags):
    with pytest.raises(errors.InvalidHealthData):
        await read_rows(
            [
                stored(session_id=str(i), is_main=flag)
                for i, flag in enumerate(main_flags)
            ]
        )


async def test_repository_owner_guard():
    with pytest.raises(errors.HealthDataForbiddenError):
        await read_rows([stored(end_user_id=uuid4())])


@pytest.mark.parametrize("kind", ["limit", "invalid", "io", "timeout", "bug", "cancel"])
async def test_error_classification_and_cleanup(sleep_reads, kind):
    factory, _, rows, lifecycle = sleep_reads
    failures = {
        "limit": errors.SleepSessionLimitError(),
        "invalid": errors.InvalidHealthData("synthetic"),
        "io": OSError("synthetic"),
        "timeout": TimeoutError(),
        "bug": RuntimeError("synthetic"),
        "cancel": asyncio.CancelledError(),
    }
    failure = failures[kind]
    rows.side_effect = failure
    expected = (
        type(failure)
        if kind in ("limit", "bug", "cancel")
        else errors.HealthDataLookupError
    )
    with pytest.raises(expected):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_actual_timeout_closes_transaction(sleep_reads, monkeypatch):
    factory, _, rows, lifecycle = sleep_reads
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    rows.side_effect = slow
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]
