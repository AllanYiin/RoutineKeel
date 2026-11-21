"""Deterministic pure-Python wheel/ZIP creation with PEP 427 RECORD hashes."""

import base64
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

from ..contracts import StudioError


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_wheel(path: Path, module: str, version: str, files: dict[str, bytes]):
    metadata = f"{module}-{version}.dist-info"
    files = dict(files)
    files[f"{metadata}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {module}\nVersion: {version}\nRequires-Python: >=3.11,<3.14\n\nGenerated RoutineKeel workflow.\n"
    ).encode()
    files[f"{metadata}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: workflow-studio\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    rows = [
        (
            name,
            "sha256="
            + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="),
            str(len(data)),
        )
        for name, data in sorted(files.items())
    ]
    rows.append((f"{metadata}/RECORD", "", ""))
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    files[f"{metadata}/RECORD"] = output.getvalue().encode()
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)


def verify_package(folder: Path):
    manifest = json.loads((folder / "flow.json").read_text(encoding="utf-8"))
    wheel = folder / manifest["package"]
    if (
        wheel.parent != folder
        or wheel.is_symlink()
        or digest(wheel.read_bytes()) != manifest["sha256"]
    ):
        raise StudioError("CONTRACT_MISMATCH", "流程包完整性不符。")
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise StudioError("CONTRACT_MISMATCH", "流程 wheel 已損毀。")
        names = archive.namelist()
        records = [n for n in names if n.endswith(".dist-info/RECORD")]
        if len(records) != 1 or len(names) != len(set(names)):
            raise StudioError("CONTRACT_MISMATCH", "流程 wheel 結構不正確。")
        rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
        if {r[0] for r in rows} != set(names):
            raise StudioError("CONTRACT_MISMATCH", "wheel RECORD 不完整。")
        for name, hashed, size in rows:
            if name == records[0]:
                continue
            data = archive.read(name)
            actual = "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(data).digest()
            ).decode().rstrip("=")
            if hashed != actual or size != str(len(data)):
                raise StudioError("CONTRACT_MISMATCH", "wheel RECORD 指紋不符。")
    return manifest
