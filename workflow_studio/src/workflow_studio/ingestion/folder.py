"""Bounded folder/ZIP inventory; archives are never extracted to disk."""

import io
import os
import stat
import zipfile
from pathlib import Path

from ..contracts import StudioError
from .models import ImportLimits
from .security import read_file, reject_links, safe_relative


def collect_path(path: Path, limits: ImportLimits) -> dict[str, bytes]:
    path = path.expanduser().absolute()
    reject_links(path)
    files, total = {}, 0

    def add(name, data):
        nonlocal total
        name = safe_relative(name)
        if name.casefold() in {n.casefold() for n in files}:
            raise StudioError("INPUT_INVALID", "來源包含重複或大小寫衝突路徑。")
        total += len(data)
        if len(files) >= limits.max_files or total > limits.max_total_bytes:
            raise StudioError("INPUT_INVALID", "來源檔案數量或總大小超過限制。")
        files[name] = data

    if path.is_dir():
        root = path.resolve()
        for current, dirs, names in os.walk(root, followlinks=False):
            for child in sorted(dirs + names):
                candidate = Path(current) / child
                reject_links(candidate)
                safe_relative(candidate.relative_to(root).as_posix())
            for name in sorted(names):
                candidate = Path(current) / name
                add(
                    candidate.relative_to(root).as_posix(),
                    read_file(candidate, root, limits.max_file_bytes),
                )
    elif path.suffix.lower() in {".zip", ".skill"}:
        data = read_file(path, path.parent, limits.max_total_bytes)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > limits.max_files:
                    raise StudioError("INPUT_INVALID", "ZIP 項目數超過限制。")
                declared_total = 0
                for info in infos:
                    safe_relative(
                        info.orig_filename.rstrip("/") if info.is_dir() else info.orig_filename
                    )
                    safe_relative(info.filename.rstrip("/") if info.is_dir() else info.filename)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or (
                        stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    ):
                        raise StudioError("INPUT_INVALID", "ZIP 含有連結或特殊檔案。")
                    if info.is_dir():
                        continue
                    declared_total += info.file_size
                    if (
                        info.file_size > limits.max_file_bytes
                        or declared_total > limits.max_total_bytes
                        or info.file_size > max(info.compress_size, 1) * limits.max_zip_ratio
                        or info.flag_bits & 1
                    ):
                        raise StudioError(
                            "INPUT_INVALID", "ZIP 大小、壓縮比例或加密方式不符合匯入限制。"
                        )
                    with archive.open(info) as stream:
                        payload = stream.read(limits.max_file_bytes + 1)
                    if len(payload) != info.file_size:
                        raise StudioError("INPUT_INVALID", "ZIP 檔案大小不符。")
                    add(info.filename, payload)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise StudioError("SOURCE_PREVIEW_UNAVAILABLE", "ZIP 無法完整讀取。") from exc
    else:
        add(path.name, read_file(path, path.parent, limits.max_file_bytes))
    if not files:
        raise StudioError("INPUT_INVALID", "來源資料夾或壓縮檔沒有可預覽檔案。")
    return files
