"""소셜 로그인·가입 요청과 플랫폼별 공개 응답."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.core.schemas import ApiRequest, ApiResponse
from app.users.schemas import ProfileInput, UserResponse


class SocialChallengeRequest(ApiRequest):
    social_type: Literal["GOOGLE", "KAKAO"]
    platform_type: Literal["ANDROID"]

    @field_validator("social_type", "platform_type", mode="before")
    @classmethod
    def validate_enum_type(cls, value: object) -> object:
        # Literal의 타입 오류도 선택값 오류가 아닌 공통 invalid_type으로 전달한다.
        if value is not None and not isinstance(value, str):
            raise PydanticCustomError("invalid_type", "string required")
        return value


class SocialChallengeResponse(ApiResponse):
    challenge_token: str = Field(repr=False)
    nonce: str = Field(repr=False)
    expires_at: datetime


class WebSocialLoginRequest(ApiRequest):
    social_type: Literal["GOOGLE", "KAKAO"]
    platform_type: Literal["WEB"]
    authorization_code: str = Field(repr=False)
    code_verifier: str = Field(repr=False)
    redirect_uri: str

    @field_validator("code_verifier")
    @classmethod
    def validate_verifier(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", value) is None:
            raise PydanticCustomError("invalid_format", "invalid PKCE verifier")
        return value


class AndroidSocialLoginRequest(ApiRequest):
    social_type: Literal["GOOGLE", "KAKAO"]
    platform_type: Literal["ANDROID"]
    id_token: str = Field(repr=False)
    challenge_token: str = Field(repr=False)


def _validate_social_login(
    value: object, handler: ValidatorFunctionWrapHandler
) -> WebSocialLoginRequest | AndroidSocialLoginRequest:
    try:
        return handler(value)
    except ValidationError as error:
        errors = []
        for item in error.errors(include_url=False):
            kind, location = item["type"], item["loc"]
            if kind in {"union_tag_not_found", "union_tag_invalid"}:
                location = ("platformType",)
                if "platformType" not in value:
                    kind = "missing"
                elif value["platformType"] is None:
                    kind = "null_not_allowed"
                elif not isinstance(value["platformType"], str):
                    kind = "invalid_type"
                else:
                    kind = "invalid_choice"
            else:
                # 이 요청의 Union 분기명만 제거한다. 실제 extra 필드명은 보존한다.
                if location and location[0] in {"WEB", "ANDROID"}:
                    location = location[1:]
                if (
                    kind == "literal_error"
                    and item.get("input") is not None
                    and not isinstance(item["input"], str)
                ):
                    kind = "invalid_type"
            errors.append(
                {
                    "type": PydanticCustomError(kind, "invalid social login request"),
                    "loc": location,
                    "input": item.get("input"),
                }
            )
        raise ValidationError.from_exception_data(
            "SocialLoginRequest", errors
        ) from None


SocialLoginRequest = Annotated[
    WebSocialLoginRequest | AndroidSocialLoginRequest,
    Field(discriminator="platform_type"),
    WrapValidator(_validate_social_login),
]


class WebLoggedInResponse(ApiResponse):
    status: Literal["LOGGED_IN"] = "LOGGED_IN"
    expires_at: datetime
    user: UserResponse


class SignupRequiredResponse(ApiResponse):
    status: Literal["SIGNUP_REQUIRED"] = "SIGNUP_REQUIRED"
    signup_token: str = Field(repr=False)
    signup_token_expires_at: datetime
    social_type: Literal["GOOGLE", "KAKAO"]


class SignupRequest(ProfileInput):
    signup_token: str = Field(repr=False)

    @field_validator("signup_token")
    @classmethod
    def validate_signup_token(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError("invalid_format", "signup token required")
        return value


class AndroidLoggedInResponse(ApiResponse):
    status: Literal["LOGGED_IN"] = "LOGGED_IN"
    access_token: str = Field(repr=False)
    access_token_expires_at: datetime
    user: UserResponse
    refresh_token: str = Field(repr=False)


SignupResponse = AndroidLoggedInResponse | WebLoggedInResponse
SocialLoginResponse = (
    AndroidLoggedInResponse | WebLoggedInResponse | SignupRequiredResponse
)


class RefreshRequest(ApiRequest):
    refresh_token: str = Field(repr=False)


class LogoutRequest(RefreshRequest):
    @field_validator("refresh_token")
    @classmethod
    def validate_refresh_token(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError("invalid_format", "refresh token required")
        return value


class AndroidRefreshResponse(ApiResponse):
    access_token: str = Field(repr=False)
    access_token_expires_at: datetime
    user: UserResponse

    refresh_token: str = Field(repr=False)


RefreshResponse = AndroidRefreshResponse


class WebSessionResponse(ApiResponse):
    user: UserResponse
    expires_at: datetime
