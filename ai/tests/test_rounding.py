from decimal import Decimal

import pytest

from coach_ai.tools.rounding import round_half_up


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        (30.5, 0, "31"),
        (Decimal("30.5"), 0, "31"),
        (31.5, 0, "32"),
        (2.5, 0, "3"),
        (0.5, 0, "1"),
        (30.49, 0, "30"),
        (-45.55, 1, "-45.6"),
        (Decimal("-45.55"), 1, "-45.6"),
        (-45.55, 0, "-46"),
        (-0.5, 0, "-1"),
        (Decimal("-45.549"), 1, "-45.5"),
        # round() 는 72.25 를 짝수 쪽으로 내리고, 2.675 는 부동소수 오차 때문에 내린다
        (72.25, 1, "72.3"),
        (2.675, 2, "2.68"),
        (Decimal("96.15"), 1, "96.2"),
        (Decimal("7412"), 0, "7412"),
        (7412, 0, "7412"),
        (58, 1, "58.0"),
    ],
)
def test_half_rounds_away_from_zero(value, places, expected):
    result = round_half_up(value, places)

    assert isinstance(result, Decimal)
    assert str(result) == expected


@pytest.mark.parametrize(("value", "places", "expected"), [(Decimal("-0.4"), 0, "0"), (-0.04, 1, "0.0")])
def test_negative_value_rounded_to_zero_has_no_sign(value, places, expected):
    assert str(round_half_up(value, places)) == expected


def test_places_default_to_whole_number():
    assert round_half_up(Decimal("1.5")) == Decimal(2)


@pytest.mark.parametrize("value", [True, "30.5", None])
def test_rejects_values_that_are_not_numbers(value):
    with pytest.raises(TypeError):
        round_half_up(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), Decimal("-Infinity")])
def test_rejects_non_finite_values(value):
    with pytest.raises(ValueError):
        round_half_up(value)


def test_rejects_negative_places():
    with pytest.raises(ValueError):
        round_half_up(Decimal("31"), -1)
