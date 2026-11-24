from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.health.models import LifestyleScore
from app.health.repository import find_lifestyle_score
from app.health.rest_schemas import lifestyle_score_response
from app.health.schemas import LifestyleScoreRecord
from tests.health.test_score_contract import TODAY, stored_score

pytestmark = pytest.mark.db


async def test_score_read_is_scoped_materialized_and_readonly(health_db):
    factory, (owner, other) = health_db
    wanted = stored_score(end_user_id=owner)
    async with factory.begin() as session:
        session.add_all(
            [
                LifestyleScore(**wanted),
                LifestyleScore(**stored_score(end_user_id=other, total_score=99)),
                LifestyleScore(
                    **stored_score(
                        end_user_id=owner,
                        date=TODAY - timedelta(days=1),
                        total_score=42,
                    )
                ),
            ]
        )
    async with factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        result = await find_lifestyle_score(session, owner, TODAY)
        other_result = await find_lifestyle_score(session, other, TODAY)
        previous = await find_lifestyle_score(session, owner, TODAY - timedelta(days=1))
        absent = await find_lifestyle_score(session, owner, TODAY - timedelta(days=2))
        assert session.in_transaction()
    assert result == LifestyleScoreRecord(**wanted)
    assert other_result.end_user_id == other
    assert other_result.total_score == 99
    assert previous.total_score == 42
    assert absent is None
    # No ORM/session access is needed to serialize the complete public result.
    response = lifestyle_score_response(result, today=TODAY).model_dump(mode="json")
    assert response["total"]["score"] == 67
    assert response["areas"]["heart"]["baselineHrAvg"] == 58.0
    assert response["computedAt"] == "2026-10-26T03:15:00Z"


async def test_existing_null_score_row_is_not_absent(health_db):
    factory, (owner, _) = health_db
    async with factory.begin() as session:
        session.add(
            LifestyleScore(
                end_user_id=owner,
                date=TODAY,
                steps=0,
                hr_baseline_nights=0,
            )
        )
    async with factory() as session:
        result = await find_lifestyle_score(session, owner, TODAY)
    assert result is not None
    response = lifestyle_score_response(result, today=TODAY).model_dump(mode="json")
    assert response["total"] == {
        "score": None,
        "missingAreas": ["activity", "sleep", "heart"],
    }
    assert response["areas"]["activity"]["steps"]["value"] == 0
    assert response["areas"]["heart"]["checkWear"] is False


async def test_invalid_stored_score_is_not_returned_or_repaired(health_db):
    factory, (owner, _) = health_db
    async with factory.begin() as session:
        session.add(LifestyleScore(**stored_score(end_user_id=owner, total_score=101)))
    async with factory() as session:
        with pytest.raises(ValidationError):
            await find_lifestyle_score(session, owner, TODAY)
