from datetime import timedelta

import pytest
from sqlalchemy import text

from app.health import repository
from app.health.daily_message_service import classify_sleep_score
from app.health.models import LifestyleScore
from tests.health.test_score_contract import TODAY, stored_score

NOW = stored_score()["computed_at"]

pytestmark = pytest.mark.db


async def test_sleep_projection_is_scoped_and_ignores_invalid_other_areas(health_db):
    factory, (owner, other) = health_db
    async with factory.begin() as session:
        session.add_all(
            [
                LifestyleScore(
                    **stored_score(
                        end_user_id=owner,
                        sleep_minutes=450,
                        sleep_score=80,
                        total_score=101,
                    )
                ),
                LifestyleScore(**stored_score(end_user_id=other, sleep_score=99)),
                LifestyleScore(
                    **stored_score(
                        end_user_id=owner,
                        date=TODAY - timedelta(days=1),
                        sleep_score=42,
                    )
                ),
            ]
        )
    async with factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        row = await repository.find_sleep_score(session, owner, TODAY)
        absent = await repository.find_sleep_score(
            session, owner, TODAY - timedelta(days=2)
        )
        assert session.in_transaction()
    assert absent is None
    assert row.end_user_id == owner and row.date == TODAY
    usable = classify_sleep_score(row, user_id=owner, today=TODAY, sleep_minutes=450)
    assert usable.value == 80 and usable.computed_at == NOW
