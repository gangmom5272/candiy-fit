"""형식 검증된 프로필의 값 검증. HTTP·DB·자격 소비를 수행하지 않는다."""

import json
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TypedDict
from zoneinfo import ZoneInfo

from app.core.schemas import ErrorDetail
from app.users.schemas import ProfileInput

# Unicode 17.0 DerivedCoreProperties.txt의 Default_Ignorable_Code_Point.
# https://www.unicode.org/Public/17.0.0/ucd/DerivedCoreProperties.txt
_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


class ProfileChanges(TypedDict, total=False):
    display_name: str
    birth_year: int
    gender: str
    height_cm: Decimal
    weight_kg: Decimal


class ProfileValidationError(Exception):
    def __init__(self, details: list[ErrorDetail]) -> None:
        super().__init__("invalid_profile")
        self.details = details


def _name(value: str) -> tuple[str, str | None]:
    if any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in value):
        return value, "invalid_character"
    value = unicodedata.normalize("NFC", value).strip()
    if not any(
        not char.isspace()
        and not any(start <= ord(char) <= end for start, end in _IGNORABLE_RANGES)
        for char in value
    ):
        return value, "blank"
    return value, "too_long" if len(value) > 12 else None


def _number(value: Decimal, maximum: int) -> tuple[Decimal, str | None]:
    if not value.is_finite():
        return value, "invalid_type"
    if not 0 < value <= maximum:
        return value, "out_of_range"
    # normalize/quantize와 Decimal 컨텍스트의 반올림 없이 유효 소수 자릿수를 센다.
    sign, digits, exponent = value.as_tuple()
    trailing = 0
    for digit in reversed(digits):
        if digit:
            break
        trailing += 1
    if exponent + trailing < -2:
        return value, "decimal_places"
    # 검증된 값의 후행 0만 제거해 DB의 소수 자릿수 한도를 넘지 않게 한다.
    if trailing:
        value = Decimal((sign, digits[:-trailing], exponent + trailing))
    return value, None


def validate_profile(
    body: ProfileInput, raw: bytes, *, now: datetime
) -> ProfileChanges:
    """ApiRoute·형식 모델을 통과한 동일 본문만 받는다. 가입은 자격 검사 후 호출한다."""
    if now.utcoffset() is None:
        raise ValueError("timezone-aware request time required")
    # 공통 JSON 처리를 바꾸지 않고 수치 원문만 Decimal로 보존한다.
    try:
        exact = json.loads(raw, parse_float=Decimal)
    except InvalidOperation:
        raise ProfileValidationError(
            [ErrorDetail(field="body", reason="invalid_json")]
        ) from None
    # 가입 모델에 추가된 자격 필드를 저장용 프로필에 섞지 않는다.
    values = {
        field: getattr(body, field)
        for field in ProfileInput.model_fields
        if field in body.model_fields_set
    }
    errors = []
    for field in values:
        reason = None
        if field == "display_name":
            values[field], reason = _name(values[field])
        elif field == "birth_year":
            if not 1900 <= values[field] <= now.astimezone(ZoneInfo("Asia/Seoul")).year:
                reason = "out_of_range"
        elif field == "gender":
            if values[field] not in {"MALE", "FEMALE"}:
                reason = "invalid_choice"
        elif field in {"height_cm", "weight_kg"}:
            alias = type(body).model_fields[field].alias
            values[field], reason = _number(
                Decimal(exact[alias]), 300 if field == "height_cm" else 500
            )
        if reason:
            errors.append(
                ErrorDetail(field=type(body).model_fields[field].alias, reason=reason)
            )
    if errors:
        raise ProfileValidationError(errors)
    return values
