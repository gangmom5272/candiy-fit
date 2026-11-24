import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import ProgrammingError

from app.health import service
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
)
from app.health.schemas import HourlyHeartRecord, MainSleepRecord
from tests.health.test_schemas import USER
from tests.health.test_service import reads, state  # noqa: F401 -- shared session fixture
from tests.health.test_timeline_contract import TODAY

SOURCES = {
    "steps": "list_hourly_steps",
    "heart_rates": "list_hourly_hearts",
    "sleeps": "list_main_sleeps",
    "exercises": "list_exercise_sessions",
}


@pytest.fixture
def timeline_reads(reads, monkeypatch):  # noqa: F811 -- pytest fixture
    factory, states, _, lifecycle = reads
    states.return_value = [
        state(metric=m) for m in ("steps", "heart_rates", "sleeps", "exercise_sessions")
    ]
    sources = {}
    for metric, name in SOURCES.items():
        sources[metric] = AsyncMock(return_value=[])
        monkeypatch.setattr(service.repository, name, sources[metric])
    return factory, states, sources, lifecycle


async def call(factory):
    return await service.get_timeline(
        USER, TODAY, TODAY, today=TODAY, session_factory=factory
    )


async def test_ready_empty_response_closes_transaction_and_reads_each_source_once(
    timeline_reads,
):
    factory, states, sources, lifecycle = timeline_reads
    states.return_value.append(
        state(metric="oxygen_saturations", initial_sync_done=False)
    )
    result = await call(factory)
    assert len(result.days) == 1 and result.days[0].is_in_progress
    assert lifecycle == ["transaction_closed", "session_closed"]
    for source in sources.values():
        source.assert_awaited_once()
        assert source.call_args.args[1:] == (USER, TODAY, TODAY)


async def test_all_states_404_precedes_readiness_and_queries(timeline_reads):
    factory, states, sources, _ = timeline_reads
    states.return_value = [state(last_error="http_404", initial_sync_done=False)]
    with pytest.raises(HealthDataNotLinkedError):
        await call(factory)
    assert all(source.await_count == 0 for source in sources.values())


async def test_all_member_states_used_for_link_check_and_recent_failure_is_not_unready(
    timeline_reads,
):
    factory, states, _, _ = timeline_reads
    states.return_value = [
        state(metric=m, last_error="http_404", last_status="fail")
        for m in ("steps", "heart_rates", "sleeps", "exercise_sessions")
    ]
    states.return_value.append(state(metric="weights", initial_sync_done=False))
    assert len((await call(factory)).days) == 1


@pytest.mark.parametrize("metric", list(SOURCES))
@pytest.mark.parametrize(
    "kind,expected",
    [
        ("missing", HealthDataLookupError),
        ("unready", HealthDataNotReadyError),
        ("no_success", HealthDataLookupError),
    ],
)
async def test_readiness_reports_public_metric_and_skips_data(
    timeline_reads, metric, kind, expected
):
    factory, states, sources, _ = timeline_reads
    stored_metric = "exercise_sessions" if metric == "exercises" else metric
    states.return_value = [s for s in states.return_value if s.metric != stored_metric]
    if kind != "missing":
        changes = (
            {"initial_sync_done": False}
            if kind == "unready"
            else {"last_success_at": None}
        )
        states.return_value.append(state(metric=stored_metric, **changes))
    with pytest.raises(expected) as caught:
        await call(factory)
    assert caught.value.metrics == (metric,)
    assert all(source.await_count == 0 for source in sources.values())


async def test_missing_state_precedes_another_unready_metric(timeline_reads):
    factory, states, sources, lifecycle = timeline_reads
    states.return_value = [
        state(metric="heart_rates"),
        state(metric="sleeps", initial_sync_done=False),
        state(metric="exercise_sessions"),
    ]
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory)
    assert caught.value.reason == "LOOKUP_FAILED"
    assert caught.value.metrics == ("steps",)
    for source in sources.values():
        source.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_unready_metric_precedes_another_missing_success_time(timeline_reads):
    factory, states, sources, lifecycle = timeline_reads
    states.return_value = [
        state(metric="steps", initial_sync_done=False),
        state(metric="heart_rates", last_success_at=None),
        state(metric="sleeps"),
        state(metric="exercise_sessions"),
    ]
    with pytest.raises(HealthDataNotReadyError) as caught:
        await call(factory)
    assert caught.value.reason == "DATA_NOT_READY"
    assert caught.value.metrics == ("steps",)
    for source in sources.values():
        source.assert_not_awaited()
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_multiple_unready_metrics_are_reported_together(timeline_reads):
    factory, states, _, _ = timeline_reads
    states.return_value = [
        state(metric=m, initial_sync_done=m not in ("steps", "exercise_sessions"))
        for m in ("steps", "heart_rates", "sleeps", "exercise_sessions")
    ]
    with pytest.raises(HealthDataNotReadyError) as caught:
        await call(factory)
    assert caught.value.metrics == ("steps", "exercises")


async def test_foreign_state_is_forbidden(timeline_reads):
    factory, states, _, _ = timeline_reads
    states.return_value.append(state(end_user_id=uuid4(), metric="weights"))
    with pytest.raises(HealthDataForbiddenError):
        await call(factory)


@pytest.mark.parametrize("metric", list(SOURCES))
@pytest.mark.parametrize("error", [OSError("private"), InvalidHealthData("private")])
async def test_source_failure_aborts_whole_response_with_known_metric(
    timeline_reads, metric, error
):
    factory, _, sources, lifecycle = timeline_reads
    sources[metric].side_effect = error
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory)
    assert caught.value.metrics == (metric,)
    assert lifecycle == ["transaction_closed", "session_closed"]


@pytest.mark.parametrize("metric", ["heart_rates", "sleeps"])
async def test_invalid_stored_json_is_lookup_failure_for_affected_metric(
    timeline_reads, metric
):
    factory, _, sources, _ = timeline_reads
    sources[metric].return_value = (
        [HourlyHeartRecord(date=TODAY, hourly_hr={"24": {}})]
        if metric == "heart_rates"
        else [
            MainSleepRecord(
                date=TODAY,
                start_at="2026-10-25T23:00:00+09:00",
                end_at="2026-10-26T06:00:00+09:00",
                sleep_timeline=[{"stage": "light"}],
            )
        ]
    )
    with pytest.raises(HealthDataLookupError) as caught:
        await call(factory)
    assert caught.value.metrics == (metric,)


async def test_total_timeout_covers_combined_queries(timeline_reads, monkeypatch):
    factory, _, sources, lifecycle = timeline_reads
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT_SECONDS", 0.03)

    async def slow(*args):
        await asyncio.sleep(0.02)
        return []

    for source in sources.values():
        source.side_effect = slow
    with pytest.raises(HealthDataLookupError):
        await call(factory)
    assert lifecycle == ["transaction_closed", "session_closed"]


async def test_programming_errors_are_not_disguised_as_transient_failure(
    timeline_reads,
):
    factory, _, sources, _ = timeline_reads
    sources["steps"].side_effect = ProgrammingError("private", {}, Exception("private"))
    with pytest.raises(ProgrammingError):
        await call(factory)
