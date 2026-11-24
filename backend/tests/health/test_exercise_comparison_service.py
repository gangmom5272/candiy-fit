import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.health import service
from app.health.calculations import DailyRecord
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
    UnknownExerciseTypeError,
)
from tests.health import test_service
from tests.health.test_calculations import NOW, TODAY
from tests.health.test_exercise_comparison_calculations import activity, session
from tests.health.test_schemas import USER
from tests.health.test_service import state

reads = test_service.reads

DAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def codes(monkeypatch):
    lookup = AsyncMock(return_value={0, 56, 70, 79})
    monkeypatch.setattr(service.repository, "list_exercise_codes", lookup)
    return lookup


async def call(factory, metric="steps", start=DAY, end=DAY, **kwargs):
    return await service.get_exercise_record_comparison(
        USER, metric, start, end, session_factory=factory, **kwargs
    )


def ready(states, metric="steps"):
    states.return_value = [state(metric="exercise_sessions"), state(metric=metric)]


@pytest.mark.parametrize("metric", ["steps", "sleeps"])
async def test_reads_two_metrics_in_one_transaction_and_returns_public_values(
    reads, codes, metric
):
    factory, states, rows, lifecycle = reads
    ready(states, metric)
    sessions_seen = []

    async def daily(db, query):
        sessions_seen.append(db)
        assert lifecycle == []
        return (
            [activity(DAY, session(DAY))]
            if query.metric == "exercises"
            else [DailyRecord(DAY + timedelta(days=metric == "sleeps"), 200)]
        )

    rows.side_effect = daily
    result = await call(factory, metric)
    assert result.with_exercise.avg == 200 and result.with_exercise.days_with_data == 1
    assert not result.reliable and result.without_exercise.avg is None
    assert sessions_seen[0] is sessions_seen[1] is states.await_args.args[0]
    db = sessions_seen[0]
    assert codes.await_args.args[0] is db
    assert "REPEATABLE READ, READ ONLY" in str(db.execute.await_args.args[0])
    assert lifecycle == ["transaction_closed", "session_closed"]
    public = json.loads(result.to_public().model_dump_json())
    assert "user_id" not in public
    assert result.reliable_min_days == public["reliable_min_days"] == 3
    assert public["with_exercise"]["days_with_data"] == 1
    assert public["without_exercise"]["days_with_data"] == 0
    assert public["comparable"] is False and public["reliable"] is False


async def test_code_catalog_drives_grouping_and_lookup_failure_is_not_empty(
    reads, codes
):
    factory, states, rows, _ = reads
    ready(states)
    rows.side_effect = lambda db, query: (
        [activity(DAY, session(DAY, 999))]
        if query.metric == "exercises"
        else [DailyRecord(DAY, 200)]
    )
    result = await call(factory, exercise_type=0)
    assert result.with_exercise.days_with_data == 1
    codes.return_value = {0, 999}
    result = await call(factory, exercise_type=0)
    assert result.excluded_days[0].reason == "OTHER_EXERCISE"
    codes.side_effect = OSError("synthetic catalog failure")
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory, exercise_type=0)
    assert caught.value.metrics == ("exercises",)


@pytest.mark.parametrize("code", [999, -1])
async def test_unknown_requested_code_is_input_error_before_daily_reads(
    reads, codes, code
):
    factory, states, rows, lifecycle = reads
    ready(states)
    with pytest.raises(UnknownExerciseTypeError) as caught:
        await call(factory, exercise_type=code)
    assert isinstance(caught.value, ValueError)
    assert not isinstance(caught.value, HealthDataLookupError)
    assert str(caught.value) == caught.value.code == "UNKNOWN_EXERCISE_TYPE"
    assert codes.await_count == 1
    rows.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("code", [None, 0, 56])
async def test_registered_or_unspecified_request_keeps_normal_empty_result(reads, code):
    factory, states, rows, _ = reads
    ready(states)
    rows.return_value = []
    result = await call(factory, exercise_type=code)
    assert result.exercise_type == code
    assert not result.comparable


async def test_empty_catalog_allows_unspecified_but_rejects_specific_request(
    reads, codes
):
    factory, states, rows, _ = reads
    ready(states)
    codes.return_value = set()
    rows.return_value = []
    assert (await call(factory)).exercise_type is None
    rows.reset_mock()
    with pytest.raises(UnknownExerciseTypeError):
        await call(factory, exercise_type=0)
    rows.assert_not_awaited()


async def test_catalog_failure_precedes_unknown_input_rejection(reads, codes):
    factory, states, rows, _ = reads
    ready(states)
    codes.side_effect = OSError("synthetic catalog failure")
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory, exercise_type=999)
    assert caught.value.metrics == ("exercises",)
    rows.assert_not_awaited()


async def test_sleep_query_ends_today_and_today_only_skips_sleep_read(reads):
    factory, states, rows, _ = reads
    ready(states, "sleeps")
    rows.side_effect = lambda db, query: (
        [activity(DAY)] if query.metric == "exercises" else [DailyRecord(TODAY, 420)]
    )
    result = await call(factory, "sleeps", end=TODAY)
    queries = [args.args[1] for args in rows.await_args_list]
    assert [(q.metric, q.start, q.end) for q in queries] == [
        ("exercises", DAY, TODAY),
        ("sleeps", TODAY, TODAY),
    ]
    assert result.without_exercise.avg == 420
    rows.reset_mock()
    result = await call(factory, "sleeps", start=TODAY, end=TODAY)
    assert rows.await_count == 1 and result.metric_period is None
    assert result.excluded_days[0].reason == "IN_PROGRESS"


@pytest.mark.parametrize(
    "case,expected,metrics",
    [
        ("foreign", HealthDataForbiddenError, ("exercises", "steps")),
        ("unlinked", HealthDataNotLinkedError, ("exercises", "steps")),
        ("missing", HealthDataLookupError, ("steps",)),
        ("unready", HealthDataNotReadyError, ("steps",)),
        ("invalid", HealthDataLookupError, ("steps",)),
        ("both_missing", HealthDataLookupError, ("exercises", "steps")),
    ],
)
@pytest.mark.parametrize("exercise_type", [None, 999])
async def test_failure_priority_and_no_daily_reads(
    reads, codes, case, expected, metrics, exercise_type
):
    factory, states, rows, _ = reads
    states.return_value = [state(metric="exercise_sessions"), state()]
    if case == "foreign":
        states.return_value = [
            state(end_user_id=uuid4(), last_error="http_404", initial_sync_done=False)
        ]
    elif case == "unlinked":
        states.return_value = [state(last_error="http_404", initial_sync_done=False)]
    elif case == "missing":
        states.return_value = [
            state(metric="exercise_sessions", initial_sync_done=False)
        ]
    elif case == "unready":
        states.return_value = [
            state(metric="exercise_sessions", last_success_at=None),
            state(initial_sync_done=False),
        ]
    elif case == "invalid":
        states.return_value = [
            state(metric="exercise_sessions"),
            state(last_success_at=None),
        ]
    elif case == "both_missing":
        states.return_value = []
    with pytest.raises(expected) as caught:
        await call(factory, exercise_type=exercise_type)
    assert caught.value.metrics == metrics
    codes.assert_not_awaited()
    rows.assert_not_awaited()


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "heart_rates"},
        {"start": TODAY + timedelta(days=1)},
        {"exercise_type": True},
        {"exercise_type": "70"},
        {"exercise_type": 70.0},
    ],
)
async def test_invalid_input_does_not_open_transaction(reads, changes):
    factory, states, rows, lifecycle = reads
    with pytest.raises(ValidationError):
        await call(factory, **changes)
    assert not lifecycle
    states.assert_not_awaited()
    rows.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [OSError("synthetic private detail"), InvalidHealthData("corrupt aggregate")],
)
async def test_read_failure_is_not_empty_group_and_identifies_metric(reads, failure):
    factory, states, rows, lifecycle = reads
    ready(states)
    rows.side_effect = [[], failure]
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory)
    assert caught.value.metrics == ("steps",)
    assert str(caught.value) == "HEALTH_DATA_UNAVAILABLE"
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_recent_failure_and_nonrequested_state_keep_existing_member_boundaries(
    reads,
):
    factory, states, rows, _ = reads
    ready(states)
    states.return_value = [
        state(metric="exercise_sessions", last_status="fail", last_error="http_404"),
        state(),
        state(
            metric="weights",
            first_data_at=NOW - timedelta(days=100),
            last_data_at=NOW + timedelta(minutes=1),
            initial_sync_done=False,
        ),
    ]
    rows.side_effect = [[activity(DAY)], [DailyRecord(DAY, 100)]]
    result = await call(factory)
    assert result.linked_from == (NOW - timedelta(days=100)).date()
    assert result.exercise_data_status.last_sync_status == "fail"
    assert result.metric_data_status.synced_until == NOW + timedelta(minutes=1)
    assert [args.args[1].metric for args in rows.await_args_list] == [
        "exercises",
        "steps",
    ]


async def test_timeout_and_external_cancellation_release_transaction(
    reads, monkeypatch
):
    factory, states, _, lifecycle = reads
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    states.side_effect = wait
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]
    lifecycle.clear()
    entered.clear()
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 10)
    task = asyncio.create_task(call(factory))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lifecycle == ["transaction_closed", "session_closed"]
