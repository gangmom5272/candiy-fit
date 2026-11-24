import pytest
from sqlalchemy import text, update

from app.health import repository, service
from app.health.errors import HealthDataNotLinkedError
from app.health.models import LifestyleScore, SyncState
from tests.health.test_score_contract import TODAY, stored_score

pytestmark = pytest.mark.db


async def test_score_and_link_status_share_readonly_snapshot(health_db, monkeypatch):
    factory, (owner, _) = health_db
    async with factory.begin() as session:
        session.add(LifestyleScore(**stored_score(end_user_id=owner)))
        session.add(SyncState(end_user_id=owner, metric="steps"))
    original = repository.list_sync_states

    async def change_after_state(session, user_id):
        states = await original(session, user_id)
        assert (
            await session.scalar(text("SHOW transaction_isolation"))
            == "repeatable read"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        async with factory.begin() as writer:
            await writer.execute(
                update(LifestyleScore)
                .where(
                    LifestyleScore.end_user_id == owner,
                )
                .values(total_score=99)
            )
            await writer.execute(
                update(SyncState)
                .where(
                    SyncState.end_user_id == owner,
                )
                .values(last_status="fail", last_error="http_404")
            )
        return states

    monkeypatch.setattr(repository, "list_sync_states", change_after_state)
    result = await service.get_lifestyle_score(owner, TODAY, session_factory=factory)
    assert result.total_score == 67
    monkeypatch.setattr(repository, "list_sync_states", original)
    with pytest.raises(HealthDataNotLinkedError):
        await service.get_lifestyle_score(owner, TODAY, session_factory=factory)
    async with factory() as session:
        assert (
            await session.scalar(text("SHOW transaction_isolation")) == "read committed"
        )
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
