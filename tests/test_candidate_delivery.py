import hashlib
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from package_paths import RELEASE, WORKSPACE

ROOT = Path(__file__).resolve().parents[1]
LINKS = ROOT / "tools/check_document_links.py"


def test_all_delivered_documents_and_archive_hashes():
    result = subprocess.run(
        [sys.executable, str(LINKS), str(WORKSPACE)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = json.loads((RELEASE / "versions.json").read_text(encoding="utf8"))
    sums = dict(
        line.split(maxsplit=1)[::-1] for line in (RELEASE / "SHA256SUMS").read_text().splitlines()
    )
    assert len(sums) == len(records) == 5
    for record in records:
        archive = RELEASE / record["file"]
        assert archive.stat().st_size == record["bytes"]
        assert (
            hashlib.sha256(archive.read_bytes()).hexdigest()
            == sums[archive.name]
            == record["sha256"]
        )
        with ZipFile(archive) as z:
            assert z.testzip() is None
    assert (WORKSPACE / "flow_base/workflow-evolution.md").is_file()
    assert (WORKSPACE / "flow_base/known-issues.md").is_file()


def test_document_links_resolve_fragment_and_skip_code_examples(tmp_path):
    doc = tmp_path / "guide.md"
    svg = tmp_path / "sample.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g id="actual"/></svg>')
    doc.write_text(
        "# 操作指南\n\n[段落](#操作指南) · [圖](sample.svg#actual)\n\n```text\n[x](missing.txt)\n```\n",
        encoding="utf8",
    )
    cmd = [sys.executable, str(LINKS), str(tmp_path)]
    assert subprocess.run(cmd, capture_output=True, check=False).returncode == 0
    doc.write_text(
        "[段落](#missing) · [圖](sample.svg#missing) · [檔案](missing.txt)", encoding="utf8"
    )
    denied = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert denied.returncode and "3 missing" in denied.stdout


def test_failed_named_build_leaves_the_previous_package_set_intact(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import release

    dist = tmp_path / "dist"
    old = dist / "release" / "previous.zip"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"previous")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release, "discover_projects", list)

    def fail_build(*_):
        raise RuntimeError("injected packaging failure")

    monkeypatch.setattr(release, "build_project", fail_build)
    monkeypatch.setattr(sys, "argv", ["release.py", "--candidate", "new"])
    with pytest.raises(RuntimeError, match="injected packaging failure"):
        release.main()
    assert old.read_bytes() == b"previous" and not (dist / "candidate-new").exists()
    for name in ("../escape", "existing"):
        (dist / "candidate-existing").mkdir(exist_ok=True)
        monkeypatch.setattr(sys, "argv", ["release.py", "--candidate", name])
        with pytest.raises(SystemExit) as invalid:
            release.main()
        assert invalid.value.code == 2 and old.read_bytes() == b"previous"
