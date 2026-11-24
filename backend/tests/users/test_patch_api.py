from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.auth import tokens
from app.core.db import get_session
from app.core.errors import TemporaryStorageError
from app.users import service

NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.fixture
def patch_api(app, settings, make_user, monkeypatch):
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    user = make_user()
    update = AsyncMock(return_value=user)
    monkeypatch.setattr(service, "update_current_profile", update, raising=False)
    db = AsyncMock()

    async def session():
        yield db

    app.dependency_overrides[get_session] = session
    headers = {
        "Authorization": "Bearer "
        + tokens.issue_access_token(user.id, settings, now=NOW).token,
        "Content-Type": "application/json",
    }
    return user, db, update, headers


@pytest.mark.parametrize(
    "raw,changes",
    [
        ('{"displayName":"  가  "}', {"display_name": "가"}),
        ('{"birthYear":2000}', {"birth_year": 2000}),
        ('{"gender":"MALE"}', {"gender": "MALE"}),
        ('{"heightCm":165.250}', {"height_cm": Decimal("165.250")}),
        ('{"weightKg":55}', {"weight_kg": Decimal("55")}),
        (
            '{"heightCm":1.6525e2,"weightKg":55.00}',
            {"height_cm": Decimal("165.25"), "weight_kg": Decimal("55")},
        ),
    ],
)
async def test_patch_passes_only_validated_changes(client, patch_api, raw, changes):
    user, db, update, headers = patch_api
    response = await client.patch(
        "/api/v1/users/me", content=raw, headers=headers | {"X-User-Id": str(uuid4())}
    )
    assert response.status_code == 200
    assert update.await_args.args == (db, user.id, changes)
    assert response.json()["data"]["id"] == str(user.id)
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers


@pytest.mark.parametrize(
    "raw,field,reason",
    [
        ("{}", "body", "empty"),
        ("null", "body", "null_not_allowed"),
        ("", "body", "missing"),
        ("[]", "body", "invalid_type"),
        ('{"socialType":null}', "socialType", "extra_forbidden"),
        ('{"userId":"other"}', "userId", "extra_forbidden"),
        ('{"display_name":"이름"}', "display_name", "extra_forbidden"),
        ('{"heightCm":true}', "heightCm", "invalid_type"),
        ('{"weightKg":"55"}', "weightKg", "invalid_type"),
        ('{"birthYear":2000.0}', "birthYear", "invalid_type"),
        ('{"heightCm":165.25000000000000001}', "heightCm", "decimal_places"),
        (
            '{"weightKg":0.010000000000000000000000000000000000001}',
            "weightKg",
            "decimal_places",
        ),
        ('{"heightCm":1e-999}', "heightCm", "decimal_places"),
        ('{"heightCm":300.00000000000000001}', "heightCm", "out_of_range"),
        ('{"heightCm":NaN}', "body", "invalid_json"),
        ('{"weightKg":Infinity}', "body", "invalid_json"),
        ('{"heightCm":1,"heightCm":2}', "heightCm", "duplicate"),
        ('{"displayName":"\\ud800"}', "body", "invalid_json"),
        ('{"gender":"male"}', "gender", "invalid_choice"),
    ],
)
async def test_patch_rejection_before_storage(client, patch_api, raw, field, reason):
    _, _, update, headers = patch_api
    response = await client.patch("/api/v1/users/me", content=raw, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    update.assert_not_awaited()


@pytest.mark.parametrize(
    "field", ["displayName", "birthYear", "gender", "heightCm", "weightKg"]
)
async def test_patch_each_null_is_rejected(client, patch_api, field):
    _, _, update, headers = patch_api
    response = await client.patch(
        "/api/v1/users/me", json={field: None}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": field, "reason": "null_not_allowed"}
    ]
    update.assert_not_awaited()


async def test_patch_multiple_errors_collected(client, patch_api):
    _, _, update, headers = patch_api
    response = await client.patch(
        "/api/v1/users/me",
        json={
            "displayName": "\n이름",
            "birthYear": 1899,
            "gender": "other",
            "heightCm": 0,
            "weightKg": 500.01,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert len(response.json()["error"]["details"]) == 5
    update.assert_not_awaited()


@pytest.mark.parametrize(
    "mode,code",
    [
        ("missing", "AUTH_REQUIRED"),
        ("invalid", "AUTH_REQUIRED"),
        ("expired", "AUTH_EXPIRED"),
    ],
)
async def test_patch_authentication(client, patch_api, monkeypatch, mode, code):
    _, _, update, headers = patch_api
    if mode == "missing":
        headers.pop("Authorization")
    elif mode == "invalid":
        headers["Authorization"] = "Bearer invalid"
    else:
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
    response = await client.patch(
        "/api/v1/users/me", json={"gender": "MALE"}, headers=headers
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == code
    assert response.headers["www-authenticate"] == "Bearer"
    update.assert_not_awaited()


@pytest.mark.parametrize(
    "error,status,code",
    [
        (service.UserNotFoundError(), 401, "AUTH_REQUIRED"),
        (TemporaryStorageError("private storage"), 503, "USER_UNAVAILABLE"),
        (RuntimeError("private bug"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_patch_service_errors(client, patch_api, error, status, code, caplog):
    _, _, update, headers = patch_api
    update.side_effect = error
    response = await client.patch(
        "/api/v1/users/me", json={"gender": "MALE"}, headers=headers
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "retryable" not in response.json()["error"]
    assert "private" not in response.text + caplog.text
    assert (
        "x-request-id" in response.headers
        and response.headers["cache-control"] == "no-store"
    )


async def test_patch_undeclared_query_and_wrong_content_type(client, patch_api):
    _, _, update, headers = patch_api
    for url, content_type in [
        ("/api/v1/users/me?userId=other", "application/json"),
        ("/api/v1/users/me", "text/plain"),
    ]:
        response = await client.patch(
            url,
            content='{"gender":"MALE"}',
            headers=headers | {"Content-Type": content_type},
        )
        assert response.status_code == 422
    update.assert_not_awaited()


async def test_patch_openapi_body_is_optional_per_field_but_not_nullable(client):
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/api/v1/users/me"]["patch"]
    assert operation["requestBody"]["required"] is True
    body = schema["components"]["schemas"]["UpdateProfileRequest"]
    assert not body.get("required")
    assert "null" not in str(body["properties"])


async def test_unrepresentable_decimal_exponent_is_safe_422(client, patch_api):
    _, _, update, headers = patch_api
    response = await client.patch(
        "/api/v1/users/me",
        content='{"heightCm":1e-9999999999999999999999999}',
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "body", "reason": "invalid_json"}
    ]
    update.assert_not_awaited()
