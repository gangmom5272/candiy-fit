from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request
from sqlalchemy.exc import IntegrityError

from app.auth.contract import AuthenticatedUser
from app.core.errors import ApiError, TemporaryStorageError
from app.health.dependencies import get_health_user
from app.users import service


@pytest.fixture
def access(monkeypatch):
    user = AuthenticatedUser(uuid4())
    session = object()
    events = []

    @asynccontextmanager
    async def factory():
        events.append("open")
        try:
            yield session
        finally:
            events.append("closed")

    lookup = AsyncMock(return_value=SimpleNamespace(id=user.user_id))
    monkeypatch.setattr(service, "get_current_profile", lookup)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(sessionmaker=factory)),
        }
    )
    return request, user, session, events, lookup


async def test_access_keeps_authenticated_identity_and_closes_member_session(access):
    request, user, session, events, lookup = access
    assert await get_health_user(request, user) is user
    lookup.assert_awaited_once_with(session, user.user_id)
    assert events == ["open", "closed"]


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (service.UserNotFoundError(), 401, "AUTH_REQUIRED"),
        (TemporaryStorageError("synthetic-secret"), 503, "HEALTH_DATA_UNAVAILABLE"),
    ],
)
async def test_access_maps_expected_failure_and_closes_session(
    access, failure, status, code
):
    request, user, _, events, lookup = access
    lookup.side_effect = failure
    with pytest.raises(ApiError) as raised:
        await get_health_user(request, user)
    assert raised.value.status_code == status
    assert raised.value.code == code
    assert raised.value.context == (
        {"reason": "LOOKUP_FAILED", "metrics": []} if status == 503 else None
    )
    assert "synthetic-secret" not in raised.value.message
    assert events == ["open", "closed"]


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("synthetic-secret"), IntegrityError(None, None, Exception())],
)
async def test_access_does_not_hide_unexpected_failures(access, failure):
    request, user, _, events, lookup = access
    lookup.side_effect = failure
    with pytest.raises(type(failure)) as raised:
        await get_health_user(request, user)
    assert raised.value is failure
    assert events == ["open", "closed"]


@pytest.mark.parametrize("when", ["enter", "exit"])
async def test_access_handles_temporary_session_lifecycle_failure(access, when):
    request, user, session, _, lookup = access

    @asynccontextmanager
    async def unavailable_factory():
        if when == "enter":
            raise ConnectionError("synthetic-secret")
        yield session
        raise ConnectionError("synthetic-secret")

    request.app.state.sessionmaker = unavailable_factory
    with pytest.raises(ApiError) as raised:
        await get_health_user(request, user)
    assert raised.value.status_code == 503
    assert raised.value.context == {"reason": "LOOKUP_FAILED", "metrics": []}
    assert lookup.await_count == (0 if when == "enter" else 1)
