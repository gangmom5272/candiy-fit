from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TemporaryStorageError
from app.users import repository, service
from app.users.schemas import AppConnection

NOW = datetime(2026, 11, 11, 6, tzinfo=UTC)


@pytest.mark.parametrize("status", ["UNKNOWN", "NOT_CONNECTED"])
def test_unconfirmed_setup_requires_no_completion_time(status):
    assert AppConnection(status=status, connected_at=None).connected_at is None
    with pytest.raises(ValidationError):
        AppConnection(status=status, connected_at=NOW)


def test_connected_requires_an_aware_time_and_normalizes_utc():
    for value in (None, NOW.replace(tzinfo=None)):
        with pytest.raises(ValidationError):
            AppConnection(status="CONNECTED", connected_at=value)
    local = NOW.astimezone(timezone(timedelta(hours=9)))
    result = AppConnection(status="CONNECTED", connected_at=local)
    assert result.connected_at.tzinfo == UTC
    with pytest.raises(ValidationError):
        AppConnection(status="BROKEN", connected_at=None)


@pytest.mark.parametrize(
    "failure,expected",
    [
        (ConnectionError("private commit uncertainty"), TemporaryStorageError),
        (ProgrammingError("private sql", {}, Exception()), ProgrammingError),
    ],
)
async def test_commit_failure_never_returns_completion(
    monkeypatch, make_user, failure, expected
):
    save = AsyncMock(
        return_value=make_user(app_connection_status="CONNECTED", app_connected_at=NOW)
    )
    monkeypatch.setattr(repository, "complete_app_connection", save)

    @asynccontextmanager
    async def transaction():
        yield
        raise failure

    db = Mock(spec=AsyncSession)
    db.begin.return_value = transaction()
    with pytest.raises(expected):
        await service.complete_app_connection(db, uuid4())
    assert save.await_count == 1


async def test_removed_member_cannot_report_completion(monkeypatch):
    monkeypatch.setattr(
        repository, "complete_app_connection", AsyncMock(return_value=None)
    )

    @asynccontextmanager
    async def transaction():
        yield

    db = Mock(spec=AsyncSession)
    db.begin.return_value = transaction()
    with pytest.raises(service.UserNotFoundError):
        await service.complete_app_connection(db, uuid4())
