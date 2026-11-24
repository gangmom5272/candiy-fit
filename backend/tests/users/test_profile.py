import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.validation import validation_details
from app.users.models import User
from app.users.schemas import ProfileInput, UpdateProfileRequest, user_response
from app.users.validation import ProfileValidationError, validate_profile

NOW = datetime(2031, 1, 1, tzinfo=UTC)


def validated(payload, now=NOW):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    return validate_profile(UpdateProfileRequest.model_validate(payload), raw, now=now)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("  가\u3000", "가"),
        ("A b", "A b"),
        ("😀" * 12, "😀" * 12),
        ("👩‍💻", "👩‍💻"),
        ("<b>나</b>", "<b>나</b>"),
        ("\u00a0É1!\u00a0", "É1!"),
    ],
)
def test_name_normalization(name, expected):
    assert validated({"displayName": name}) == {"display_name": expected}


@pytest.mark.parametrize(
    "name,reason",
    [
        ("", "blank"),
        ("\u3000 ", "blank"),
        ("\u200d\ufe0f", "blank"),
        ("\u3164", "blank"),
        ("\U000e0001", "blank"),
        ("\u034f", "blank"),
        ("a" * 13, "too_long"),
        ("\n이름", "invalid_character"),
        ("이름\t", "invalid_character"),
        ("a\x00b", "invalid_character"),
        ("\u2028이름", "invalid_character"),
        ("a\u2029b", "invalid_character"),
    ],
)
def test_invalid_names(name, reason):
    with pytest.raises(ProfileValidationError) as caught:
        validated({"displayName": name})
    assert [(d.field, d.reason) for d in caught.value.details] == [
        ("displayName", reason)
    ]


@pytest.mark.parametrize("field,number", [("heightCm", 300), ("weightKg", 500)])
def test_numeric_range(field, number):
    for valid in (0.01, number):
        validated({field: valid})
    for invalid in (0, -1, number + 0.01):
        with pytest.raises(ProfileValidationError) as caught:
            validated({field: invalid})
        assert caught.value.details[0].reason == "out_of_range"


@pytest.mark.parametrize(
    "literal,valid",
    [
        ("165.250", True),
        ("1.65250e2", True),
        ("55", True),
        ("165.251", False),
        ("165.25000000000000001", False),
        ("0.01000000000000000000000000000000000001", False),
        ("1e-999", False),
    ],
)
def test_exact_decimal_from_raw_body(literal, valid):
    raw = ('{"heightCm":' + literal + "}").encode()
    body = UpdateProfileRequest.model_validate_json(raw)
    if valid:
        result = validate_profile(body, raw, now=NOW)
        assert result["height_cm"] == Decimal(literal)
        assert isinstance(result["height_cm"], Decimal)
    else:
        with pytest.raises(ProfileValidationError) as caught:
            validate_profile(body, raw, now=NOW)
        assert caught.value.details[0].reason == "decimal_places"


@pytest.mark.parametrize(
    "field,literal", [("heightCm", "166.25"), ("weightKg", "56.75")]
)
def test_long_trailing_zeros_are_removed_without_context_rounding(field, literal):
    raw = ('{"' + field + '":' + literal + "0" * 20000 + "}").encode()
    body = UpdateProfileRequest.model_validate_json(raw)
    with localcontext() as context:
        context.prec = 1
        result = validate_profile(body, raw, now=NOW)
    value = next(iter(result.values()))
    assert value.as_tuple() == Decimal(literal).as_tuple()

    # 마지막에 0이 아닌 자릿수가 있으면 반올림해 허용하지 않는다.
    invalid_raw = raw[:-1] + b"1}"
    with pytest.raises(ProfileValidationError) as caught:
        validate_profile(
            UpdateProfileRequest.model_validate_json(invalid_raw), invalid_raw, now=NOW
        )
    assert caught.value.details[0].reason == "decimal_places"


@pytest.mark.parametrize(
    "field,value",
    [
        ("birthYear", True),
        ("birthYear", "2000"),
        ("birthYear", 2000.0),
        ("heightCm", True),
        ("heightCm", "165.25"),
        ("weightKg", False),
        ("gender", 1),
        ("displayName", 3),
    ],
)
def test_format_rejects_coercion(field, value):
    with pytest.raises(ValidationError) as caught:
        UpdateProfileRequest.model_validate({field: value})
    assert all(
        d.reason == "invalid_type" for d in validation_details(caught.value.errors())
    )


@pytest.mark.parametrize(
    "field", ["displayName", "birthYear", "gender", "heightCm", "weightKg"]
)
def test_null_is_not_omission(field):
    with pytest.raises(ValidationError) as caught:
        UpdateProfileRequest.model_validate({field: None})
    assert validation_details(caught.value.errors())[0].reason == "null_not_allowed"


def test_empty_extra_and_schema_contract():
    for payload, reason in [
        ({}, "empty"),
        ({"socialType": None}, "extra_forbidden"),
        ({"birth_year": 2000}, "extra_forbidden"),
    ]:
        with pytest.raises(ValidationError) as caught:
            UpdateProfileRequest.model_validate(payload)
        assert validation_details(caught.value.errors())[0].reason == reason
    schema = UpdateProfileRequest.model_json_schema()
    assert not schema.get("required")
    assert schema["additionalProperties"] is False
    for value in schema["properties"].values():
        assert "null" not in str(value)


def test_format_and_value_validation_are_separate_and_collect_errors():
    payload = dict(
        displayName="", birthYear=1899, gender="male", heightCm=301, weightKg=0
    )
    body = ProfileInput.model_validate(payload)
    with pytest.raises(ProfileValidationError) as caught:
        validate_profile(body, json.dumps(payload).encode(), now=NOW)
    assert {d.field for d in caught.value.details} == set(payload)


def test_birth_year_uses_kst_request_year():
    boundary = datetime(2030, 12, 31, 15, tzinfo=UTC)
    validated({"birthYear": 1900})
    with pytest.raises(ProfileValidationError):
        validated({"birthYear": 2031}, boundary - timedelta(microseconds=1))
    assert validated({"birthYear": 2031}, boundary)["birth_year"] == 2031
    with pytest.raises(ProfileValidationError):
        validated({"birthYear": 2032}, boundary)


def test_user_response_has_exact_public_fields_and_numbers():
    member = User(
        id=uuid4(),
        display_name="합성",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.250"),
        weight_kg=Decimal("55"),
        social_type="GOOGLE",
        social_user_id="private-synthetic-id",
        created_at=NOW.astimezone(timezone(timedelta(hours=9))),
        updated_at=NOW,
    )
    result = json.loads(user_response(member).model_dump_json())
    assert set(result) == {
        "id",
        "endUserId",
        "displayName",
        "birthYear",
        "gender",
        "heightCm",
        "weightKg",
        "socialType",
        "createdAt",
    }
    assert result["id"] == result["endUserId"] == str(member.id)
    assert result["heightCm"] == 165.25 and isinstance(result["heightCm"], float)
    assert result["weightKg"] == 55 and not isinstance(result["weightKg"], str)
    assert result["createdAt"] == "2031-01-01T00:00:00Z"
    assert "private-synthetic" not in str(result)


def test_profile_reuse_does_not_return_signup_credentials():
    class SignupShape(ProfileInput):
        signup_token: str

    payload = dict(
        displayName="가입",
        birthYear=2000,
        gender="MALE",
        heightCm=165,
        weightKg=55,
        signupToken="synthetic-signup-credential",
    )
    result = validate_profile(
        SignupShape.model_validate(payload), json.dumps(payload).encode(), now=NOW
    )
    assert set(result) == {
        "display_name",
        "birth_year",
        "gender",
        "height_cm",
        "weight_kg",
    }
    assert "synthetic-signup-credential" not in str(result)
