import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "studio_bootstrap", ROOT / "routinekeel/workflow_studio/scripts/studio_bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def package(root):
    root.mkdir()
    files = {
        "studio.whl": b"fixture wheel",
        "studio-requirements.txt": b"fixture lock",
        "studio_bootstrap.py": b"fixture script",
    }
    for name, content in files.items():
        (root / name).write_bytes(content)
    bundle = {
        "schema_version": 1,
        "studio_version": "0.1.0",
        "runtime_version": "0.2.12",
        "studio_wheel": "studio.whl",
        "files": {name: bootstrap.digest(root / name) for name in files},
    }
    (root / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    return bundle


def test_tampered_bundle_is_rejected_without_running_installation(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    package(root)
    (root / "studio.whl").write_bytes(b"changed")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="integrity"):
        bootstrap.verify_bundle(root)
    assert calls == []
    assert not (root / ".studio-venv").exists()


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/outside", "a\\b"])
def test_bundle_paths_cannot_escape_installation(tmp_path, name):
    root = tmp_path / "bundle"
    bundle = package(root)
    bundle["files"] = {name: "0" * 64, **bundle["files"]}
    (root / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle path"):
        bootstrap.verify_bundle(root)


def fake_installer(monkeypatch, root, *, fail=False):
    calls = []
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "fixture-os")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "fixture-cpu")
    monkeypatch.setattr(bootstrap, "find_uv", lambda _root: "fixture-uv")
    monkeypatch.setattr(
        bootstrap,
        "check_environment",
        lambda python, environment, version, runtime=False: (
            runtime or (environment / "ready").is_file()
        ),
    )

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["UV_NO_ENV_FILE"] == "1"
        assert "STUDIO_PRIVATE_TEST_KEY" not in kwargs["env"]
        target = root / ".studio-venv"
        if command[1] == "venv":
            target.mkdir()
            (target / "pyvenv.cfg").write_text("fixture", encoding="utf-8")
        elif fail:
            raise subprocess.CalledProcessError(1, command)
        elif command[1:3] == ["pip", "check"]:
            (target / "ready").touch()

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("STUDIO_PRIVATE_TEST_KEY", "never send to installer")
    return calls


def test_install_is_locked_pinned_and_second_launch_does_not_reinstall(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    bundle = package(root)
    calls = fake_installer(monkeypatch, root)
    first = bootstrap.prepare(root, bundle)
    assert len(calls) == 4
    assert "--require-hashes" in calls[1] and "--no-build" in calls[1]
    assert bootstrap.prepare(root, bundle) == first
    assert len(calls) == 4
    assert (root / "share_states/studio-environment.json").is_file()


def test_failed_update_restores_previous_environment_and_keeps_failed_copy(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    bundle = package(root)
    old = root / ".studio-venv"
    old.mkdir()
    (old / "pyvenv.cfg").write_text("previous", encoding="utf-8")
    (old / "keep.txt").write_bytes(b"old installation")
    state = root / "share_states"
    state.mkdir()
    (state / "studio-environment.json").write_text('{"old":true}', encoding="utf-8")
    fake_installer(monkeypatch, root, fail=True)
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.prepare(root, bundle)
    assert (old / "keep.txt").read_bytes() == b"old installation"
    assert (state / "studio-environment.json").read_text() == '{"old":true}'
    failed = list((state / "retired_studio_envs").glob("failed-*"))
    assert len(failed) == 1 and (failed[0] / "pyvenv.cfg").is_file()


@pytest.mark.skipif(os.name == "nt", reason="Requires POSIX symlink creation")
def test_installer_refuses_linked_state_without_writing_outside_root(tmp_path):
    root = tmp_path / "bundle"
    package(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "share_states").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Linked"):
        bootstrap.owned(root, root / "share_states/packages")
    assert list(outside.iterdir()) == []
