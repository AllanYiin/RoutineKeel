"""Explicit previous trusted templates, so an app upgrade does not invalidate releases."""

import hashlib
import re
from pathlib import Path

from ..contracts import StudioError

ROOT = Path(__file__).parent
LEGACY = {"v1": ROOT / "templates/v1"}


def canonical_code(content):
    return re.sub(rb"\r+\n", b"\n", content).replace(b"\r", b"\n")


def verify_template(name, content):
    if canonical_code(content) == canonical_code((ROOT / name).read_bytes()):
        return "current"
    for version, folder in LEGACY.items():
        path = folder / (name + ".txt")
        if path.is_file() and canonical_code(content) == canonical_code(path.read_bytes()):
            return version
    raise StudioError("TEST_FAILED", "執行程式不是已驗證的受控模板。")


def package_namespace(workflow_id, ir_hash, *, has_llm=False, has_program=False):
    names = ["runtime_adapter.py"]
    if has_llm:
        names.append("runtime_llm.py")
    if has_program:
        names.append("runtime_program.py")
    digest = hashlib.sha256(ir_hash.encode())
    for name in names:
        digest.update(canonical_code((ROOT / name).read_bytes()))
    return "studio_" + workflow_id.replace("-", "_") + "_" + digest.hexdigest()[:20]
