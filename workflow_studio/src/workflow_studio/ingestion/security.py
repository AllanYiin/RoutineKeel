import stat
from pathlib import Path

from routinekeel_contracts.paths import RESERVED as RESERVED
from routinekeel_contracts.paths import safe_relative as safe_relative

from ..contracts import StudioError


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
