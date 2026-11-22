"""Deterministic pure-Python wheel/ZIP creation with PEP 427 RECORD hashes."""

import base64
import csv
import hashlib
import io

import zipfile
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_wheel(path: Path, module: str, version: str, files: dict[str, bytes]):
    metadata = f"{module}-{version}.dist-info"
    files = dict(files)
    files[f"{metadata}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {module}\nVersion: {version}\nRequires-Python: >=3.11,<3.14\nRequires-Dist: flow-base>=0.2.14\n\nGenerated RoutineKeel workflow.\n"
    ).encode()
    files[f"{metadata}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: routinekeel-blueprint\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    rows = [
        (
            name,
            "sha256="
            + base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .decode()
            .rstrip("="),
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
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
