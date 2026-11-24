"""입력값을 공개하지 않는 JSON 검사와 Pydantic 오류 변환."""

import json
import math
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import ApiError, error_response
from app.core.schemas import ErrorDetail

REASONS = frozenset(
    [
        "missing",
        "null_not_allowed",
        "invalid_type",
        "blank",
        "too_long",
        "invalid_character",
        "invalid_choice",
        "out_of_range",
        "decimal_places",
        "extra_forbidden",
        "duplicate",
        "empty",
        "invalid_json",
        "invalid_format",
    ]
)


def invalid_request(details: list[ErrorDetail]) -> ApiError:
    return ApiError(
        422, "INVALID_REQUEST", "요청 내용을 확인해 주세요.", details=details
    )


def invalid_field(field: str, reason: str) -> ApiError:
    return invalid_request([ErrorDetail(field=field, reason=reason)])


class _ObjectPairs(list):
    """object_pairs_hook가 JSON 객체와 배열을 구분하도록 보존한다."""


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def strict_json(raw: bytes) -> Any:
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_ObjectPairs,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        duplicates = []

        def unpack(value: Any, path: str = "") -> Any:
            if isinstance(value, _ObjectPairs):
                result = {}
                for key, item in value:
                    # JSON escape로 들어온 단독 surrogate는 UTF-8 응답에 담을 수 없다.
                    key.encode("utf-8")
                    field = f"{path}.{key}" if path else key
                    if key in result:
                        duplicates.append(ErrorDetail(field=field, reason="duplicate"))
                    result[key] = unpack(item, field)
                return result
            if isinstance(value, list):
                return [
                    unpack(item, f"{path}[{index}]") for index, item in enumerate(value)
                ]
            if isinstance(value, str):
                value.encode("utf-8")
            return value

        result = unpack(parsed)
    except (ValueError, RecursionError):
        raise invalid_field("body", "invalid_json") from None
    if duplicates:
        raise invalid_request(duplicates)
    return result


def _field_path(location: tuple) -> str:
    parts = (
        location[1:]
        if location and location[0] in {"body", "query", "path", "header", "cookie"}
        else location
    )
    result = ""
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += ("." if result else "") + str(part)
    return result or (str(location[0]) if location else "body")


def validation_details(errors: list[dict]) -> list[ErrorDetail]:
    details = []
    for error in errors:
        kind = error["type"]
        if kind in REASONS:
            reason = kind
        elif error.get("input") is None:
            reason = "null_not_allowed"
        elif kind in {"literal_error", "enum"}:
            reason = "invalid_choice"
        elif kind in {
            "greater_than",
            "greater_than_equal",
            "less_than",
            "less_than_equal",
        }:
            reason = "out_of_range"
        elif kind in {"string_too_long", "too_long"}:
            reason = "too_long"
        elif kind == "json_invalid":
            reason = "invalid_json"
        elif kind.endswith("_type") or kind in {
            "int_from_float",
            "finite_number",
            "model_attributes_type",
        }:
            reason = "invalid_type"
        else:
            reason = "invalid_format"
        field = "body" if kind == "json_invalid" else _field_path(error["loc"])
        details.append(ErrorDetail(field=field, reason=reason))
    return details


async def handle_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return error_response(invalid_request(validation_details(error.errors())))


async def ensure_no_body(request: Request) -> None:
    if await request.body():
        raise invalid_field("body", "extra_forbidden")
