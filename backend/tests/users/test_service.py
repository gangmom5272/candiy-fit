import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TemporaryStorageError
from app.users import repository, service


@pytest.mark.parametrize(
    "error,expected",
    [
        (ConnectionError("synthetic commit uncertainty"), TemporaryStorageError),
        (
            ProgrammingError("private sql", {}, Exception("private detail")),
            ProgrammingError,
        ),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_commit_failure_does_not_return_success_or_retry(
    monkeypatch, make_user, error, expected
):
    update = AsyncMock(return_value=make_user())
    monkeypatch.setattr(repository, "update_user", update)

    @asynccontextmanager
    async def transaction():
        yield
        raise error

    db = Mock(spec=AsyncSession)
    db.begin.return_value = transaction()
    with pytest.raises(expected):
        await service.update_current_profile(
            db, uuid4(), {"gender": "MALE"}, now=datetime.now(UTC)
        )
    assert update.await_count == 1
    db.begin.assert_called_once()
