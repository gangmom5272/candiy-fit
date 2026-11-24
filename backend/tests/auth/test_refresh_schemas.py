import json
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError

from app.auth.schemas import (
    AndroidRefreshResponse,
    LogoutRequest,
    RefreshRequest,
    RefreshResponse,
)
from app.auth.web import delete_refresh_cookie
from app.core.errors import ApiError, error_response
from app.core.schemas import DataResponse
from app.users.schemas import UserResponse


@pytest.mark.parametrize("model", [RefreshRequest, LogoutRequest])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"refreshToken": None},
        {"refreshToken": 1},
        {"refreshToken": "x", "platformType": "WEB"},
    ],
)
def test_refresh_body_requires_only_string_token(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("value", ["", " \t\n"])
def test_logout_rejects_blank_but_refresh_preserves_submitted_credential(value):
    with pytest.raises(ValidationError):
        LogoutRequest.model_validate({"refreshToken": value})
    assert RefreshRequest.model_validate({"refreshToken": value}).refresh_token == value


def test_refresh_token_is_preserved_and_hidden():
    for model in (RefreshRequest, LogoutRequest):
        value = model.model_validate({"refreshToken": " synthetic "})
        assert value.refresh_token == " synthetic "
        assert "synthetic" not in repr(value)


def test_refresh_response_has_no_status_and_only_android_token():
    now = datetime(2031, 1, 1, tzinfo=UTC)
    uid = uuid4()
    user = UserResponse(
        id=uid,
        end_user_id=uid,
        created_at=now,
        social_type="GOOGLE",
        display_name="합성",
        birth_year=2000,
        gender="FEMALE",
        height_cm=165,
        weight_kg=55,
    )
    fields = dict(
        access_token="synthetic-access", access_token_expires_at=now, user=user
    )
    data = AndroidRefreshResponse(**fields, refresh_token="synthetic-refresh")
    payload = DataResponse[RefreshResponse](data=data).model_dump(mode="json")["data"]
    assert set(payload) == {
        "accessToken",
        "accessTokenExpiresAt",
        "user",
        "refreshToken",
    }
    assert payload["user"] == user.model_dump(mode="json")
    assert "synthetic" not in repr(data)


def test_delete_cookie_matches_refresh_scope():
    response = Response(status_code=204)
    delete_refresh_cookie(response)
    cookie = SimpleCookie(response.headers["set-cookie"])["cf_refresh"]
    assert cookie["path"] == "/api/v1/auth"
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"].lower() == "lax"
    assert cookie["max-age"] == "0" and cookie["expires"]
    assert not cookie["domain"]


@pytest.mark.parametrize("retryable", [None, False, True])
def test_error_optional_retryable_preserves_false_and_omits_none(retryable):
    response = error_response(
        ApiError(503, "AUTH_UNAVAILABLE", "safe", retryable=retryable)
    )
    error = json.loads(response.body)["error"]
    assert ("retryable" in error) is (retryable is not None)
    if retryable is not None:
        assert error["retryable"] is retryable
    assert json.loads(error_response(ApiError(500, "INTERNAL_ERROR", "safe")).body) == {
        "error": {"code": "INTERNAL_ERROR", "message": "safe"}
    }
