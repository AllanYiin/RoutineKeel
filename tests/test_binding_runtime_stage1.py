import asyncio
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flow_base.graph import builder, preparation
from flow_base.blueprints.source import inspect_blueprint
from flow_base.graph.preparation import WheelPreparation, preparation_id, prepare_wheels
from flow_base.runtime import FlowError, Runtime, atomic_json, read_json
from flow_base.studio import _studio_command, design_preflight
from routinekeel_contracts.runtime_environment import python_in
from test_toolchain_preparation import preparation_recipe


def test_binding_preflight_reports_mismatched_workspace_python_before_tool_probe(tmp_path, monkeypatch):
    environment = tmp_path / ".venv"
    environment.mkdir()
    monkeypatch.setattr(builder, "resolve_graph", lambda *_args, **_kwargs: pytest.fail("wrong Runtime probed"))

    observed = builder.preflight_graph({}, tmp_path)

    assert observed["build_status"] == "blocked"
    assert observed["bindings"] == {}
    gap, = observed["gaps"]
    assert gap["code"] == "RUNTIME_MISMATCH"
    assert gap["expected_python"] == str(python_in(environment))
    assert gap["actual_python"] == sys.executable


def test_bottom_up_studio_bridge_passes_target_workspace_python(tmp_path, monkeypatch):
    environment = tmp_path / ".venv"
    environment.mkdir()
    monkeypatch.setattr("flow_base.studio.studio_python", lambda: Path(sys.executable))

    assert _studio_command(tmp_path)[-2:] == ["--runtime-python", str(python_in(environment))]


def test_three_binding_views_use_the_same_runtime_and_gaps(tmp_path):
    from test_blueprint_completion import gap_archive
    from test_mcp_stdio import call, client_for
    from test_workspace import cli

    archive = gap_archive(tmp_path / "fixture.zip")
    payload = tmp_path / "request.json"
    atomic_json(payload, {"request_id": "stage1-runtime-consistency"})
    opened = cli(tmp_path, "blueprint-completion", "start", "--archive", archive, "--payload", payload)["result"]

    async def mcp_status():
        async with client_for(tmp_path) as client:
            return (await call(client, "blueprint_completion", operation="status", work_id=opened["work_id"]))["result"]

    assert asyncio.run(mcp_status()) == opened
    source, _ = inspect_blueprint(archive.read_bytes())
    proposal = {"graph": source["graph"], "display_text": ""}
    design_preflight(proposal, tmp_path)
    studio = proposal["build_preflight"]
    assert studio["binding_environment"] == opened["binding_environment"]
    assert {(gap["node"], gap["code"]) for gap in studio["capability_gaps"]} == {
        (gap["location"], gap["code"]) for gap in opened["gaps"] if gap["kind"] == "capability"
    }


def test_packaged_studio_validates_the_integrated_runtime_not_its_nested_python(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "workflow_studio/scripts/studio_bootstrap.py"
    bootstrap = runpy.run_path(str(path))
    prepare = bootstrap["prepare"]
    studio_root = tmp_path / "workflow_studio"
    studio_root.mkdir()
    (studio_root / ".venv").mkdir()
    expected_runtime = tmp_path / ".venv"
    expected_runtime.mkdir()

    def checked(python, environment, version, *, runtime):
        assert runtime and environment == expected_runtime and python == python_in(expected_runtime)
        return False

    monkeypatch.setitem(prepare.__globals__, "check_environment", checked)
    with pytest.raises(ValueError, match="RUNTIME_MISMATCH"):
        prepare(studio_root, {"runtime_version": "0.3.0"}, tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows short-staging recovery")
@pytest.mark.parametrize("fails_again", [False, True])
def test_uv_staging_error_uses_one_short_retry_and_preserves_diagnostics(tmp_path, monkeypatch, fails_again):
    runtime = Runtime(tmp_path / "workspace-with-a-longer-name" / "share_states")
    host = tmp_path / "trusted"
    host.mkdir()
    recipe = WheelPreparation.model_validate(preparation_recipe(host))
    # Keep the simulated fallback in the test workspace, separate from the publication directory.
    short_staging = tmp_path / "cache#"
    short_staging.mkdir()
    monkeypatch.setattr(preparation.tempfile, "tempdir", str(short_staging))
    actual_run = subprocess.run
    attempts = []

    def run(command, **kwargs):
        attempts.append(kwargs["cwd"])
        if len(attempts) == 1 or fails_again:
            return SimpleNamespace(
                returncode=2,
                stderr=b"Failed to inspect Python interpreter: failed to persist temporary file: (os error 3)",
            )
        return actual_run(command, **kwargs)

    monkeypatch.setattr(preparation.subprocess, "run", run)
    target = runtime.home / "toolchain_preparations" / preparation_id(recipe)
    if fails_again:
        with pytest.raises(FlowError) as error:
            prepare_wheels(runtime, recipe, host)
        assert error.value.code == "STAGING_PATH_UNUSABLE"
        assert error.value.details["initial_failure"]["details"]["os_error"] == 3
        assert not target.exists()
    else:
        receipt = prepare_wheels(runtime, recipe, host)
        diagnostic = read_json(target / "preparation-diagnostic.json")
        assert diagnostic["operation_id"] == receipt["operation_id"]
        assert diagnostic["initial_failure"]["details"]["stage"] == "uv_python_probe"
        assert diagnostic["initial_failure"]["details"]["os_error"] == 3
        assert prepare_wheels(runtime, recipe, host) == receipt
    assert len(attempts) == 2 and len(str(attempts[1])) < len(str(attempts[0]))
