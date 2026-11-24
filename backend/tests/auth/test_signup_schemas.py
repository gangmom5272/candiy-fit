from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.auth.schemas import (
    AndroidLoggedInResponse,
    SignupRequest,
    SignupResponse,
    WebLoggedInResponse,
)
from app.core.schemas import DataResponse
from app.users.schemas import UserResponse

PROFILE = {
    "displayName": "합성회원",
    "birthYear": 2000,
    "gender": "FEMALE",
    "heightCm": 165.25,
    "weightKg": 55,
}


@pytest.mark.parametrize("field", ["signupToken", *PROFILE])
@pytest.mark.parametrize("mode", ["missing", "null"])
def test_signup_requires_all_fields(field, mode):
    body = PROFILE | {"signupToken": "synthetic-signup"}
    if mode == "missing":
        del body[field]
    else:
        body[field] = None
    with pytest.raises(ValidationError):
        SignupRequest.model_validate(body)


@pytest.mark.parametrize("value", ["", " \t\n", 123, True, []])
def test_signup_rejects_invalid_token_format(value):
    with pytest.raises(ValidationError):
        SignupRequest.model_validate(PROFILE | {"signupToken": value})


@pytest.mark.parametrize("field", ["platformType", "socialType", "userId"])
def test_signup_rejects_identity_overrides(field):
    with pytest.raises(ValidationError):
        SignupRequest.model_validate(
            PROFILE | {"signupToken": "synthetic", field: None}
        )


def test_signup_preserves_token_and_defers_profile_values():
    body = SignupRequest.model_validate(
        PROFILE
        | {
            "signupToken": " synthetic ",
            "gender": "UNKNOWN",
            "birthYear": 1800,
            "heightCm": -1,
        }
    )
    assert body.signup_token == " synthetic "
    assert body.gender == "UNKNOWN" and body.height_cm == -1
    assert "synthetic" not in repr(body)


@pytest.mark.parametrize("android", [False, True])
def test_signup_response_keeps_platform_refresh_contract(android):
    now = datetime(2026, 10, 24, tzinfo=UTC)
    user_id = uuid4()
    user = UserResponse(
        id=user_id, end_user_id=user_id, created_at=now, social_type="GOOGLE", **PROFILE
    )
    fields = {
        "access_token": "synthetic-access",
        "access_token_expires_at": now,
        "user": user,
    }
    data = (
        AndroidLoggedInResponse(**fields, refresh_token="synthetic-refresh")
        if android
        else WebLoggedInResponse(user=user, expires_at=now)
    )
    response = DataResponse[SignupResponse](data=data)
    payload = TypeAdapter(DataResponse[SignupResponse]).dump_python(
        response, mode="json"
    )["data"]
    assert ("refreshToken" in payload) is android
    assert ("accessToken" in payload) is android
    assert ("expiresAt" in payload) is not android
    assert payload["status"] == "LOGGED_IN"
