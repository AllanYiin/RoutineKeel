import importlib.util
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_builder(monkeypatch):
    tools = Path(__file__).resolve().parents[2] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    spec = importlib.util.spec_from_file_location(
        "studio_release_tests", tools / "studio_release.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def test_studio_is_not_a_domain_workflow_example(monkeypatch):
    load_builder(monkeypatch)
    import release

    projects = release.discover_projects()
    assert release.STUDIO not in projects
    assert release.BASE in projects
    assert all((p / "flow.template.json").is_file() for p in projects if p != release.BASE)


@pytest.mark.parametrize("integrated", [False, True])
def test_packaged_studio_reuses_runtime_python_and_leaves_legacy_env(
    tmp_path, monkeypatch, integrated
):
    source = Path(__file__).resolve().parents[2] / "workflow_studio/scripts/studio_bootstrap.py"
    spec = importlib.util.spec_from_file_location("studio_bootstrap_tests", source)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    root = (tmp_path / "workflow_studio").resolve()
    workspace = root.parent if integrated else root
    state = workspace / "share_states"
    if integrated:
        (workspace / "workflows").mkdir()
        state = workspace / "flow_base/share_states"
    state.mkdir(parents=True)
    venv = workspace / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("already prepared")
    legacy = root / ".studio-venv/keep.txt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy")
    hashes = dict.fromkeys(("studio.whl", "contracts.whl", "studio-requirements.txt"), "fixture")
    bundle = {
        "runtime_version": "0.3.0",
        "studio_version": "0.3.0",
        "studio_wheel": "studio.whl",
        "contracts_wheel": "contracts.whl",
        "files": hashes,
    }
    monkeypatch.setattr(bootstrap, "check_environment", lambda *args, **kwargs: True)
    monkeypatch.setattr(bootstrap, "find_uv", lambda *args: "uv")
    calls = []

    def run(command, *, env, **kwargs):
        calls.append(command)
        assert (state / "bootstrap.lock").is_dir()
        assert env["UV_CACHE_DIR"] == str(state / "packages")
        assert env["UV_PYTHON_DOWNLOADS"] == "never"

    monkeypatch.setattr(bootstrap, "subprocess", SimpleNamespace(run=run))
    assert bootstrap.prepare(root, bundle, workspace) == python
    assert bootstrap.prepare(root, bundle, workspace) == python
    assert len(calls) == 3
    assert all(command[1] == "pip" for command in calls)
    assert all(command[command.index("--python") + 1] == str(python) for command in calls)
    assert legacy.read_text() == "legacy"
    assert python.read_text() == "already prepared"
    assert not (state / "bootstrap.lock").exists()

    def failed_install(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "uv")

    changed = {**bundle, "files": {**hashes, "studio.whl": "changed"}}
    monkeypatch.setattr(bootstrap.subprocess, "run", failed_install)
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.prepare(root, changed, workspace)
    assert not (state / "bootstrap.lock").exists()
    assert python.read_text() == "already prepared"
    marker = json.loads((root / "share_states/studio-environment.json").read_text(encoding="utf-8"))
    assert marker["wheel"] == "fixture"


def test_public_documents_preserve_urls_but_remove_machine_paths(monkeypatch):
    builder = load_builder(monkeypatch)
    urls = "https://example.invalid/docs http://example.invalid/help"
    document = urls + "\nWindows E:/private-machine/run.log and `C:\\Users\\fixture\\data.txt`"
    public = builder.public_document(document, set())
    assert urls in public
    assert "private-machine" not in public and "fixture" not in public
    assert public.count("{local-path}") == 2


@pytest.mark.parametrize("fault", ["untracked", "changed", "machine-path"])
def test_wheel_cannot_add_untracked_or_private_material(tmp_path, monkeypatch, fault):
    builder = load_builder(monkeypatch)
    component = tmp_path / "workflow_studio"
    source = component / "src/workflow_studio/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# source")
    wheel = tmp_path / "fixture.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "workflow_studio/__init__.py", b"# changed" if fault == "changed" else b"# source"
        )
        if fault == "untracked":
            archive.writestr("workflow_studio/private.json", b"PRIVATE_SENTINEL")
        elif fault == "machine-path":
            archive.writestr("workflow_studio-0.1.0.dist-info/METADATA", str(tmp_path).encode())
    with pytest.raises(ValueError):
        builder.verify_wheel_sources(
            wheel, tmp_path, component, {source.relative_to(tmp_path).as_posix()}, {tmp_path}
        )


def test_archive_inventory_excludes_even_tracked_private_state(tmp_path, monkeypatch):
    builder = load_builder(monkeypatch)
    root = tmp_path / "repo"
    project, base = root / "workflow_studio", root / "flow_base"
    entries = {
        "LICENSE": "fixture license",
        "assets/routinekeel-logo.png": "original logo fixture",
        "flow_base/pyproject.toml": '[project]\nversion="0.2.12"\n',
        "flow_base/src/flow_base/__init__.py": "# runtime source",
        "flow_base/components/workflow_contracts/pyproject.toml": '[project]\nversion="1.0.0"\n',
        "flow_base/components/workflow_contracts/src/routinekeel_contracts/__init__.py": "# shared validator",
        "workflow_studio/pyproject.toml": '[project]\nversion="0.1.0"\n',
        "workflow_studio/scripts/studio_bootstrap.py": "# trusted fixture",
        "workflow_studio/docs/example.md": (
            "Evidence at E:/private-machine/run/file.log\n"
            "[documentation](https://example.invalid/guide)"
        ),
        "workflow_studio/examples/first-copy/input/a.txt": "sample",
        "workflow_studio/src/workflow_studio/__init__.py": "# source",
        "workflow_studio/.env": "PRIVATE_SENTINEL",
        "workflow_studio/specs/requirements.txt": "PRIVATE_SENTINEL",
        "workflow_studio/share_states/private.json": "PRIVATE_SENTINEL",
        "workflow_studio/share_logs/private.json": "PRIVATE_SENTINEL",
        "workflow_studio/outputs/private.txt": "PRIVATE_SENTINEL",
        "workflow_studio/settings.json": "PRIVATE_SENTINEL",
    }
    for name, text in entries.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    staging = tmp_path / "build"
    (staging / "release").mkdir(parents=True)

    def git(command, **kwargs):
        if command[1] == "diff":
            return b""
        if command[1] == "ls-files":
            return "\0".join(entries).encode()
        return str(root / ".git") if command[-1] == "--git-common-dir" else "f" * 40

    monkeypatch.setattr(builder.subprocess, "check_output", git)

    def run(command, **kwargs):
        if command[1] == "build":
            wheels = Path(command[command.index("--out-dir") + 1])
            wheels.mkdir(exist_ok=True)
            name = "flow_base-0.2.12" if kwargs["cwd"] == base else "workflow_studio-0.1.0"
            module = "flow_base" if kwargs["cwd"] == base else "workflow_studio"
            if kwargs["cwd"].name == "workflow_contracts":
                module, name = "routinekeel_contracts", "routinekeel_contracts-1.0.0"
            with zipfile.ZipFile(wheels / (name + "-py3-none-any.whl"), "w") as wheel:
                wheel.write(kwargs["cwd"] / "src" / module / "__init__.py", module + "/__init__.py")
        else:
            Path(command[-1]).write_text("fixture lock", encoding="utf-8")

    def boot(destination, wheel, version, *, contracts_wheel):
        assert (destination / contracts_wheel.name).is_file()
        (destination / "start.sh").write_text("#!/bin/sh\nexit 0\n")
        (destination / "start.ps1").write_text("exit 0\n")

    result = builder.build_studio(
        project, staging, root=root, base=base, run=run, write_bootstrap=boot
    )
    with zipfile.ZipFile(staging / "release" / result["file"]) as archive:
        manifest = json.loads(archive.read("workflow_studio/bundle.json"))
        assert not manifest["source_dirty"]
        assert set(archive.namelist()) == {
            "workflow_studio/" + name
            for name in [*manifest["files"], "bundle.json", "bundle.sha256"]
        }
        assert (
            archive.read("workflow_studio/assets/routinekeel-logo.png") == b"original logo fixture"
        )
        for name in archive.namelist():
            assert b"PRIVATE_SENTINEL" not in archive.read(name)
            assert b"E:/private-machine" not in archive.read(name)
        assert b"https://example.invalid/guide" in archive.read("workflow_studio/docs/example.md")
        with zipfile.ZipFile(io.BytesIO(archive.read("workflow_studio/source.zip"))) as source:
            assert "workflow_studio/src/workflow_studio/__init__.py" in source.namelist()
            for name in source.namelist():
                assert b"PRIVATE_SENTINEL" not in source.read(name), name
                assert b"E:/private-machine" not in source.read(name)
            assert b"https://example.invalid/guide" in source.read(
                "workflow_studio/docs/example.md"
            )
