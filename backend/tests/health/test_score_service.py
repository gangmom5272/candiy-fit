from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from app.health import service
from app.health.errors import (
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
)
from app.health.schemas import LifestyleScoreRecord, MissingLifestyleScore
from tests.health.test_schemas import USER
from tests.health.test_score_contract import TODAY, stored_score
from tests.health.test_service import reads, state  # noqa: F401


@pytest.fixture
def score_reads(reads, monkeypatch):  # noqa: F811 - imported pytest fixture
    factory, states, _, lifecycle = reads
    scores = AsyncMock(
        return_value=LifestyleScoreRecord(**stored_score(end_user_id=USER))
    )
    monkeypatch.setattr(service.repository, "find_lifestyle_score", scores)
    return factory, states, scores, lifecycle


async def call(factory):
    return await service.get_lifestyle_score(USER, TODAY, session_factory=factory)


async def test_all_404_precedes_score_and_closes_session(score_reads):
    factory, states, scores, lifecycle = score_reads
    states.return_value = [state(last_error="http_404", last_status="fail")]
    with pytest.raises(HealthDataNotLinkedError) as caught:
        await call(factory)
    assert caught.value.metrics == ()
    scores.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "states",
    [
        [],
        [state(initial_sync_done=False)],
        [
            state(last_error="http_404"),
            state(metric="weights", last_error=None),
        ],
    ],
)
async def test_stored_score_is_available_without_metric_readiness(score_reads, states):
    factory, state_read, scores, lifecycle = score_reads
    state_read.return_value = states
    assert await call(factory) == scores.return_value
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_absent_score_is_not_ready_with_empty_metric_context(score_reads):
    factory, _, scores, _ = score_reads
    scores.return_value = None
    with pytest.raises(HealthDataNotReadyError) as caught:
        await call(factory)
    assert caught.value.metrics == ()


@pytest.mark.parametrize("source", ["states", "scores"])
@pytest.mark.parametrize(
    "error", [OSError("synthetic"), InvalidHealthData("synthetic")]
)
async def test_failed_read_is_lookup_failure_not_missing_or_unlinked(
    score_reads, source, error
):
    factory, states, scores, lifecycle = score_reads
    (states if source == "states" else scores).side_effect = error
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory)
    assert caught.value.metrics == ()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_unexpected_programming_error_is_not_hidden(score_reads):
    factory, _, scores, _ = score_reads
    scores.side_effect = RuntimeError("synthetic bug")
    with pytest.raises(RuntimeError):
        await call(factory)


INPUT_METRICS = ("steps", "exercise_sessions", "sleeps", "heart_rates")


@pytest.mark.parametrize("metric", INPUT_METRICS)
@pytest.mark.parametrize("missing", [True, False])
async def test_absent_score_requires_each_input_state_ready(
    score_reads, metric, missing
):
    factory, states, scores, _ = score_reads
    scores.return_value = None
    states.return_value = [
        state(metric=m, initial_sync_done=m != metric)
        for m in INPUT_METRICS
        if not (missing and m == metric)
    ]
    with pytest.raises(HealthDataNotReadyError):
        await call(factory)


@pytest.mark.parametrize(
    "requested,expected",
    [
        (date(2026, 10, 9), "BEFORE_LINK"),
        (date(2026, 10, 10), "NO_RECORD"),
        (date(2026, 10, 19), "NO_RECORD"),
        (date(2026, 10, 20), "UNSYNCED"),
        (TODAY, "UNSYNCED"),
    ],
)
async def test_absent_score_reason_uses_all_metrics_and_kst(
    score_reads, requested, expected
):
    factory, states, scores, lifecycle = score_reads
    scores.return_value = None
    states.return_value = [
        state(
            metric=m,
            first_data_at=datetime(2026, 10, 12, tzinfo=UTC),
            last_data_at=datetime(2026, 10, 15, tzinfo=UTC),
            last_status="fail",
            last_error="http_503",
        )
        for m in INPUT_METRICS
    ] + [
        state(
            metric="weights",
            initial_sync_done=False,
            first_data_at=datetime(2026, 10, 9, 15, tzinfo=UTC),
            last_data_at=datetime(2026, 10, 19, 15, tzinfo=UTC),
        )
    ]
    result = await service.get_lifestyle_score(USER, requested, session_factory=factory)
    assert result == MissingLifestyleScore(
        end_user_id=USER, date=requested, reason=expected
    )
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize(
    "first,last,reason",
    [
        (None, None, "BEFORE_LINK"),
        (datetime(2026, 10, 1, tzinfo=UTC), None, "UNSYNCED"),
    ],
)
async def test_absent_score_with_no_boundary_uses_metric_rules(
    score_reads, first, last, reason
):
    factory, states, scores, _ = score_reads
    scores.return_value = None
    states.return_value = [
        state(metric=m, first_data_at=first, last_data_at=last) for m in INPUT_METRICS
    ]
    assert (await call(factory)).reason == reason


async def test_ready_input_without_success_time_is_lookup_failure(score_reads):
    factory, states, scores, _ = score_reads
    scores.return_value = None
    states.return_value = [state(metric=m, last_success_at=None) for m in INPUT_METRICS]
    with pytest.raises(HealthDataLookupError):
        await call(factory)
