"""Failure boundaries for the first-install transaction and immutable review tasks."""

import asyncio
import json
from pathlib import Path

import pytest
from flow_base.blueprints import installer
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError
from test_blueprint_install import apply, blueprint, candidate, case, finish, resolution


@pytest.mark.parametrize("point", ["before_switch", "after_switch"])
def test_interruption_has_no_partial_workflow_and_retry_is_idempotent(
    tmp_path, monkeypatch, point
):
    service, draft, built = candidate(tmp_path)
    rename, atomic = Path.rename, installer.atomic_json

    def interrupt_rename(path, dest):
        if path.name == "package" and point == "before_switch":
            raise OSError("fixture switch failure")
        return rename(path, dest)

    def interrupt_receipt(path, value):
        if path.name == "installation.json" and point == "after_switch":
            raise OSError("fixture receipt failure")
        return atomic(path, value)

    with monkeypatch.context() as m:
        m.setattr(Path, "rename", interrupt_rename)
        m.setattr(installer, "atomic_json", interrupt_receipt)
        with pytest.raises(OSError):
            apply(service, draft, built)
    assert bool(service.workspace.discover(read_only=True)) == (point == "after_switch")
    assert apply(service, draft, built)["status"] == (
        "already_installed" if point == "after_switch" else "installed"
    )


def test_approved_profile_reuse_requires_original_inputs_and_version(tmp_path):
    service = WorkflowService(tmp_path)
    draft = service.install_workflow(archive=blueprint(tmp_path / "sample.zip"))[
        "draft_id"
    ]
    recipe = resolution()
    recipe["steps"]["1"].update(reuse_approved=True, profile_config=["character"])
    trial = case()
    trial["config"] = {"character": "a"}
    built = service.install_workflow(draft_id=draft, resolution=recipe, cases=[trial])
    apply(service, draft, built)

    def start(name, req):
        source = tmp_path / (name + ".json")
        source.write_text(json.dumps(req))
        return service.run_workflow_sync({"task": "example_flow", "source": source})[
            "blueprint"
        ]

    first = start("one", {"inputs": {"text": "one"}, "config": {"character": "a"}})
    approved = finish(
        service, first, "complete", evidence={"actor": "person", "decision": "approved"}
    )
    second = start(
        "two",
        {
            "profile_id": approved["profile_id"],
            "inputs": {},
            "config": {"character": "a"},
        },
    )
    assert second["pending"]["kind"] == "external" and second["pending"]["inputs"] == {
        "draft": "ONE"
    }
    with pytest.raises(FlowError):
        start(
            "three",
            {
                "profile_id": approved["profile_id"],
                "inputs": {"text": "edited"},
                "config": {"character": "a"},
            },
        )


def test_missing_capability_and_tampered_candidate_are_blocked(tmp_path):
    service, draft, built = candidate(tmp_path)
    recipe = resolution()
    del recipe["bindings"]["post"]["probe"]
    with pytest.raises(FlowError):
        service.install_workflow(draft_id=draft, resolution=recipe, cases=[case()])
    Path(built["archive"]).write_bytes(b"bad zip")
    with pytest.raises(FlowError):
        apply(service, draft, built)
    assert not service.workspace.discover(read_only=True)


def test_studio_validator_and_runtime_language_compatibility(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    studio = root / "workflow_studio/src"
    monkeypatch.syspath_prepend(str(studio))
    from flow_base.blueprints import program

    assert Path(program.__file__).read_text(encoding="utf-8") == (
        studio / "workflow_studio/compiler/runtime_program.py"
    ).read_text(encoding="utf-8")
    _, _, built = candidate(tmp_path)
    assert built["evidence"]["studio_ir_and_record"] == "passed"


def test_official_mcp_first_install_and_reconnect(tmp_path):
    pytest.importorskip("mcp")
    from test_mcp_stdio import client_for, call

    source = blueprint(tmp_path / "fixture.zip")

    async def exercise():
        async with client_for(tmp_path) as client:
            draft = await call(client, "install_workflow", archive=source.name)
            key = draft["result"]["draft_id"]
            info = await call(
                client, "install_workflow", draft_id=key, member="flow.json"
            )
            assert json.loads(info["result"]["text"])["id"] == "example_flow"
            built = await call(
                client,
                "install_workflow",
                draft_id=key,
                resolution=resolution(),
                cases=[case()],
            )
            assert built["ok"], built
        async with client_for(tmp_path) as client:
            args = {
                k: built["result"][k] for k in ("draft_id", "build_id", "install_hash")
            }
            installed = await call(client, "install_workflow", **args)
            assert installed["result"]["status"] == "installed"

    asyncio.run(exercise())
