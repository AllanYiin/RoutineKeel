"""Deterministic, local data operations on explicitly supplied inputs."""

import csv
import io
import re
from decimal import Decimal, localcontext

_MONEY = re.compile(r"^[+-]?[0-9]{1,24}(?:\.[0-9]{1,8})?$")


def read_csv(inputs, parameters):
    text = inputs["source"]
    maximum_rows = parameters.get("max_rows", 10000)
    if (
        not isinstance(text, str)
        or len(text.encode("utf-8")) > 1024**2
        or type(maximum_rows) is not int
        or not 1 <= maximum_rows <= 10000
    ):
        raise ValueError("invalid CSV input")
    text = text.removeprefix("\ufeff")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    headers = next(reader, [])
    if not headers or len(headers) > 256 or len(set(headers)) != len(headers) or not all(headers):
        raise ValueError("invalid CSV header")
    rows = []
    for row in reader:
        if len(row) != len(headers) or len(rows) >= maximum_rows:
            raise ValueError("invalid CSV row")
        rows.append(dict(zip(headers, row, strict=True)))
    return {"rows": rows}


def _indexed(rows, key, amount):
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise ValueError("invalid input")
    entries = {}
    for row in rows:
        name, original = row[key], row[amount]
        if type(name) not in {str, int} or name == "" or name in entries:
            raise ValueError("invalid key")
        if not isinstance(original, str) or not _MONEY.fullmatch(original):
            raise ValueError("invalid amount")
        entries[name] = Decimal(original)
    return entries


def reconcile(inputs, parameters):
    key = parameters.get("key", "id")
    amount = parameters.get("amount", "amount")
    tolerance = parameters.get("absolute_tolerance", "0")
    if not isinstance(tolerance, str) or not _MONEY.fullmatch(tolerance) or Decimal(tolerance) < 0:
        raise ValueError("invalid tolerance")
    try:
        left = _indexed(inputs["left"], key, amount)
        right = _indexed(inputs["right"], key, amount)
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid_input"}
    with localcontext() as context:
        context.prec = 40
        same = left.keys() == right.keys() and all(
            abs(left[item] - right[item]) <= Decimal(tolerance) for item in left
        )
    return {"status": "matched" if same else "mismatched"}
