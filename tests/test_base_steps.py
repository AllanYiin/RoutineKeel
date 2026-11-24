"""Exercise common steps through Studio, Graph Runtime and the installed delivery path."""

import json
import shutil
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
from flow_base.blueprints.adapter import BlueprintAdapter
from flow_base.blueprints.bundled_skills import load_bundled_skills
from flow_base.blueprints.sessions import Assets
from flow_base.core.run_context import ACTIVE_RUN, RunContext
from flow_base.graph import documents
from flow_base.graph.base_steps import execute_reconciliation
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import FlowError, Runtime, atomic_json, digest, read_json
from flow_base.storage.delivery import prepare_staging
from flow_base.studio import studio_request
from routinekeel_contracts.base_steps import (
    CRITIC,
    DESIGN_KEY,
    DOCUMENT_SEARCH,
    JUDGE,
    RECONCILE_PLAN,
    RECONCILE_RUN,
    REVISER,
    SKILL_LOAD,
    VERSION,
    ReconciliationPlan,
    task_node,
)
from routinekeel_contracts.documa import call_documa
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.graph_validator import validate_blueprint
from test_graph_builder import data_graph
from test_graph_scheduler import setup
from test_studio_design_contract import approve, finish, install, sketch


def test_named_file_delivery_and_withheld_preview_keep_previous_version_separate(
    tmp_path,
):
    runtime = Runtime(tmp_path / "share_states")
    assets = Assets(runtime)
    source = tmp_path / "evidence.txt"
    source.write_text("previous result", encoding="utf-8")
    reference = assets.store(source)
    token = ACTIVE_RUN.set(RunContext(runtime, "run_first", tmp_path, None))
    adapter = BlueprintAdapter.__new__(BlueprintAdapter)
    adapter.plan_data = {"format": 2, "graph": {"quality_contract": {DESIGN_KEY: 1}}}
    adapter.assets = assets
    terminal = tmp_path / "terminal.json"
    output = tmp_path / "delivery"
    try:
        atomic_json(
            terminal,
            {
                "status": "complete",
                "result_status": "accepted",
                "outputs": {"evidence": reference},
            },
        )
        first = adapter.publish_outputs(terminal, output, None)
    finally:
        ACTIVE_RUN.reset(token)
    artifact = "artifacts/" + reference.removeprefix("asset:")
    assert read_json(output / first["result_delivery"]["link_index"])["outputs"][
        "evidence"
    ]["file_paths"] == [artifact]
    with ZipFile(output / first["result_delivery"]["archive"]) as archive:
        assert archive.read(artifact) == source.read_bytes()

    stage = prepare_staging(output, "run_next")
    atomic_json(terminal, {"status": "waiting"})
    token = ACTIVE_RUN.set(RunContext(runtime, "run_next", tmp_path, None))
    try:
        next_report = adapter.publish_outputs(terminal, stage, None)
    finally:
        ACTIVE_RUN.reset(token)
    assert next_report["graph_delivery_status"] == "withheld"
    assert not list((stage / "artifacts").iterdir())
    assert (output / artifact).read_bytes() == source.read_bytes()
    assert (output / first["result_delivery"]["archive"]).is_file()


def graph_with(inputs, nodes, outputs, sample_input, expected):
    graph = data_graph()
    graph.update(
        inputs={key: {"type": kind} for key, kind in inputs.items()},
        nodes=nodes,
        outputs=outputs,
    )
    graph["quality_contract"] = {
        "success_criteria": ["交付具名結果"],
        "golden_cases": [{"inputs": sample_input, "expected": expected}],
    }
    return graph


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [{"id": 1, "value": "0.25"}, {"id": 2, "value": "0.25"}],
            ["matched", "matched", "matched"],
        ),
        ([{"id": 1, "value": "0.25"}], ["mismatched", "mismatched", "mismatched"]),
        (
            [{"id": 1, "value": "0.25"}, {"id": 1, "value": "0.25"}],
            ["matched", "matched", "invalid_input"],
        ),
        (
            [{"id": 1, "value": "0.25"}, {"id": 2}],
            ["matched", "invalid_input", "invalid_input"],
        ),
    ],
)
def test_reconciliation_checks_exact_tolerance_count_and_invalid_rows(rows, expected):
    spec = ReconciliationPlan.model_validate(
        {
            "rules": [
                {
                    "id": "count",
                    "operation": "count",
                    "left": "/rows",
                    "right": "/rows",
                },
                {
                    "id": "sum",
                    "operation": "sum",
                    "left": "/rows",
                    "left_field": "/value",
                    "right": "/totals",
                    "tolerance": "0.01",
                },
                {
                    "id": "keyed",
                    "operation": "keyed",
                    "left": "/rows",
                    "left_key": "/id",
                    "left_field": "/value",
                    "right": "/rows",
                    "right_key": "/id",
                    "right_field": "/value",
                },
            ]
        }
    ).model_dump(mode="json")
    plan = {"version": VERSION, "spec": spec, "sha256": digest([VERSION, spec])}
    right = {
        "rows": [{"id": 1, "value": "0.25"}, {"id": 2, "value": "0.25"}],
        "totals": ["0.51"],
    }
    result = execute_reconciliation(plan, {"rows": rows}, right)
    assert [check["status"] for check in result["report"]["checks"]] == expected
    assert result["matched"] == all(status == "matched" for status in expected)
    with pytest.raises(ValueError, match="版本或指紋不符"):
        execute_reconciliation({**plan, "sha256": "changed"}, {"rows": rows}, right)


def test_studio_reconciliation_plan_reuses_cache_and_delivers_current_result(
    tmp_path, workflow_design_review
):
    nodes = [
        task_node(
            RECONCILE_PLAN,
            "plan",
            {},
            parameters={
                "rules": [
                    {
                        "id": "total",
                        "operation": "sum",
                        "left": "/lines",
                        "left_field": "/value",
                        "right": "/totals",
                        "tolerance": "0.01",
                    },
                    {"id": "code", "left": "/code", "right": "/code"},
                    {
                        "id": "detail",
                        "operation": "keyed",
                        "left": "/lines",
                        "left_key": "/id",
                        "left_field": "/value",
                        "right": "/detail",
                        "right_key": "/key",
                        "right_field": "/amount",
                    },
                ]
            },
        ),
        task_node(
            RECONCILE_RUN,
            "compare",
            {
                "plan": "artifact://plan/plan",
                "left": "input://left",
                "right": "input://right",
            },
        ),
    ]
    inputs = {
        "left": {
            "lines": [{"id": 1, "value": "0.1"}, {"id": 2, "value": "0.2"}],
            "code": "A",
        },
        "right": {
            "totals": ["0.30"],
            "code": "A",
            "detail": [
                {"key": 2, "amount": "0.2"},
                {"key": 1, "amount": "0.1"},
            ],
        },
    }
    graph = graph_with(
        {"left": "object", "right": "object"},
        nodes,
        {"matched": "artifact://compare/matched"},
        inputs,
        {"matched": True},
    )
    draft = sketch(tmp_path, graph)
    assert RECONCILE_PLAN in draft["draft_design"]["base_step_catalog"]["tasks"]
    final = finish(tmp_path, draft, workflow_design_review(draft))
    assert (
        final["design_review"]["base_steps"]["tasks"]["plan"]["capability"]
        == RECONCILE_PLAN
    )
    assert "底座 · 執行勾稽" in final["design_review"]["final_diagram"]
    service = install(tmp_path, approve(tmp_path, final))

    request = tmp_path / "job.json"
    request.write_text(json.dumps({"inputs": inputs}))
    first = service.run_workflow_sync({"task": "graph_copy", "source": request})
    assert first["blueprint"]["outputs"] == {"matched": True}
    output = Path(first["output"])
    delivery = first["result_delivery"]
    assert (
        read_json(output / delivery["link_index"])["outputs"]["matched"]["key"]
        == "matched"
    )
    assert "[具名輸出的 JSON](results.json)" in (output / delivery["links"]).read_text(
        encoding="utf-8"
    )
    with ZipFile(output / delivery["archive"]) as bundle:
        assert set(bundle.namelist()) == {
            "artifacts/results.json",
            "artifacts/result-links.json",
            "artifacts/result-links.md",
        }
        assert json.loads(bundle.read("artifacts/results.json")) == {"matched": True}
    assert service.list_run_artifacts(first["run_id"])

    inputs["right"] = {
        "totals": ["9"],
        "code": "B",
        "detail": [{"key": 3, "amount": "0.2"}],
    }
    request = tmp_path / "next-job.json"
    request.write_text(json.dumps({"inputs": inputs}))
    second = service.run_workflow_sync({"task": "graph_copy", "source": request})[
        "blueprint"
    ]
    assert second["outputs"] == {"matched": False}
    checks = second["nodes"]["compare"]["outputs"]["report"]["checks"]
    assert [row["status"] for row in checks] == [
        "mismatched",
        "mismatched",
        "mismatched",
    ]
    assert checks[-1]["left_only"]["examples"] == [1, 2]
    history = read_json(
        GraphSession(service.workspace.runtime, second["session_id"]).path
    )["history"]
    assert history[0]["evidence"]["kind"] == "local_cache"
    nodes[0]["parameters"]["rules"][0]["tolerance"] = "0.02"
    runtime, plan, bindings = setup(tmp_path, graph)
    revised = GraphSession.begin(runtime, plan, {"inputs": inputs}, tmp_path, bindings)
    revised_history = read_json(GraphSession(runtime, revised["session_id"]).path)[
        "history"
    ]
    assert revised_history[0]["evidence"]["kind"] == "local_execution"
    assert (
        revised_history[0]["evidence"]["plan_sha256"]
        != history[0]["evidence"]["plan_sha256"]
    )


def _pdf(path):
    stream = b"BT /F1 12 Tf 40 80 Td (reconciliation fixture) Tj ET"
    entries = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 150] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for number, item in enumerate(entries, 1):
        offsets.append(len(data))
        data += b"%d 0 obj\n" % number + item + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        b"%010d 00000 n \n" % v for v in offsets
    )
    data += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(data)


def _docx(path, text="reconciliation fixture"):
    with ZipFile(path, "w") as doc:
        doc.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        doc.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        doc.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>",
        )


@pytest.fixture
def documa_executable():
    executable = shutil.which("documa")
    if not executable:
        pytest.skip("Documa CLI is not installed")
    return Path(executable)


def test_documa_indexes_pdf_and_docx_and_searches_in_runtime_and_studio(
    tmp_path, documa_executable, monkeypatch
):
    real_build = documents._build
    parsed = []

    def observe_build(executable, assets, source, version):
        parsed.append(source.suffix)
        return real_build(executable, assets, source, version)

    monkeypatch.setattr(documents, "_build", observe_build)
    _pdf(tmp_path / "example.pdf")
    _docx(tmp_path / "example.docx")
    inputs = {
        "pdf": {"$file": "example.pdf"},
        "office": {"$file": "example.docx"},
        "query": "reconciliation",
    }
    graph = graph_with(
        {
            "pdf": "file",
            "office": "file",
            "query": "string",
            "document_indexes": "object",
        },
        [
            task_node(
                DOCUMENT_SEARCH,
                "search",
                {"indexes": "input://document_indexes", "query": "input://query"},
                parameters={"route": "graph-first"},
            )
        ],
        {"results": "artifact://search/matches"},
        inputs,
        {"results": []},
    )
    graph["quality_contract"][DESIGN_KEY] = 1
    runtime, plan, bindings = setup(tmp_path, graph)
    view = GraphSession.begin(runtime, plan, {"inputs": inputs}, tmp_path, bindings)
    assert view["status"] == "complete", view
    assert len(view["document_indexes"]) == 2
    assert {Path(token[6:]).suffix for token in view["document_indexes"]} == {
        ".docx",
        ".pdf",
    }
    for result in view["outputs"]["results"]:
        assert result["route"] == "graph-first" and result["graph_used"]
        assert any(
            "reconciliation" in row.get("snippet", "") for row in result["candidates"]
        )
    assert (
        GraphSession.begin(runtime, plan, {"inputs": inputs}, tmp_path, bindings)[
            "document_indexes"
        ]
        == view["document_indexes"]
    )
    assert sorted(parsed) == [".docx", ".pdf"]
    with pytest.raises(FlowError, match="底座派生"):
        GraphSession.begin(
            runtime,
            plan,
            {"inputs": {**inputs, "document_indexes": {}}},
            tmp_path,
            bindings,
        )

    project = studio_request(
        tmp_path, "create", {"name": "文件索引", "task": "查詢文件"}
    )
    attached = studio_request(
        tmp_path,
        "attach",
        {
            "project_id": project["project_id"],
            "expected_revision": project["revision"],
            "path": str(tmp_path / "example.docx"),
        },
    )
    assert attached["preview"]["files"][0]["documa"]["relation_count"] > 0
    found = studio_request(
        tmp_path,
        "search_document",
        {
            "project_id": project["project_id"],
            "source_id": attached["source_id"],
            "version": attached["version"],
            "relative": "example.docx",
            "query": "reconciliation",
        },
    )
    assert found["candidates"] and found["candidates"][0]["blockId"]

    _docx(tmp_path / "example.docx", "reconciliation updated figures")
    revised = GraphSession.begin(runtime, plan, {"inputs": inputs}, tmp_path, bindings)
    assert sorted(parsed) == [".docx", ".docx", ".pdf"]
    assert set(revised["document_indexes"]) != set(view["document_indexes"])
    assert any(
        "updated figures" in row.get("snippet", "")
        for match in revised["outputs"]["results"]
        for row in match["candidates"]
    )
    assert any("fixture" in row.get("snippet", "") for row in found["candidates"])


def test_documa_is_required_for_document_inputs_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTINEKEEL_DOCUMA", str(tmp_path / "missing-documa.exe"))
    _pdf(tmp_path / "example.pdf")
    graph = graph_with(
        {"document": "file", "query": "string", "document_indexes": "object"},
        [
            task_node(
                DOCUMENT_SEARCH,
                "search",
                {"indexes": "input://document_indexes", "query": "input://query"},
            )
        ],
        {"results": "artifact://search/matches"},
        {"query": "fixture"},
        {"results": []},
    )
    graph["inputs"]["document"]["required"] = False
    graph["quality_contract"][DESIGN_KEY] = VERSION
    runtime, plan, bindings = setup(tmp_path, graph)
    without_document = GraphSession.begin(
        runtime, plan, {"inputs": {"query": "fixture"}}, tmp_path, bindings
    )
    assert without_document["outputs"] == {"results": []}
    with pytest.raises(FlowError) as missing:
        GraphSession.begin(
            runtime,
            plan,
            {"inputs": {"query": "fixture", "document": {"$file": "example.pdf"}}},
            tmp_path,
            bindings,
        )
    assert missing.value.code == "DOCUMA_UNAVAILABLE"


def test_documa_graph_skill_loader_is_shared_by_studio_and_runtime(
    tmp_path, documa_executable, monkeypatch
):
    monkeypatch.delenv("ROUTINEKEEL_DOCUMA_SKILL_STORE", raising=False)
    task = "Run reconciliation for an invoice ledger"
    assert (
        studio_request(tmp_path, "load_skills", {"task": task})["code"]
        == "SKILL_CONFIG_MISSING"
    )
    catalog = tmp_path / "trusted-skills"
    skill = catalog / "invoice-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: invoice-check\ndescription: Reconcile an invoice ledger.\n---\n\n"
        "# Invoice check\n\n## Workflow\n\nCompare the totals before delivery.\n",
        encoding="utf-8",
    )
    (skill / "skill_lifecycle.yaml").write_text(
        "dependencies:\n  skills: [test:ledger-rules]\n"
    )
    dependency = catalog / "ledger-rules"
    dependency.mkdir()
    (dependency / "SKILL.md").write_text(
        "---\nname: ledger-rules\ndescription: Requirements for source-ledger checks.\n---\n\n"
        "# Ledger rules\n\nUse the declared tolerance.\n",
        encoding="utf-8",
    )
    store = tmp_path / "documa-store"
    call_documa(
        documa_executable,
        "skills",
        "root-add",
        "test",
        catalog,
        "--store-dir",
        store,
        directory=tmp_path,
    )
    call_documa(
        documa_executable, "skills", "sync", "--store-dir", store, directory=tmp_path
    )
    monkeypatch.setenv("ROUTINEKEEL_DOCUMA_SKILL_STORE", str(store))
    selected = studio_request(
        tmp_path, "load_skills", {"task": task, "skill_names": ["test:invoice-check"]}
    )
    assert (
        selected["status"] == "ok"
        and selected["graph_trace"]
        and selected["provenance"]
    ), selected
    graph = graph_with(
        {"task": "string"},
        [
            task_node(
                SKILL_LOAD,
                "load",
                {"task": "input://task"},
                parameters={"skill_names": ["test:invoice-check"]},
            )
        ],
        {"skill": "artifact://load/bundle"},
        {"task": task},
        {"skill": {"status": "ok"}},
    )
    runtime, plan, bindings = setup(tmp_path, graph)
    result = GraphSession.begin(
        runtime, plan, {"inputs": {"task": task}}, tmp_path, bindings
    )
    assert (
        result["outputs"]["skill"]["rendered_skill_md"] == selected["rendered_skill_md"]
    )


def test_bundled_v1_skills_use_documa_dependency_graph(tmp_path, documa_executable):
    plan = {"steps": [{"title": "Review an invoice", "skills": ["review"]}]}
    load_bundled_skills(
        tmp_path,
        plan,
        {"skills": {"skills": [{"id": "review", "uses": ["rules"]}, {"id": "rules"}]}},
        {
            "skills/review/SKILL.md": b"# Review\n\nReview the invoice.\n",
            "skills/rules/SKILL.md": b"# Policy\n\nVerify the invoice totals.\n",
        },
    )
    rendered = plan["steps"][0]["skill_text"]
    assert set(rendered) == {"review", "rules"}
    assert "Verify the invoice totals." in rendered["review"]


def _model_reply(session, view, outputs):
    task = view["pending"]
    claimed = session.interact(
        "claim", {"revision": view["revision"], "task_id": task["id"]}
    )
    assert claimed["dispatch"]["instructions"]
    partial = session.interact(
        "stream_delta",
        {
            "revision": claimed["revision"],
            "task_id": task["id"],
            "sequence": 0,
            "text": "Fixture model output",
        },
    )
    record_observation(
        session.runtime.workspace_root,
        task["idempotency_key"],
        task["scope_hash"],
        "model-fixture",
        "succeeded",
        outputs,
        usage={"cost_micro_usd": 0, "model_calls": 1},
        stream={
            "count": 1,
            "completed": True,
            "hash": partial["pending"]["stream_hash"],
        },
    )
    return session.interact(
        "reconcile", {"revision": partial["revision"], "task_id": task["id"]}
    )


@pytest.mark.parametrize(
    ("passed", "invalid_step", "bad_output", "failure_code"),
    [
        (False, None, None, None),
        (True, None, None, None),
        (True, "judge", ["missing named output"], "MODEL_OUTPUT_INVALID"),
        (True, "judge", {"verdict": {"passed": True}}, "MODEL_REVIEW_INVALID"),
        (False, "revise", {"candidate": ["wrong type"]}, "MODEL_REVISION_INVALID"),
    ],
    ids=[
        "revise-and-accept",
        "accept-without-revision",
        "invalid-ports",
        "invalid-review",
        "invalid-revision",
    ],
)
def test_model_critic_revise_and_judge_use_host_receipts(
    tmp_path, desktop_test_catalog, passed, invalid_step, bad_output, failure_code
):
    judging = {"candidate": "input://candidate", "rubric": "input://rubric"}
    revising = {
        "candidate": "input://candidate",
        "critique": "artifact://critic/critique",
    }
    nodes = [
        task_node(CRITIC, "critic", judging),
        task_node(REVISER, "revise", revising),
        task_node(
            JUDGE, "judge", {**judging, "candidate": "artifact://revise/candidate"}
        ),
    ]
    inputs = {"candidate": "Original text", "rubric": {"must_include": "Details"}}
    graph = graph_with(
        {"candidate": "json", "rubric": "object"},
        nodes,
        {"verdict": "artifact://judge/verdict"},
        inputs,
        {"verdict": {"passed": True}},
    )
    wrong = task_node(REVISER, "revise", {**revising, "critique": "input://rubric"})
    wrong["depends_on"] = ["critic"]
    bad = {**graph, "nodes": [nodes[0], wrong, nodes[2]]}
    with pytest.raises(StudioError, match="批評者輸出"):
        validate_blueprint(bad)
    catalog = read_json(desktop_test_catalog)
    record = catalog["tools"]["test-sink"]
    for node in nodes:
        capability = node["capability"]
        catalog["tools"][capability] = {
            **record,
            "tool": capability,
            "permissions": [],
            "side_effect": "none",
            "inputs": {key: binding["port"] for key, binding in node["inputs"].items()},
            "outputs": node["outputs"],
            "resources": {"model_calls": 1, "cost_micro_usd": 0},
        }
    desktop_test_catalog.write_text(json.dumps(catalog))
    runtime, plan, bindings = setup(tmp_path, graph)
    initial = GraphSession.begin(runtime, plan, {"inputs": inputs}, tmp_path, bindings)
    session = GraphSession(runtime, initial["session_id"])
    assert initial["pending"]["step"] == "critic"
    view = _model_reply(
        session,
        initial,
        {
            "critique": {
                "passed": passed,
                "rationale": "Review rubric",
                "findings": [] if passed else ["Add detail"],
            }
        },
    )
    if not passed:
        assert view["pending"]["step"] == "revise"
        if invalid_step == "revise":
            failed = _model_reply(session, view, bad_output)
            assert failed["nodes"]["judge"]["status"] == "pending"
            assert failed["status"] == "failed" and not failed["pending_tasks"]
            assert failed["nodes"]["revise"]["error"] == failure_code
            assert failed["nodes"]["revise"]["receipt"] == "model-fixture"
            return
        view = _model_reply(session, view, {"candidate": "Revised text with details"})
    assert view["pending"]["step"] == "judge"
    assert view["pending"]["inputs"]["candidate"] == (
        "Original text" if passed else "Revised text with details"
    )
    final = _model_reply(
        session,
        view,
        bad_output
        if invalid_step == "judge"
        else {"verdict": {"passed": True, "rationale": "Rubric met", "findings": []}},
    )
    if failure_code:
        assert final["status"] == "failed" and not final["pending_tasks"]
        assert final["nodes"]["judge"]["error"] == failure_code
        assert final["nodes"]["judge"]["receipt"] == "model-fixture"
    else:
        assert final["outputs"]["verdict"]["passed"] is True
