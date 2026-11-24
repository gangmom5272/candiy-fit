from datetime import timedelta

import pytest

from app.health import repository, service
from app.health.models import RawHealthRecord
from app.users import service as user_service
from tests.health.test_calculations import NOW

pytestmark = pytest.mark.db


def raw_record(owner, metric, arrival, *, data_id="synthetic"):
    return RawHealthRecord(
        end_user_id=owner,
        metric=metric,
        data_id=data_id,
        app="synthetic-test",
        start_at=NOW - timedelta(days=30),
        payload={"synthetic_outlier": True},
        last_modified_at=NOW,
        source_created_at=arrival,
        source_updated_at=NOW,
    )


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps"]
)
async def test_arrival_uses_raw_source_time_even_before_aggregation(status_db, metric):
    factory, (owner, other) = status_db
    earliest = NOW - timedelta(days=2)
    async with factory.begin() as db:
        db.add_all(
            [
                raw_record(owner, metric, earliest),
                raw_record(owner, metric, NOW, data_id="later"),
                raw_record(owner, "exercise_sessions", NOW - timedelta(days=5)),
                raw_record(owner, "weights", NOW - timedelta(days=6)),
                raw_record(other, metric, NOW - timedelta(days=7)),
            ]
        )
    result = await service.get_data_status(owner, session_factory=factory)
    assert result.first_arrived_at == earliest
    assert all(item.ready is None for item in result.metrics)


async def test_exercise_only_and_no_records_are_unconfirmed(status_db):
    factory, (owner, other) = status_db
    async with factory.begin() as db:
        db.add(raw_record(owner, "exercise_sessions", NOW))
    for user in (owner, other):
        result = await service.get_data_status(user, session_factory=factory)
        assert result.first_arrived_at is None


async def test_first_arrival_and_states_share_one_read_snapshot(status_db, monkeypatch):
    factory, (owner, _) = status_db
    original = repository.list_sync_states

    async def read_then_insert(db, user_id):
        states = await original(db, user_id)
        async with factory.begin() as writer:
            writer.add(raw_record(owner, "steps", NOW))
        async with factory() as writer:
            await user_service.complete_app_connection(writer, owner)
        return states

    monkeypatch.setattr(repository, "list_sync_states", read_then_insert)
    result = await service.get_data_status(owner, session_factory=factory)
    assert result.first_arrived_at is None
    assert result.app_connection.status == "NOT_CONNECTED"
    monkeypatch.setattr(repository, "list_sync_states", original)
    next_result = await service.get_data_status(owner, session_factory=factory)
    assert next_result.first_arrived_at == NOW
    assert next_result.app_connection.status == "CONNECTED"
