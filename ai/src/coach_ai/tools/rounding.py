"""결과 문장에 표시할 숫자의 반올림. 기준 판정은 반올림 전 값으로 하고 표시할 때만 쓴다."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: Decimal | int | float, places: int = 0) -> Decimal:
    """value 를 소수 places 자리로 반올림한다. 0.5 는 0 에서 먼 쪽으로 올린다 (Decimal ROUND_HALF_UP).

    내장 round() 는 0.5 를 짝수 쪽으로 보내 결과가 다르다. float 은 짧은 십진 표기(str)로 바꿔 계산해
    72.25 가 72.3 이 된다. 반올림해 0 이 된 음수는 부호 없는 0 이다.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float):
        raise TypeError("숫자만 반올림한다")
    if places < 0:
        raise ValueError("places 는 0 이상이어야 한다")
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("유한한 값만 반올림한다")
    rounded = number.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    return rounded if rounded else abs(rounded)
