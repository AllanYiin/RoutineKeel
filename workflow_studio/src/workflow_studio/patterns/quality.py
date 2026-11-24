"""Independent checks used by the reference business qualification cases."""

import re
from collections import Counter
from decimal import Decimal, InvalidOperation

_NUMBERS_AND_CITATIONS = re.compile(r"\[[^\]\r\n]+\]|(?<!\w)[+−-]?\d[\d,.]*(?:%|‰)?")
_DECIMAL = re.compile(r"^[+-]?[0-9]{1,24}(?:\.[0-9]{1,8})?$")


def _fidelity(original, actual):
    return (
        isinstance(original, str)
        and isinstance(actual, str)
        and Counter(_NUMBERS_AND_CITATIONS.findall(original))
        == Counter(_NUMBERS_AND_CITATIONS.findall(actual))
    )


def _rows(values, key, amount):
    if not isinstance(values, list) or not values or len(values) > 10000:
        raise ValueError("invalid rows")
    result = {}
    for row in values:
        value, total = row[key], row[amount]
        if (
            type(value) not in {str, int}
            or value == ""
            or value in result
            or not isinstance(total, str)
            or not _DECIMAL.fullmatch(total)
        ):
            raise ValueError("invalid comparison key or decimal")
        result[value] = Decimal(total)
    return result


def _reconciliation(inputs, output, rule):
    from decimal import localcontext

    try:
        with localcontext() as context:
            context.prec = 40
            left = _rows(inputs[rule["left"]], rule["key"], rule["amount"])
            right = _rows(inputs[rule["right"]], rule["key"], rule["amount"])
            tolerance = rule["absolute_tolerance"]
            if not isinstance(tolerance, str) or not _DECIMAL.fullmatch(tolerance):
                return False
            tolerance = Decimal(tolerance)
            if tolerance < 0:
                return False
            same = left.keys() == right.keys() and all(
                abs(left[key] - right[key]) <= tolerance for key in left
            )
            expected = "matched" if same else "mismatched"
    except (ValueError, TypeError, KeyError, InvalidOperation):
        expected = "invalid_input"
    return output == expected


def check_reference_quality(rule, inputs, outputs):
    output = outputs.get(rule["output"])
    if rule["kind"] == "text_fidelity":
        return _fidelity(inputs.get(rule["source"]), output)
    if rule["kind"] == "decimal_reconciliation":
        return _reconciliation(inputs, output, rule)
    return False
