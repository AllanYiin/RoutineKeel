import re
import stat
from pathlib import Path, PurePosixPath

from ..contracts import StudioError

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


def reject_links(path: Path):
    # Reparse points include Windows junctions, not just Python symbolic links.
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise StudioError("INPUT_INVALID", "不匯入符號連結或重新解析點。")


def read_file(path: Path, root: Path, limit: int) -> bytes:
    reject_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise StudioError("INPUT_INVALID", "來源超出選取資料夾。")
    if not path.is_file() or path.stat().st_size > limit:
        raise StudioError("INPUT_INVALID", "來源不是一般檔案或已超過單檔大小限制。")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise StudioError("INPUT_INVALID", "來源已超過單檔大小限制。")
    reject_links(path)
    return data
