"""Validate typed parameters supplied by a host conversation."""

import math
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from ..contracts import StudioError


def numeric(value, *, integer=False, money=False):
    try:
        if len(str(value)) > 256:
            raise ValueError()
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or (not number.is_zero() and abs(number.adjusted()) > 1000)
            or (integer and number != number.to_integral_value())
        ):
            raise ValueError()
        if money:
            return int((number * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
        if integer:
            return int(number)
        result = float(number)
        if not math.isfinite(result):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise StudioError("INPUT_INVALID", "請輸入有效的整數或有限小數。") from exc
