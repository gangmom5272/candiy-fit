from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.health.errors import HealthDataForbiddenError, InvalidHealthData
from app.health.repository import find_lifestyle_score
from tests.health.test_score_contract import OWNER, TODAY, stored_score


async def test_missing_score_returns_none_without_writing_or_closing_transaction():
    session = Mock(scalar=AsyncMock(return_value=None))
    assert await find_lifestyle_score(session, OWNER, TODAY) is None
    session.scalar.assert_awaited_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


async def test_other_owner_is_rejected_before_validating_payload():
    session = Mock(scalar=AsyncMock(return_value=SimpleNamespace(end_user_id=uuid4())))
    with pytest.raises(HealthDataForbiddenError):
        await find_lifestyle_score(session, OWNER, TODAY)


async def test_unexpected_date_is_rejected():
    row = SimpleNamespace(**stored_score(date=TODAY.replace(day=1)))
    with pytest.raises(InvalidHealthData):
        await find_lifestyle_score(
            Mock(scalar=AsyncMock(return_value=row)), OWNER, TODAY
        )


async def test_query_failure_is_not_a_missing_score():
    session = Mock(scalar=AsyncMock(side_effect=OSError("synthetic unavailable")))
    with pytest.raises(OSError):
        await find_lifestyle_score(session, OWNER, TODAY)
