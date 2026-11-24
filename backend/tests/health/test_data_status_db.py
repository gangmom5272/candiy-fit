from datetime import timedelta

import pytest
from sqlalchemy import text, update

from app.health import repository, service
from app.health.models import SyncState
from app.health.rest_schemas import data_status_response
from tests.health.test_calculations import NOW
from tests.health.test_data_status_response import METRICS

pytestmark = pytest.mark.db


async def test_states_are_user_scoped_ordered_and_preserve_history(
    status_db, monkeypatch
):
    factory, (owner, other) = status_db
    monkeypatch.setattr(service, "_now", lambda: NOW)
    async with factory.begin() as session:
        for raw_metric in (
            "steps",
            "heart_rates",
            "oxygen_saturations",
            "sleeps",
            "exercise_sessions",
        ):
            session.add(
                SyncState(
                    end_user_id=other,
                    metric=raw_metric,
                    initial_sync_done=True,
                    last_status="success",
                    last_data_at=NOW + timedelta(days=1),
                    last_success_at=NOW + timedelta(days=1),
                )
            )
        session.add_all(
            [
                SyncState(
                    end_user_id=owner,
                    metric="steps",
                    initial_sync_done=True,
                    last_status="success",
                    last_data_at=NOW,
                    last_success_at=NOW + timedelta(minutes=1),
                ),
                SyncState(
                    end_user_id=owner,
                    metric="oxygen_saturations",
                    initial_sync_done=False,
                    last_status="success",
                ),
                SyncState(
                    end_user_id=owner,
                    metric="sleeps",
                    initial_sync_done=True,
                    last_status="fail",
                    last_error="aggregate:RuntimeError",
                    last_data_at=NOW - timedelta(days=1),
                    last_success_at=NOW,
                ),
                SyncState(
                    end_user_id=owner,
                    metric="exercise_sessions",
                    initial_sync_done=True,
                    last_status="success",
                    last_success_at=NOW,
                ),
                SyncState(
                    end_user_id=owner,
                    metric="weights",
                    initial_sync_done=True,
                    last_status="success",
                    last_success_at=NOW,
                ),
            ]
        )
    result = await service.get_data_status(owner, session_factory=factory)
    assert result.user_id == owner and result.checked_at == NOW
    assert tuple(item.metric for item in result.metrics) == METRICS
    assert [item.ready for item in result.metrics] == [True, None, False, True, True]
    assert result.metrics[0].last_data_at == NOW
    assert result.metrics[1].last_sync_status is None
    assert result.metrics[2].last_success_at is None
    assert result.metrics[3].last_sync_status == "fail"
    assert result.metrics[3].last_data_at == NOW - timedelta(days=1)
    assert result.metrics[3].last_success_at == NOW
    assert result.metrics[4].last_data_at is None
    assert result.metrics[4].last_success_at == NOW
    response = data_status_response(result).model_dump(mode="json")
    assert response["firstRecord"] == {"status": "UNCONFIRMED", "firstArrivedAt": None}
    assert response["metrics"][3]["lastSyncStatus"] == "fail"
    assert response["metrics"][4]["metric"] == "exercises"
    assert set(response) == {"appConnection", "metrics", "firstRecord", "checkedAt"}


@pytest.mark.parametrize("failure", ["http_503", "aggregate:RuntimeError", "http_404"])
@pytest.mark.parametrize("ready", [False, True])
async def test_failed_attempt_keeps_previous_success_and_does_not_infer_unlinked(
    status_db, ready, failure
):
    factory, (owner, _) = status_db
    async with factory.begin() as session:
        session.add(
            SyncState(
                end_user_id=owner,
                metric="steps",
                initial_sync_done=ready,
                last_status="success" if ready else None,
                last_data_at=NOW if ready else None,
                last_success_at=NOW if ready else None,
            )
        )
    async with factory.begin() as session:
        await session.execute(
            update(SyncState)
            .where(SyncState.end_user_id == owner, SyncState.metric == "steps")
            .values(last_status="fail", last_error=failure)
        )
    result = await service.get_data_status(owner, session_factory=factory)
    item = result.metrics[0]
    assert item.ready is ready
    assert item.last_sync_status == "fail"
    assert item.last_data_at == item.last_success_at == (NOW if ready else None)


async def test_absent_states_and_successful_empty_sync_are_different(status_db):
    factory, (owner, other) = status_db
    async with factory.begin() as session:
        session.add(
            SyncState(
                end_user_id=other,
                metric="steps",
                initial_sync_done=True,
                last_status="success",
                last_success_at=NOW,
            )
        )
    absent = await service.get_data_status(owner, session_factory=factory)
    assert all(item.ready is None for item in absent.metrics)
    empty = await service.get_data_status(other, session_factory=factory)
    assert empty.metrics[0].ready is True
    assert empty.metrics[0].last_data_at is None
    assert empty.metrics[0].last_success_at == NOW


async def test_state_read_is_readonly_without_changing_pool_default(
    status_db, monkeypatch
):
    factory, (owner, _) = status_db
    original = repository.list_sync_states

    async def inspect_transaction(session, user_id):
        assert (
            await session.scalar(text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        return await original(session, user_id)

    monkeypatch.setattr(repository, "list_sync_states", inspect_transaction)
    await service.get_data_status(owner, session_factory=factory)
    async with factory() as session:
        assert (
            await session.scalar(text("SHOW transaction_isolation")) == "read committed"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
