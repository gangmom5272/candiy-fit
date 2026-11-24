import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.health import daily_message_service as service
from app.health import repository
from app.health.calculations import DailyRecord
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
)
from app.health.schemas import Night
from tests.health.test_daily_message_repository import NOW, OWNER, TODAY


def sync(metric="steps", **changes):
    return repository.SyncRecord(
        **(
            dict(
                end_user_id=OWNER,
                metric=metric,
                initial_sync_done=True,
                first_data_at=NOW - timedelta(days=2),
                last_data_at=NOW,
                last_success_at=NOW,
                last_status="success",
                last_error=None,
            )
            | changes
        )
    )


def night(minutes=450):
    return Night(
        date=TODAY,
        start_at=NOW - timedelta(hours=8),
        end_at=NOW,
        sleep_minutes=minutes,
        deep_minutes=None,
        rem_minutes=None,
        light_minutes=None,
        awake_minutes=None,
        session_minutes=480,
        unspecified_minutes=None,
        unknown_minutes=None,
        avg_heart_rate=None,
        min_heart_rate=None,
        max_heart_rate=None,
        spo2_avg=None,
        spo2_min=None,
        spo2_max=None,
        spo2_count=None,
        spo2_low_count=None,
    )


@pytest.fixture
def reads(monkeypatch):
    states = AsyncMock(
        return_value=[sync("steps"), sync("exercise_sessions"), sync("sleeps")]
    )
    records = AsyncMock(return_value=[DailyRecord(TODAY, 8200)])
    monkeypatch.setattr(repository, "list_sync_states", states)
    monkeypatch.setattr(repository, "list_daily_records", records)
    return states, records


async def read(metric="steps", observe=None):
    return await service._read_metric(
        Mock(), OWNER, TODAY, metric, observe or (lambda _: None)
    )


@pytest.mark.parametrize("value,missing", [(8200, False), (0, True), (None, True)])
async def test_today_uses_daily_value_and_preserves_stored_zero(reads, value, missing):
    reads[1].return_value = [DailyRecord(TODAY, value)]
    result = await read()
    assert result.value == value and result.missing is missing
    assert result.has_stored_value is (value is not None)
    assert result.as_of == NOW
    query = reads[1].call_args.args[1]
    assert query.start == query.end == TODAY


async def test_before_link_does_not_hide_invalid_raw_value(reads):
    reads[0].return_value = [sync(first_data_at=NOW + timedelta(days=1))]
    reads[1].return_value = [DailyRecord(TODAY, -1)]
    with pytest.raises(InvalidHealthData):
        await read()
    reads[1].return_value = [DailyRecord(TODAY, 25)]
    result = await read()
    assert result.value is None and result.has_stored_value


@pytest.mark.parametrize("minutes", [None, 0, 450])
async def test_main_sleep_detail_is_independent_of_nullable_time(reads, minutes):
    reads[1].return_value = [DailyRecord(TODAY, minutes, night=night(minutes))]
    result = await read("sleeps")
    assert result.has_detail and result.value == minutes


@pytest.mark.parametrize(
    "record",
    [
        DailyRecord(TODAY, -1),
        DailyRecord(TODAY, True),
        DailyRecord(TODAY, 1.0),
        DailyRecord(TODAY - timedelta(days=1), 50),
    ],
)
async def test_bad_records_are_rejected_before_calculation(reads, record):
    reads[1].return_value = [record]
    with pytest.raises(InvalidHealthData):
        await read()


async def test_impossible_sleep_is_not_a_detail_fallback(reads):
    reads[1].return_value = [DailyRecord(TODAY, 481, night=night(481))]
    with pytest.raises(InvalidHealthData):
        await read("sleeps")


async def test_sync_failure_is_observed_before_unready_or_record_failure(reads):
    signals = []
    reads[0].return_value = [
        sync(last_status="fail", initial_sync_done=False, last_success_at=None)
    ]
    with pytest.raises(HealthDataNotReadyError):
        await read(observe=signals.append)
    assert signals == [True]
    reads[0].return_value = [sync(last_status="fail")]
    reads[1].side_effect = InvalidHealthData("synthetic")
    with pytest.raises(InvalidHealthData):
        await read(observe=signals.append)
    assert signals == [True, True]


async def test_all_states_are_used_for_link_but_other_readiness_is_not_required(reads):
    reads[0].return_value = [
        sync(last_error="http_404"),
        sync("weights", initial_sync_done=False),
    ]
    assert (await read()).value == 8200
    reads[0].return_value = [sync(last_error="http_404")]
    with pytest.raises(HealthDataNotLinkedError):
        await read()
    reads[0].return_value = [replace(sync(), end_user_id=OWNER.int)]
    with pytest.raises(HealthDataForbiddenError):
        await read()


async def test_unknown_readiness_is_invalid_instead_of_initial_sync_pending(reads):
    reads[0].return_value = [sync(initial_sync_done=None)]
    with pytest.raises(InvalidHealthData):
        await read()


@pytest.fixture
def blocks(monkeypatch):
    from app.health.daily_message_read import ReadBlockResult
    from app.health.lookup import lookup_boundary

    events = []
    faults = {}

    async def block(engine, read, *, request_id, metric=None, score_read=None):
        events.append((metric, "open"))
        async with lookup_boundary((metric,) if metric else ()):
            value = await read(Mock())
        score, error = None, None
        if score_read:
            try:
                async with lookup_boundary((metric,)):
                    score = await score_read(Mock(), value)
            except service.HealthDataLookupError as failure:
                error = failure
        events.append((metric, "closed"))
        if metric in faults:
            raise faults[metric]
        return ReadBlockResult(value, score, error, 0.0)

    monkeypatch.setattr(service, "read_daily_message_block", block)
    return events, faults


@pytest.fixture
def composed(reads, monkeypatch):
    async def records(session, query):
        return [
            DailyRecord(
                TODAY,
                {"steps": 8200, "exercises": 35, "sleeps": 450}[query.metric],
                night=night() if query.metric == "sleeps" else None,
            )
        ]

    reads[1].side_effect = records
    from tests.health.test_daily_message_repository import score_row

    score = AsyncMock(return_value=score_row(sleep_score=90))
    monkeypatch.setattr(repository, "find_sleep_score", score)
    return reads, score


async def compose():
    return await service.get_daily_message(
        OWNER, today=TODAY, request_id="synthetic", engine=Mock()
    )


async def test_composition_waits_for_each_block_before_selecting(blocks, composed):
    result = await compose()
    assert result.code == "BALANCED_RECORDS" and result.status == "READY"
    assert blocks[0] == [
        (m, e)
        for m in (None, "steps", "exercises", "sleeps")
        for e in ("open", "closed")
    ]


@pytest.mark.parametrize(
    "failure",
    [service.HealthDataLookupError(("sleeps",)), HealthDataNotReadyError(("sleeps",))],
)
async def test_known_metric_failure_is_partial_after_cleanup(blocks, composed, failure):
    blocks[1]["sleeps"] = failure
    result = await compose()
    assert result.status == "PARTIAL" and result.code == "ACTIVE_DAY"
    assert result.data_as_of.sleeps is None and result.score_computed_at is None
    assert result.notices[0].code == failure.reason


async def test_score_query_fault_preserves_sleep_only_after_normal_cleanup(
    blocks, composed
):
    composed[1].side_effect = OSError("synthetic-private")
    result = await compose()
    assert result.status == "READY" and result.code == "ACTIVE_DAY"
    assert result.data_as_of.sleeps == NOW and result.notices == ()


@pytest.mark.parametrize(
    "failure",
    [HealthDataForbiddenError(), HealthDataNotLinkedError(), RuntimeError("synthetic")],
)
async def test_whole_errors_stop_after_current_block(blocks, composed, failure):
    blocks[1]["steps"] = failure
    with pytest.raises(type(failure)):
        await compose()
    assert blocks[0][-1] == ("steps", "closed")
    composed[1].assert_not_awaited()


@pytest.mark.parametrize("minutes,queried", [(None, False), (0, True), (450, True)])
async def test_score_sql_is_gated_on_materialized_sleep_and_zero_is_valid(
    blocks, composed, minutes, queried
):
    original = composed[0][1].side_effect

    async def records(session, query):
        if query.metric == "sleeps":
            return [DailyRecord(TODAY, minutes, night=night(minutes))]
        return await original(session, query)

    composed[0][1].side_effect = records
    await compose()
    assert composed[1].await_count == int(queried)


async def test_verified_sync_failure_survives_unready_and_stays_request_local(
    blocks, composed
):
    composed[0][0].return_value = [
        sync("steps"),
        sync("exercise_sessions"),
        sync("sleeps", initial_sync_done=False, last_status="fail"),
    ]
    result = await compose()
    assert [(n.metric, n.code) for n in result.notices] == [
        ("sleeps", "DATA_NOT_READY"),
        ("sleeps", "SYNC_FAILED"),
    ]
    composed[0][0].return_value = [
        sync("steps"),
        sync("exercise_sessions"),
        sync("sleeps"),
    ]
    assert (await compose()).notices == ()


@pytest.mark.parametrize(
    "failure", [asyncio.CancelledError(), service.HealthDataLookupError(())]
)
async def test_common_failure_never_runs_metric_reads(blocks, composed, failure):
    blocks[1][None] = failure
    with pytest.raises(type(failure)):
        await compose()
    composed[0][1].assert_not_awaited()


async def test_cleanup_uncertainty_never_becomes_partial(blocks, composed):
    from app.health.daily_message_read import ReadCleanupError

    blocks[1]["sleeps"] = ReadCleanupError()
    with pytest.raises(ReadCleanupError):
        await compose()


async def test_score_owner_error_is_whole_forbidden(blocks, composed):
    composed[1].side_effect = HealthDataForbiddenError()
    with pytest.raises(HealthDataForbiddenError):
        await compose()
