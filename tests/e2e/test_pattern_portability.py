"""Cross-host Runtime and Studio acceptance using an isolated stdio fixture."""

import asyncio
import json
import os
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from importlib.util import find_spec
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.external import record_observation
from flow_base.graph.portable import ADAPTER_GROUP
from flow_base.graph.qualification import BindingReview
from flow_base.studio import studio_request
from routinekeel_contracts.domain_assets import content_hash

from tools.reference_packs import write_reference_pack

studio_module = find_spec("workflow_studio")
if studio_module is None or studio_module.origin is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workflow_studio/src"))

TEXT = {"type": "string", "required": True}
ROWS = {"type": "array", "required": True}
CASES = [
    ("invoice", "id,amount\nA,0.10\n", [{"id": "A", "amount": "0.10"}]),
    ("different", "id,amount\nB,1.00\n", [{"id": "B", "amount": "1.00"}]),
    ("boundary", "id,amount\n", []),
    ("negative", "id,amount\nC,001.20\n", [{"id": "C", "amount": "001.20"}]),
]


def _mcp_result(tmp_path):
    pytest.importorskip("mcp", reason="Cross-host fixture needs the optional MCP stdio SDK")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    script = tmp_path / "sheet_fixture.py"
    script.write_text(
        "import csv, io\n"
        "from mcp.server import MCPServer\n"
        "mcp = MCPServer('isolated-sheet-fixture')\n"
        "@mcp.tool()\n"
        "def read_rows(source: str) -> dict[str, list[dict[str, str]]]:\n"
        "    return {'rows': list(csv.DictReader(io.StringIO(source)))}\n"
        "mcp.run(transport='stdio')\n",
        encoding="utf-8",
    )

    async def read():
        params = StdioServerParameters(command=sys.executable, args=["-I", str(script)])
        async with (
            stdio_client(params) as (incoming, outgoing),
            ClientSession(incoming, outgoing) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["read_rows"]
            result = await session.call_tool("read_rows", {"source": CASES[0][1]})
            assert not result.is_error
            return result.structured_content or json.loads(result.content[0].text)

    return asyncio.run(read())


def _observe(path, items):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                "tools": items,
            }
        ),
        encoding="utf-8",
    )


def _tool(tool):
    return {
        "tool": tool,
        "version": "1.0.0",
        "receipt_id": "e2e-observed-tool",
        "available": True,
        "allowed": True,
        "platforms": [sys.platform],
        "permissions": [],
        "network": False,
        "side_effect": "none",
        "inputs": {"source": TEXT},
        "outputs": {"rows": ROWS},
        "resources": {"cost_micro_usd": 0},
    }


def _import(service, graph, filename):
    path = service.workspace.root / filename
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("design/blueprint.json", json.dumps(graph))
    return service.install_workflow(archive=path)["draft_id"]


def _build_install(service, draft):
    capability = service.resolve_blueprint_capabilities(draft)
    assert capability["status"] == "ready", capability
    built = service.install_workflow(
        draft_id=draft, resolution={"capability_fingerprint": capability["fingerprint"]}
    )
    outcome = service.install_workflow(
        draft_id=draft, build_id=built["build_id"], install_hash=built["install_hash"]
    )
    assert outcome["status"] == "installed"
    return built


def _source(service, rows):
    graph = {
        "blueprint_schema_version": 2,
        "workflow": {"id": "sheet_source", "version": "1.0.0", "revision": 1, "goal": "讀取明細"},
        "inputs": {"csv": TEXT},
        "nodes": [
            {
                "id": "read_sheet",
                "kind": "task",
                "capability": "fixture.read_rows",
                "capability_version": "1.0.0",
                "permissions": [],
                "side_effect": "none",
                "inputs": {"source": {"source": "input://csv", "port": TEXT}},
                "outputs": {"rows": ROWS},
            }
        ],
        "outputs": {"rows": "artifact://read_sheet/rows"},
        "provenance": {},
        "quality_contract": {
            "golden_cases": [
                {
                    "inputs": {"csv": CASES[0][1]},
                    "expected": rows,
                    "replies": [{"step": "read_sheet", "kind": "host", "outputs": rows}],
                }
            ]
        },
    }
    source = _import(service, graph, "source.zip")
    installed = _build_install(service, source)
    work = service.workspace.root / "mcp-run.json"
    work.write_text(json.dumps({"inputs": {"csv": CASES[0][1]}}))
    run = service.run_workflow_sync({"task": "sheet_source", "source": work})
    view = run["blueprint"]
    pending = view["pending"]
    task_args = {
        "task_id": pending["id"],
        "revision": view["revision"],
        "scope_hash": pending["scope_hash"],
        "payload_hash": pending["payload_hash"],
    }
    claimed = service.blueprint_task(view["session_id"], "claim", task_args)
    record_observation(
        service.workspace.root,
        pending["idempotency_key"],
        pending["scope_hash"],
        "local-mcp-call-completed",
        "succeeded",
        rows,
    )
    state = service.blueprint_task(
        view["session_id"], "complete", {**task_args, "revision": claimed["revision"]}
    )
    assert state["outputs"] == rows
    return source, installed, run["run_id"]


def _pack_candidate(extracted, path, version="1.0.0"):
    from tools.reference_packs import data_reference

    pattern = extracted["pattern"]
    pattern["pattern"]["version"] = version
    pattern["triggers"]["design"].update(
        task_categories=["reporting"],
        intents=["read_tabular_source"],
        positive_examples=["讀取本機 CSV"],
        negative_examples=["僅複製檔案"],
        prerequisites=["source_csv"],
    )
    definitions = data_reference()[1]
    definition = next(item for item in definitions if item["id"] == "tabular.read")
    records = [
        (
            name,
            category,
            edge,
            {"in_1": text},
            {"rows": rows},
            [{"step": "read_sheet", "kind": "host", "outputs": {"rows": rows}}],
        )
        for (name, text, rows), category, edge in zip(
            CASES,
            ["positive", "positive", "boundary", "negative"],
            ["normal", "normal", "boundary", "negative"],
            strict=True,
        )
    ]
    write_reference_pack(
        path, "portable_e2e", (pattern, [definition], records), pack_version=version
    )
    return path


def _admit_pack(workspace, archive, pattern_ref, *, activate):
    from workflow_studio.patterns.qualification import qualify
    from workflow_studio.registry.packs import CatalogDecision
    from workflow_studio.service import StudioService

    with StudioService.open_workspace(workspace) as studio:
        registry = studio.tools.packs
        pending = registry.register(archive)
        item = registry.decide(
            CatalogDecision(
                "approve",
                pending["ref"],
                pending["manifest_hash"],
                pending["revision"],
                "acceptance-fixture",
                "simulated local Pack decision",
                "local_terminal",
            )
        )
        result = qualify(studio, item["ref"], pattern_ref, runtime_python=sys.executable)
        assert result["verdict"] == "PASS", result
        if activate:
            target = registry.pattern_status(pattern_ref)
            registry.decide_pattern(
                CatalogDecision(
                    "approve",
                    pattern_ref,
                    target["content_hash"],
                    target["revision"],
                    "acceptance-fixture",
                    "simulated local Pattern decision",
                    "local_terminal",
                    result["qualification_hash"],
                )
            )
        return item, result


def _target(service, workflow_id, pattern_ref, workflow_design_review):
    root = service.workspace.root
    project = studio_request(root, "create", {"name": workflow_id, "task": "讀取本機 CSV"})
    pid = project["project_id"]
    offer = studio_request(
        root,
        "recommend_patterns",
        {
            "project_id": pid,
            "expected_revision": project["revision"],
            "request_id": "recommend",
            "context": {
                "intent": "讀取本機 CSV",
                "category": "reporting",
                "available_inputs": {"csv": TEXT},
                "facts": ["source_csv"],
            },
        },
    )
    (recommended,) = offer["recommendations"]
    accepted = studio_request(
        root,
        "decide_pattern",
        {
            "project_id": pid,
            "expected_revision": offer["revision"],
            "request_id": "choose",
            "recommendation_id": recommended["recommendation_id"],
            "decision": "accepted",
            "user_text": "採用已顯示的 CSV 表格讀取工具",
        },
    )
    graph = {
        "blueprint_schema_version": 2,
        "workflow": {"id": workflow_id, "version": "1.0.0", "revision": 1, "goal": "整理本機 CSV"},
        "inputs": {"csv": TEXT, "initial_rows": ROWS},
        "provenance": {},
        "nodes": [
            {
                "id": "deliver",
                "kind": "task",
                "capability": "json.identity",
                "capability_version": "1",
                "permissions": [],
                "side_effect": "none",
                "inputs": {"source": {"source": "input://initial_rows", "port": ROWS}},
                "outputs": {"rows": ROWS},
            }
        ],
        "outputs": {"rows": "artifact://deliver/rows"},
        "quality_contract": {
            "success_criteria": ["保留 CSV 所有欄位和值"],
            "golden_cases": [
                {
                    "inputs": {"csv": CASES[0][1], "initial_rows": CASES[0][2]},
                    "expected": {"rows": CASES[0][2]},
                }
            ],
            "adapter_cases": {
                "requirement_1": [
                    {"id": name, "inputs": {"source": text}, "expected": {"rows": rows}}
                    for name, text, rows in CASES
                ]
            },
        },
    }
    draft = studio_request(
        root,
        "prepare_blueprint",
        {
            "project_id": pid,
            "expected_revision": accepted["revision"],
            "request_id": "draft",
            "graph": graph,
            "design": {"rules": "保留原始表格欄位與值"},
        },
    )
    composed = studio_request(
        root,
        "compose_pattern",
        {
            "project_id": pid,
            "expected_revision": draft["revision"],
            "request_id": "compose",
            "draft_id": draft["proposal_id"],
            "pattern_ref": pattern_ref,
            "decision_id": recommended["recommendation_id"],
            "insert_location": {"namespace": "csv"},
            "port_map": {
                "inputs": {"in_1": "input://csv"},
                "outputs": {"rows": ["node://deliver/source"]},
            },
            "parameter_bindings": {},
        },
    )
    final = studio_request(root, "finalize_workflow_design", {
        "project_id": pid, "expected_revision": composed["revision"], "request_id": "finalize",
        "draft_id": composed["proposal_id"],
        "review": workflow_design_review(composed["proposal"], workspace=root),
        "draft_reply": "初稿可進入定稿",
        "shown_sections": {key: item["sha256"] for key, item in composed["proposal"]["review_sections"].items()},
    })
    exported = studio_request(
        root,
        "export_blueprint",
        {
            "project_id": pid,
            "expected_revision": final["revision"],
            "request_id": "export",
            "proposal_id": final["proposal_id"],
            "review_hash": final["review_hash"],
            "shown_sections": {key: item["sha256"] for key, item in final["review_sections"].items()},
            "decision": "approve",
            "user_text": "確認匯出",
        },
    )
    imported = service.install_workflow(archive=Path(exported["path"]))["draft_id"]
    preflight = service.resolve_blueprint_capabilities(imported)
    assert preflight["status"] == "blocked"
    (portable,) = preflight["portable_decisions"]
    assert portable["status"] == "needs_qualification"
    (candidate,) = portable["candidates"]
    selected = {portable["node"]: candidate["adapter"]}
    proposal = service.preview_binding_qualification(imported, selected)
    qualified = service.qualify_blueprint_bindings(
        imported,
        selected,
        BindingReview(
            proposal["request_hash"], "simulated local confirmation for actual CSV checks"
        ),
    )
    assert qualified["status"] == "qualified", qualified
    built = _build_install(service, imported)
    # Use a file as the local source. Supply its text through the declared work input.
    local_file = root / f"{workflow_id}.csv"
    local_file.write_text(CASES[0][1], encoding="utf-8")
    request = root / f"{workflow_id}.json"
    request.write_text(
        json.dumps({"inputs": {"csv": local_file.read_text(encoding="utf-8"), "initial_rows": []}})
    )
    run = service.run_workflow_sync({"task": workflow_id, "source": request})
    assert run["blueprint"]["outputs"] == {"rows": CASES[0][2]}
    session_id = run["blueprint"]["session_id"]
    preview = service.workflow_quality(session_id)
    # A scripted user reports a desired correction to exercise the historical feedback path.
    feedback = service.workflow_quality(
        session_id,
        "feedback",
        {
            "expected_revision": preview["revision"],
            "request_id": "reported",
            "preview_id": preview["preview_id"],
            "decision": "correct",
            "reason": "測試受影響流程的回饋與上游改版追蹤",
            "user_text": "請一併檢查另一個流程",
            "outputs": {"rows": [{"id": "A", "amount": "0.100"}]},
            "tags": ["pattern_defect", "wrong_total"],
        },
    )
    return {
        "run_id": run["run_id"],
        "blueprint_hash": imported,
        "session_id": session_id,
        "lock_hash": qualified["lock"]["lock_hash"],
        "candidate_hash": built["archive_sha256"],
        "feedback_id": feedback["feedback_id"],
    }


def test_portable_pattern_from_mcp_to_qualified_local_csv_and_upgrade_draft(
    tmp_path, monkeypatch, workflow_design_review,
):
    if not any(entry.name == "routinekeel.csv_read" for entry in entry_points(group=ADAPTER_GROUP)):
        if os.environ.get("ROUTINEKEEL_E2E_ENFORCE") == "1":
            pytest.fail(
                "Install the candidate's optional Data Adapter wheel in the stage-26 environment"
            )
        pytest.skip("Cross-host acceptance requires the optional Data Adapter wheel")
    pytest.importorskip("workflow_studio", reason="Stage 26 tests require Studio")
    observed = _mcp_result(tmp_path)
    assert observed == {"rows": CASES[0][2]}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_catalog = tmp_path / "host-observation.json"
    _observe(host_catalog, {"fixture.read_rows": _tool("fixture.read_rows")})
    monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG", str(host_catalog))
    runtime = WorkflowService(workspace)
    source, installed, source_run = _source(runtime, observed)
    project = studio_request(workspace, "create", {"name": "抽取來源", "task": "重用資料讀取能力"})
    extracted = studio_request(
        workspace,
        "extract_pattern",
        {
            "project_id": project["project_id"],
            "expected_revision": project["revision"],
            "workflow_ref": "sheet_source@1.0.0",
            "selected_nodes": ["read_sheet"],
            "purpose": "可攜資料讀取",
            "pattern_id": "data.portable_csv",
            "request_id": "extract",
            "capability_map": {"read_sheet": {"capability": "tabular.read", "version": "1.0.0"}},
        },
    )
    assert extracted["report"]["source_blueprint_hash"] == source
    assert extracted["report"]["dependencies"][0]["source_locus"] == "host_mediated"
    pattern_ref = "data.portable_csv@1.0.0"
    pattern_pack = _pack_candidate(extracted, tmp_path / "extracted.zip")
    pack, trial = _admit_pack(workspace, pattern_pack, pattern_ref, activate=True)

    local = _tool("routinekeel.csv_read")
    local["receipt_id"] = "locally-installed-adapter-observation"
    local["portable"] = {
        "id": "routinekeel.csv_read",
        "version": "1.0.0",
        "capability": "tabular.read",
        "capability_version": "1.0.0",
        "provider_version": "1.0.0",
        "locus": "local_runtime",
        "tool": "routinekeel.csv_read",
        "distribution": "routinekeel-data-adapter",
        "inputs": {"source": TEXT},
        "outputs": {"rows": ROWS},
        "permissions": [],
        "side_effect": "none",
        "network": False,
        "platforms": [sys.platform],
        "max_cost_micro_usd": 0,
    }
    _observe(host_catalog, {"routinekeel.csv_read": local})
    first = _target(runtime, "local_csv_a", pattern_ref, workflow_design_review)
    second = _target(runtime, "local_csv_b", pattern_ref, workflow_design_review)
    target_ref = "data.portable_csv@1.1.0"
    candidate_path = _pack_candidate(extracted, tmp_path / "revision.zip", version="1.1.0")
    _admit_pack(workspace, candidate_path, target_ref, activate=False)
    improvement = studio_request(
        workspace,
        "propose_pattern_improvement",
        {
            "source_ref": pattern_ref,
            "target_ref": target_ref,
            "request_id": "portability-improvement",
            "feedback_ids": [first["feedback_id"], second["feedback_id"]],
        },
    )
    assert improvement["status"] == "proposed"
    assert len(improvement["dependents"]) == 2
    result = {
        "source_run_id": source_run,
        "source_blueprint_hash": source,
        "source_candidate_hash": installed["archive_sha256"],
        "pattern_pack_hash": pack["manifest_hash"],
        "pattern_hash": trial["pattern_hash"],
        "pattern_trial_hash": trial["qualification_hash"],
        "active_runs": [first, second],
        "improvement_proposal_id": improvement["proposal_id"],
        "improvement_evidence_hash": content_hash(improvement["attribution"]),
        "source_host": "local MCP stdio fixture",
        "target_host": "local CSV only",
        "acceptance_decisions": "simulated",
        "paid_calls": 0,
    }
    if destination := os.environ.get("ROUTINEKEEL_E2E_EVIDENCE"):
        Path(destination).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
