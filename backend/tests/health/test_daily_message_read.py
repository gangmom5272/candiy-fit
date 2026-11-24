import asyncio

import pytest

from app.health.daily_message_read import (
    CleanupResult,
    ReadCleanupError,
    _resolve_error,
)
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)


@pytest.mark.parametrize(
    "original",
    [None, HealthDataLookupError(("sleeps",)), HealthDataNotReadyError(("sleeps",))],
)
def test_normal_cleanup_preserves_original_result(original):
    assert _resolve_error(original, CleanupResult(), ("sleeps",)) is original


@pytest.mark.parametrize(
    "original",
    [
        asyncio.CancelledError(),
        HealthDataForbiddenError(),
        HealthDataNotLinkedError(),
        RuntimeError("synthetic original"),
    ],
)
@pytest.mark.parametrize(
    "cleanup",
    [
        CleanupResult(error=OSError()),
        CleanupResult(
            error=RuntimeError(), driver_closed=True, pool_blocked=True, tasks_done=True
        ),
    ],
)
def test_whole_errors_and_cancellation_win_over_cleanup(original, cleanup):
    assert _resolve_error(original, cleanup, ("sleeps",)) is original


@pytest.mark.parametrize("metrics", [(), ("sleeps",)])
def test_known_cleanup_fault_with_complete_disposal_is_lookup_failure(metrics):
    cleanup = CleanupResult(
        error=OSError(), driver_closed=True, pool_blocked=True, tasks_done=True
    )
    error = _resolve_error(None, cleanup, metrics)
    assert isinstance(error, HealthDataLookupError)
    assert error.metrics == metrics


@pytest.mark.parametrize("missing", ["driver_closed", "pool_blocked", "tasks_done"])
def test_incomplete_disposal_is_whole_failure_even_when_score_only_failed(missing):
    evidence = dict(driver_closed=True, pool_blocked=True, tasks_done=True)
    evidence[missing] = False
    cleanup = CleanupResult(error=OSError(), **evidence)
    assert isinstance(_resolve_error(None, cleanup, ("sleeps",)), ReadCleanupError)


def test_unexpected_cleanup_fault_is_whole_error_even_after_disposal():
    cleanup = CleanupResult(
        error=ValueError(), driver_closed=True, pool_blocked=True, tasks_done=True
    )
    assert isinstance(_resolve_error(None, cleanup, ("sleeps",)), ReadCleanupError)


def test_disposal_error_is_whole_failure_even_when_cleanup_eventually_finishes():
    cleanup = CleanupResult(
        error=OSError(),
        disposal_error=TimeoutError(),
        driver_closed=True,
        pool_blocked=True,
        tasks_done=True,
    )
    assert isinstance(_resolve_error(None, cleanup, ("sleeps",)), ReadCleanupError)
