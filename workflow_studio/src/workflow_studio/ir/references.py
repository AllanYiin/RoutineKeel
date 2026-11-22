import re
from dataclasses import dataclass

from ..contracts import StudioError

IDENTIFIER = r"[a-z][a-z0-9_-]{0,63}"
REFERENCE = re.compile(rf"^(input|artifact|output|secret)://({IDENTIFIER})(?:/({IDENTIFIER}))?$")


@dataclass(frozen=True)
class Reference:
    scheme: str
    owner: str
    port: str | None


def parse_reference(value: str) -> Reference:
    match = REFERENCE.fullmatch(value)
    if not match or (match[1] == "artifact") != (match[3] is not None):
        raise StudioError("IR_SCHEMA_INVALID", f"不合法的邏輯引用：{value}")
    return Reference(*match.groups())
