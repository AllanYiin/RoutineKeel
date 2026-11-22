import re
from pathlib import PurePosixPath

from .errors import StudioError

RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)


def safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or any(part in {".", ".."} for part in path.parts)
        or any(c in name for c in '\\:\x00<>"|?*')
        or any(ord(c) < 32 for c in name)
        or any(part.endswith((".", " ")) or RESERVED.match(part) for part in path.parts)
    ):
        raise StudioError("INPUT_INVALID", "來源含有不安全或跨平台不相容的相對路徑。")
    return name
