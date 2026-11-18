"""Explicit, versioned acceptance rules independent of generated handlers."""

import json
from decimal import Decimal, InvalidOperation

from ..contracts import StudioError

RULES = ("exact_bytes", "json_equal")
MAX_BYTES = 10 * 1024 * 1024
MAX_DEPTH = 64
MAX_VALUES = 100_000


def read_json(data: bytes):
    """Read bounded UTF-8 JSON without duplicate keys or lossy float conversion."""
    if len(data) > MAX_BYTES:
        raise ValueError("JSON 超過 10 MiB 比較上限。")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 含重複欄位。")
            result[key] = value
        return result

    def number(token):
        result = Decimal(token)
        if not result.is_finite():
            raise ValueError("JSON 數字必須有限。")
        return result

    def constant(_):
        raise ValueError("JSON 不接受 NaN 或 Infinity。")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_int=number,
            parse_float=number,
            parse_constant=constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation, RecursionError) as exc:
        raise ValueError("成果不是有效的 UTF-8 JSON，或超過解析限制。") from exc
    stack = [(value, 0)]
    count = 0
    while stack:
        current, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_VALUES:
            raise ValueError("JSON 超過深度 64 或 100,000 個值的比較上限。")
        if isinstance(current, dict):
            for key in current:
                if any(0xD800 <= ord(char) <= 0xDFFF for char in key):
                    raise ValueError("JSON 欄位含無效 Unicode 字元。")
            stack.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, list):
            stack.extend((v, depth + 1) for v in current)
        elif isinstance(current, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in current):
            raise ValueError("JSON 值含無效 Unicode 字元。")
    return value


def validate_expected(rule, expected):
    if rule not in RULES:
        raise StudioError("INPUT_INVALID", "不支援的成果比較規則。")
    if rule == "json_equal":
        try:
            read_json(expected)
        except ValueError as exc:
            raise StudioError(
                "INPUT_INVALID", "預期成果無法使用 JSON 內容比較：" + str(exc)
            ) from exc


def compare(rule, expected: bytes, actual: bytes):
    """Return match and structured reason; never infer or relax an acceptance rule."""
    validate_expected(rule, expected)
    if rule == "exact_bytes":
        return actual == expected, {"reason": "位元組內容不同。"}
    left = read_json(expected)
    try:
        right = read_json(actual)
    except ValueError as exc:
        return False, {"reason": "實際成果不是可比較的 JSON：" + str(exc)}

    stack = [(left, right, "")]
    while stack:
        a, b, pointer = stack.pop()
        reason = None
        if type(a) is not type(b):
            reason = "JSON 值型別不同。"
        elif isinstance(a, dict):
            if a.keys() != b.keys():
                reason = "JSON 物件欄位不同。"
            else:
                for key in sorted(a, reverse=True):
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    stack.append((a[key], b[key], (pointer + "/" + escaped)[:2048]))
        elif isinstance(a, list):
            if len(a) != len(b):
                reason = "JSON 陣列長度不同。"
            else:
                stack.extend(
                    (a[i], b[i], (pointer + f"/{i}")[:2048]) for i in reversed(range(len(a)))
                )
        elif a != b:
            reason = "JSON 值不同。"
        if reason:
            return False, {"reason": reason, "json_pointer": pointer[:2048]}
    return True, {}
