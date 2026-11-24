import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.tokens import issue_access_token
from app.core.db import get_session
from app.health.models import SyncState
from app.users import repository
from app.users.models import User

pytestmark = pytest.mark.db


@pytest.fixture
def db_api(app, settings, user_database):
    factory, members = user_database

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session

    def headers(user):
        token = issue_access_token(user.id, settings, now=datetime.now(UTC)).token
        return {"Authorization": "Bearer " + token}

    return factory, members, headers


async def test_get_real_database_contract(client, db_api):
    _, members, headers = db_api
    for user in members:
        response = await client.get("/api/v1/users/me", headers=headers(user))
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == data["endUserId"] == str(user.id)
        assert data["heightCm"] == 165.25 and isinstance(data["heightCm"], float)
        assert data["weightKg"] == 55 and not isinstance(data["weightKg"], str)
        assert data["createdAt"] == "2026-01-01T00:00:00Z"
        assert "socialUserId" not in data and "social_user_id" not in data


async def test_patch_commit_omission_identity_and_first_data_preserved(client, db_api):
    factory, (user, other), headers = db_api
    first = user.created_at + timedelta(days=1)
    async with factory.begin() as db:
        db.add(SyncState(end_user_id=user.id, metric="steps", first_data_at=first))
    response = await client.patch(
        "/api/v1/users/me",
        content='{"displayName":"  가  ","heightCm":165.250}',
        headers=headers(user) | {"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["displayName"] == "가" and data["heightCm"] == 165.25
    assert len(data) == 9 and data["weightKg"] == 55
    async with factory() as observer:
        saved = await observer.get(User, user.id)
        untouched = await observer.get(User, other.id)
        state = await observer.get(SyncState, (user.id, "steps"))
        assert saved.display_name == "가" and saved.height_cm == Decimal("165.25")
        assert saved.weight_kg == user.weight_kg and saved.birth_year == user.birth_year
        assert saved.id == user.id and saved.social_user_id == user.social_user_id
        assert (
            saved.social_type == user.social_type
            and saved.created_at == user.created_at
        )
        assert saved.updated_at > user.updated_at
        assert untouched.display_name == other.display_name
        assert state.first_data_at == first
    fetched = await client.get("/api/v1/users/me", headers=headers(user))
    assert fetched.json() == response.json()


@pytest.mark.parametrize(
    "field,column,literal",
    [("heightCm", "height_cm", "166.25"), ("weightKg", "weight_kg", "56.75")],
)
async def test_long_trailing_zeros_patch_database_get_roundtrip(
    client, db_api, field, column, literal
):
    factory, (user, _), headers = db_api
    response = await client.patch(
        "/api/v1/users/me",
        content='{"' + field + '":' + literal + "0" * 20000 + "}",
        headers=headers(user) | {"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["data"][field] == float(literal)
    async with factory() as observer:
        saved = await observer.get(User, user.id)
        assert getattr(saved, column) == Decimal(literal)
    fetched = await client.get("/api/v1/users/me", headers=headers(user))
    assert fetched.status_code == 200
    assert fetched.json() == response.json()
    assert isinstance(fetched.json()["data"][field], (int, float))


async def test_duplicate_nickname_is_allowed(client, db_api):
    _, (first, second), headers = db_api
    for user in (first, second):
        response = await client.patch(
            "/api/v1/users/me",
            json={"displayName": "동일닉네임"},
            headers=headers(user),
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"displayName": "바뀌면안됨", "heightCm": 300.01},
        {"gender": "MALE", "weightKg": 55.251},
    ],
)
async def test_invalid_patch_changes_nothing(client, db_api, payload):
    factory, (user, _), headers = db_api
    response = await client.patch(
        "/api/v1/users/me", json=payload, headers=headers(user)
    )
    assert response.status_code == 422
    async with factory() as observer:
        saved = await observer.get(User, user.id)
        assert saved.display_name == user.display_name
        assert saved.gender == user.gender and saved.weight_kg == user.weight_kg
        assert saved.updated_at == user.updated_at


@pytest.mark.parametrize("failure", ["bug", "constraint", "connection"])
async def test_real_update_rollback_on_later_failure(
    client, db_api, monkeypatch, failure
):
    factory, (user, _), headers = db_api
    original = repository.update_user
    constraint_failures = []

    async def fail_after_update(db, user_id, changes, *, now):
        await original(db, user_id, changes, now=now)
        # 다른 연결에서는 커밋 전 변경이 보이지 않아야 한다.
        async with factory() as observer:
            previous = await observer.get(User, user_id)
            assert previous.display_name == user.display_name
        if failure == "constraint":
            try:
                await original(db, user_id, {"height_cm": Decimal("0")}, now=now)
            except IntegrityError as error:
                constraint_failures.append(error.orig.sqlstate)
                raise
        if failure == "connection":
            raise ConnectionError("synthetic private failure after update")
        raise RuntimeError("synthetic private failure after update")

    monkeypatch.setattr(repository, "update_user", fail_after_update)
    response = await client.patch(
        "/api/v1/users/me",
        json={"displayName": "롤백대상", "gender": "MALE"},
        headers=headers(user),
    )
    assert response.status_code == (503 if failure == "connection" else 500)
    if failure == "constraint":
        assert constraint_failures == ["23514"]
    async with factory() as observer:
        saved = await observer.get(User, user.id)
        assert saved.display_name == user.display_name and saved.gender == user.gender
        assert saved.updated_at == user.updated_at


async def test_two_concurrent_partial_updates_preserve_both_fields(
    client, db_api, monkeypatch
):
    factory, (user, _), headers = db_api
    original = repository.update_user
    both_entered = asyncio.Event()
    arrived = 0

    async def coordinated(db, user_id, changes, *, now):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        return await original(db, user_id, changes, now=now)

    monkeypatch.setattr(repository, "update_user", coordinated)
    responses = await asyncio.gather(
        client.patch(
            "/api/v1/users/me", json={"displayName": "동시수정"}, headers=headers(user)
        ),
        client.patch(
            "/api/v1/users/me", json={"weightKg": 60.25}, headers=headers(user)
        ),
    )
    assert [r.status_code for r in responses] == [200, 200]
    async with factory() as observer:
        saved = await observer.get(User, user.id)
        assert saved.display_name == "동시수정" and saved.weight_kg == Decimal("60.25")
        assert saved.height_cm == user.height_cm


async def test_missing_member_patch_is_auth_required(client, db_api, settings):
    token = issue_access_token(uuid4(), settings, now=datetime.now(UTC)).token
    response = await client.patch(
        "/api/v1/users/me",
        json={"gender": "MALE"},
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
