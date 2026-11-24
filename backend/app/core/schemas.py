"""공개 응답 외곽. 중첩 dict(건강 snapshot)의 키는 변환하지 않는다."""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class ApiResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True
    )


class DataResponse(ApiResponse, Generic[T]):
    data: T


class ApiRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


class ErrorDetail(BaseModel):
    field: str
    reason: str
