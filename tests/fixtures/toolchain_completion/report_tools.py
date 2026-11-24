"""Independent implementations for the installed CSV acceptance fixture."""

from csv import DictReader
from decimal import Decimal
from io import StringIO


def read_pair(inputs, _parameters):
    return {
        "pair": {
            key: list(DictReader(StringIO(inputs[key]))) for key in ("left", "right")
        }
    }


def normalize(inputs, _parameters):
    return {
        "normalized": {
            key: {row["id"]: f"{Decimal(row['amount']):.2f}" for row in rows}
            for key, rows in inputs["pair"].items()
        }
    }


def normalize_replacement(inputs, _parameters):
    result = {}
    for key, rows in inputs["pair"].items():
        result[key] = {
            row["id"]: str(Decimal(row["amount"]).quantize(Decimal("0.01")))
            for row in rows
        }
    return {"normalized": result}


def compare(inputs, _parameters):
    left, right = (inputs["normalized"][key] for key in ("left", "right"))
    matched, differences = [], []
    for key in sorted(left.keys() & right.keys()):
        delta = Decimal(right[key]) - Decimal(left[key])
        if delta == 0:
            matched.append(key)
        else:
            differences.append({"id": key, "right_minus_left": f"{delta:.2f}"})
    return {
        "report": {
            "matched": matched,
            "differences": differences,
            "right_only": sorted(right.keys() - left.keys()),
        }
    }
