import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import errors, repository, schemas, service
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_schemas import USER
from tests.health.test_service import reads as reads
from tests.health.test_service import state


def values(**changes):
    return (
        dict(
            record_id="synthetic-step",
            date=TODAY,
            start_at=NOW,
            end_at=None,
            step_count=1200,
            source_app="synthetic.app",
            last_modified_at=NOW,
            ingested_at=NOW,
        )
        | changes
    )


async def call(factory, history=False, **changes):
    args = dict(user_id=USER, start=TODAY, end=TODAY, session_factory=factory) | changes
    if history:
        return await service.get_step_record_history(record_id="synthetic-step", **args)
    return await service.get_step_records(**args)


@pytest.fixture
def step_reads(reads, monkeypatch):
    factory, states, _, lifecycle = reads
    states.return_value = [state(metric="steps")]
    listing = AsyncMock(return_value=[])
    record = AsyncMock(return_value=None)
    versions = AsyncMock(return_value=[])
    monkeypatch.setattr(repository, "list_step_records", listing, raising=False)
    monkeypatch.setattr(repository, "find_step_record", record, raising=False)
    monkeypatch.setattr(
        repository, "list_step_record_versions", versions, raising=False
    )
    return factory, states, listing, record, versions, lifecycle


@pytest.mark.parametrize("end", [None, NOW + timedelta(seconds=1)])
@pytest.mark.parametrize("count", [0, 1, 1000000])
def test_step_record_optional_end_and_count(end, count):
    record = schemas.StepRawRecord(**values(end_at=end, step_count=count))
    assert record.end_at == end and record.step_count == count
    assert "end_at" in record.model_dump(mode="json")


@pytest.mark.parametrize(
    "changes",
    [
        {"end_at": NOW},
        {"end_at": NOW - timedelta(seconds=1)},
        {"step_count": -1},
        {"step_count": 1000001},
        {"step_count": 1.5},
        {"step_count": True},
        {"step_count": "1"},
        {"step_count": None},
        {"record_id": ""},
        {"record_id": "x" * 65},
        {"source_app": ""},
        {"source_app": "x" * 129},
        {"date": TODAY - timedelta(days=1)},
        {"start_at": NOW.replace(tzinfo=None)},
        {"last_modified_at": None},
    ],
)
def test_invalid_record(changes):
    with pytest.raises(ValidationError):
        schemas.StepRawRecord(**values(**changes))


def test_all_record_keys_required():
    for field in values():
        data = values()
        del data[field]
        with pytest.raises(ValidationError):
            schemas.StepRawRecord(**data)


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "bad"},
        {"start": TODAY + timedelta(days=1)},
        {"end": TODAY + timedelta(days=1)},
        {"end": TODAY - timedelta(days=1)},
        {"start": TODAY - timedelta(days=31)},
        {"start": date.min, "end": date.min},
    ],
)
async def test_invalid_request_before_db(step_reads, history, changes):
    factory, states, _, _, _, lifecycle = step_reads
    with pytest.raises(ValidationError):
        await call(factory, history, **changes)
    states.assert_not_awaited()
    assert lifecycle == []


@pytest.mark.parametrize("identifier", ["", "x" * 65, None])
async def test_invalid_record_id_before_db(step_reads, identifier):
    factory, states, _, _, _, _ = step_reads
    with pytest.raises(ValidationError):
        await service.get_step_record_history(
            USER, identifier, TODAY, TODAY, session_factory=factory
        )
    states.assert_not_awaited()


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("length", [1, 31])
async def test_empty_period_and_current_state(step_reads, history, length):
    factory, states, _, find, _, lifecycle = step_reads
    states.return_value = [
        state(
            metric="steps", last_status="fail", last_success_at=NOW - timedelta(days=1)
        ),
        state(metric="heart_rates", initial_sync_done=False),
    ]
    find.return_value = schemas.StepRawRecord(**values())
    result = await call(factory, history, start=TODAY - timedelta(days=length - 1))
    assert result.data_status.last_sync_status == "fail"
    assert result.data_status.checked_at == NOW
    assert result.unit == "steps"
    assert result.metric == "steps"
    if history:
        assert result.versions == []
        assert result.record.ingested_at > result.data_status.last_success_at
        assert result.record.end_at is None
    else:
        assert result.records == []
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize(
    "kind", ["missing", "unready", "no_success", "unlinked", "foreign"]
)
async def test_state_errors_precede_record_lookup(step_reads, history, kind):
    factory, states, listing, find, versions, lifecycle = step_reads
    states.return_value = {
        "missing": [],
        "unready": [state(initial_sync_done=False)],
        "no_success": [state(last_success_at=None)],
        "unlinked": [state(last_error="http_404")],
        "foreign": [state(end_user_id=uuid4())],
    }[kind]
    expected = {
        "unready": errors.HealthDataNotReadyError,
        "unlinked": errors.HealthDataNotLinkedError,
        "foreign": errors.HealthDataForbiddenError,
    }.get(kind, errors.HealthDataLookupError)
    with pytest.raises(expected) as caught:
        await call(factory, history)
    assert caught.value.metrics == ("steps",)
    listing.assert_not_awaited()
    find.assert_not_awaited()
    versions.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_missing_record_does_not_read_versions(step_reads):
    factory, _, _, _, versions, lifecycle = step_reads
    with pytest.raises(errors.StepRecordNotFoundError) as caught:
        await call(factory, True)
    assert caught.value.code == "STEP_RECORD_NOT_FOUND"
    versions.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("kind", ["invalid", "io", "bug", "cancel", "limit"])
async def test_error_classification_and_cleanup(step_reads, history, kind):
    factory, _, listing, find, versions, lifecycle = step_reads
    find.return_value = schemas.StepRawRecord(**values())
    limit = errors.StepHistoryLimitError if history else errors.StepRecordLimitError
    failure = {
        "invalid": errors.InvalidHealthData("synthetic"),
        "io": OSError("synthetic"),
        "bug": RuntimeError("synthetic"),
        "cancel": asyncio.CancelledError(),
        "limit": limit(),
    }[kind]
    (versions if history else listing).side_effect = failure
    expected = (
        type(failure)
        if kind in ("bug", "cancel", "limit")
        else errors.HealthDataLookupError
    )
    with pytest.raises(expected) as caught:
        await call(factory, history)
    if expected is errors.HealthDataLookupError:
        assert caught.value.metrics == ("steps",)
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("history", [False, True])
async def test_timeout_cleans_up(step_reads, monkeypatch, history):
    factory, _, listing, find, versions, lifecycle = step_reads
    find.return_value = schemas.StepRawRecord(**values())
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    (versions if history else listing).side_effect = slow
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(errors.HealthDataLookupError):
        await call(factory, history)
    assert cancelled == [True]
    assert lifecycle == ["transaction_closed", "session_closed"]
