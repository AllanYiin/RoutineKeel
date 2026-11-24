import json
import zipfile
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError


def data_graph():
    text = {"type": "string"}

    def task(key, source, tool):
        return {
            "id": key,
            "kind": "task",
            "inputs": {"source": {"source": source, "port": text}},
            "outputs": {"result": text},
            "capability": tool,
            "capability_version": "1",
            "permissions": [],
            "side_effect": "none",
        }

    return {
        "blueprint_schema_version": 2,
        "workflow": {"id": "graph_copy", "goal": "產生兩種文字", "revision": 1, "version": "1.0.0"},
        "inputs": {"text": text},
        "nodes": [
            task("upper", "artifact://copy/result", "text.upper"),
            task("copy", "input://text", "text.copy"),
        ],
        "outputs": {"original": "artifact://copy/result", "upper": "artifact://upper/result"},
        "provenance": {},
        "quality_contract": {
            "golden_cases": [
                {"inputs": {"text": "ok"}, "expected": {"original": "ok", "upper": "OK"}}
            ]
        },
    }


def import_graph(ws, graph):
    path = ws / "source.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("graph/blueprint.json", json.dumps(graph))
    service = WorkflowService(ws)
    key = service.install_workflow(archive=path)["draft_id"]
    report = service.resolve_blueprint_capabilities(key)
    return service, key, {"capability_fingerprint": report["fingerprint"]}


def test_only_validated_graph_build_can_install_and_run(tmp_path):
    svc, key, res = import_graph(tmp_path, data_graph())
    built = svc.install_workflow(draft_id=key, resolution=res)
    assert built["status"] == "verified" and built["validation"]["status"] == "PASS"
    with (
        zipfile.ZipFile(tmp_path / "source.zip") as design,
        zipfile.ZipFile(built["archive"]) as candidate,
    ):
        assert design.namelist() == ["graph/blueprint.json"]
        assert any(name.endswith(".whl") for name in candidate.namelist())
    assert built["archive_sha256"] != key
    assert built["builder_receipt"]["package_hash"] == built["archive_sha256"]
    assert not svc.workspace.discover(read_only=True)
    assert (
        svc.install_workflow(
            draft_id=key, build_id=built["build_id"], install_hash=built["install_hash"]
        )["status"]
        == "installed"
    )
    request = tmp_path / "input.json"
    request.write_text(json.dumps({"inputs": {"text": "fresh"}}))
    result = svc.run_workflow_sync({"task": "graph_copy", "source": request})
    assert result["blueprint"]["outputs"] == {"original": "fresh", "upper": "FRESH"}
    assert (
        json.loads((Path(result["output"]) / "artifacts/results.json").read_bytes())
        == result["blueprint"]["outputs"]
    )


@pytest.mark.parametrize("fault", ["unsupported", "drift", "wrong_gold", "omit_gold"])
def test_graph_gates_preserve_uninstalled_candidate(tmp_path, fault):
    graph = data_graph()
    if fault == "unsupported":
        graph["nodes"][0]["parameters"] = {"unknown": True}
    if fault == "wrong_gold":
        graph["quality_contract"]["golden_cases"][0]["expected"]["upper"] = "wrong"
    svc, key, res = import_graph(tmp_path, graph)
    if fault == "drift":
        res["capability_fingerprint"] = "different"
    with pytest.raises(FlowError):
        svc.install_workflow(
            draft_id=key, resolution=res, cases=[] if fault == "omit_gold" else None
        )
    assert svc.install_workflow(draft_id=key)["builds"][0]["status"] == "blocked"
    assert not svc.workspace.discover(read_only=True)


def test_desktop_design_export_and_builder_use_same_graph_and_receipt(tmp_path, workflow_design_review):
    from flow_base.studio import studio_request
    from routinekeel_contracts.builder import fingerprint

    graph = data_graph()
    graph["quality_contract"]["success_criteria"] = ["文字一致"]
    project = studio_request(tmp_path, "create", {"name": "文字工作", "task": "產生兩種文字"})
    pid = project["project_id"]
    proposal = studio_request(
        tmp_path,
        "prepare_blueprint",
        {
            "project_id": pid,
            "expected_revision": project["revision"],
            "request_id": "preview",
            "graph": graph,
            "design": {"rules": "保留原文"},
        },
    )
    assert proposal["build_preflight"] == {
        "status": "ready", "blocks_blueprint_export": False, "capability_gaps": [], "build_gaps": []
    }
    proposal = studio_request(tmp_path, "finalize_workflow_design", {
        "project_id": pid, "expected_revision": proposal["revision"], "request_id": "finalize",
        "draft_id": proposal["proposal_id"], "review": workflow_design_review(proposal),
        "draft_reply": "初稿可進入定稿",
        "shown_sections": {key: item["sha256"] for key, item in proposal["review_sections"].items()},
    })
    exported = studio_request(
        tmp_path,
        "export_blueprint",
        {
            "project_id": pid,
            "expected_revision": proposal["revision"],
            "request_id": "reply",
            "proposal_id": proposal["proposal_id"],
            "decision": "approve",
            "user_text": "可匯出",
            "review_hash": proposal["review_hash"],
            "shown_sections": {key: item["sha256"] for key, item in proposal["review_sections"].items()},
        },
    )
    svc = WorkflowService(tmp_path)
    draft = svc.install_workflow(archive=exported["path"])
    assert draft["draft_id"] == exported["sha256"]
    bindings = svc.resolve_blueprint_capabilities(draft["draft_id"])
    build = svc.install_workflow(
        draft_id=draft["draft_id"], resolution={"capability_fingerprint": bindings["fingerprint"]}
    )
    source = svc.install_workflow(draft_id=draft["draft_id"])["source"]
    assert build["builder_receipt"]["graph_hash"] == fingerprint(source["graph"])


def test_preflight_reports_both_unbound_tools_and_description_only_trials(tmp_path):
    graph = data_graph()
    graph["nodes"][0].update(capability="unregistered", parameters={"keep_timeline": True})
    graph["quality_contract"]["golden_cases"] = ["保留來源時間軸"]
    service, key, _ = import_graph(tmp_path, graph)
    report = service.resolve_blueprint_capabilities(key)
    assert report["build_status"] == "blocked"
    assert [gap["node"] for gap in report["gaps"]] == ["upper"]
    assert [(gap["code"], gap["case"]) for gap in report["build_gaps"]] == [
        ("TRIAL_CASE_NOT_EXECUTABLE", 1)
    ]

    graph["nodes"][0].update(capability="text.upper", parameters={})
    service, key, resolution = import_graph(tmp_path, graph)
    assert service.resolve_blueprint_capabilities(key)["status"] == "ready"
    with pytest.raises(FlowError) as error:
        service.install_workflow(draft_id=key, resolution=resolution)
    assert "inputs" in error.value.details["reason"]
    build = service.install_workflow(draft_id=key)["builds"][0]
    assert build["status"] == "blocked" and build["validation"]["status"] == "NOT_RUN"
