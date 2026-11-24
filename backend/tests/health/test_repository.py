from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.health.errors import HealthDataForbiddenError
from app.health.repository import list_daily_records, list_sync_states
from tests.health.test_schemas import USER, query


@pytest.mark.parametrize("read", ["states", "daily"])
async def test_wrong_owner_in_returned_rows_is_rejected(read):
    result = Mock()
    result.mappings.return_value = [{"end_user_id": uuid4()}]
    session = Mock(execute=AsyncMock(return_value=result))
    with pytest.raises(HealthDataForbiddenError):
        if read == "states":
            await list_sync_states(session, USER)
        else:
            await list_daily_records(session, query())
