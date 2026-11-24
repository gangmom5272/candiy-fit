"""프로필 형식과 공개 User. 업무 값 검증은 자격 확인 뒤 별도로 호출한다."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.core.schemas import ApiRequest, ApiResponse
from app.users.models import User


class AppConnection(BaseModel):
    """앱이 보고한 최초 설정 완료 이력. 현재 수집 가능 여부와 다르다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["UNKNOWN", "NOT_CONNECTED", "CONNECTED"]
    connected_at: AwareDatetime | None

    @field_validator("connected_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_completion(self) -> "AppConnection":
        if (self.status == "CONNECTED") != (self.connected_at is not None):
            raise ValueError("only CONNECTED requires a completion time")
        return self


def app_connection(user: User) -> AppConnection:
    return AppConnection(
        status=user.app_connection_status, connected_at=user.app_connected_at
    )


def _number_type(value: object) -> int | float:
    if type(value) not in {int, float}:
        reason = "null_not_allowed" if value is None else "invalid_type"
        raise PydanticCustomError(reason, "JSON number required")
    return value


JsonNumber = Annotated[int | float, BeforeValidator(_number_type)]


class ProfileInput(ApiRequest):
    display_name: str
    birth_year: int
    gender: str
    height_cm: JsonNumber
    weight_kg: JsonNumber


def _omit_default(schema: dict) -> None:
    schema.pop("default", None)


class UpdateProfileRequest(ProfileInput):
    # 미전달 필드만 내부 None을 가진다. 전달한 null은 비-null 타입 검증에서 거절한다.
    display_name: str = Field(default=None, json_schema_extra=_omit_default)
    birth_year: int = Field(default=None, json_schema_extra=_omit_default)
    gender: str = Field(default=None, json_schema_extra=_omit_default)
    height_cm: JsonNumber = Field(default=None, json_schema_extra=_omit_default)
    weight_kg: JsonNumber = Field(default=None, json_schema_extra=_omit_default)

    @model_validator(mode="after")
    def require_changes(self) -> "UpdateProfileRequest":
        if not self.model_fields_set:
            raise PydanticCustomError("empty", "empty patch")
        return self


class UserResponse(ApiResponse):
    id: UUID
    end_user_id: UUID
    display_name: str
    birth_year: int
    gender: Literal["MALE", "FEMALE"]
    height_cm: float
    weight_kg: float
    social_type: Literal["GOOGLE", "KAKAO"]
    created_at: datetime


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        end_user_id=user.id,
        display_name=user.display_name,
        birth_year=user.birth_year,
        gender=user.gender,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        social_type=user.social_type,
        created_at=user.created_at.astimezone(UTC),
    )
