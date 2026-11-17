import copy
import json
import sqlite3

import pytest
from compiler_helpers import build_ir, runtime_python
from generated_cases import csv_parameters
from ir_cases import valid_ir
from test_generated_handlers import program_ir
from workflow_studio.contracts import StudioError
from workflow_studio.llm.provider import Chunk
from workflow_studio.llm.usage import Budget
from workflow_studio.registry.handlers import MARKER
from workflow_studio.service import StudioService
from workflow_studio.storage.migrations import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION
from workflow_studio.storage.sqlite import SQLiteRepository
from workflow_studio.ui.model import StudioUIModel


def source_handler(
    tmp_path,
    parameters=None,
    *,
    text="id,amount,status\nA,100,open\nB,50,open\n",
    expected="id,amount\nA,100\n",
    format="csv",
):
    studio, p, revision, policy = build_ir(
        tmp_path, program_ir(parameters or csv_parameters(), format)
    )
    studio.close()
    model = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
    model.select_project(p.project_id)
    source, result = tmp_path / "input.txt", tmp_path / "expected.txt"
    source.write_bytes(text.encode())
    result.write_bytes(expected.encode())
    model.add_case(source, result, "result")
    report = model.verify()
    assert report["status"] == "passed", report
    item = model.save_handler_tool(
        model.project().version,
        model.build_id,
        report["package_hash"],
        "copy",
        tool_id="saved.filter",
        version="1.0.0",
        title="可重用處理",
        actor="fixture",
        reason="share verified deterministic handler",
    )
    return model, p, revision, report, item


def test_verified_handler_reused_across_projects_after_source_archive(tmp_path):
    model, origin, _, report, item = source_handler(tmp_path)
    assert item["trust_status"] == "untrusted"
    assert "saved.filter@1.0.0" not in model.editor_tools()
    approved = model.decide_tool(item, "approve", "reviewer", "reviewed fixed source and tests")
    target = model.create_project("另一份訂單", "篩選至少 300 的未結訂單", "符合本專案固定案例")
    source = model.import_text("新的工作規則與新資料，不使用舊專案來源。")
    ir = model.initialize_flow(
        model.project().version,
        source.source_id,
        source.version,
        source.manifest["files"][0]["path"],
        "saved.filter@1.0.0",
        output_path="result.csv",
        parameters={"arguments": {"minimum": 300}},
    )
    params = ir.ir["nodes"][0]["parameters"]
    assert params["program"] == csv_parameters()["program"]
    assert params["arguments"] == {"minimum": 300}
    assert {e["source_id"] for e in ir.ir["evidence"]} == {source.source_id}
    with model.service() as studio:
        original = studio.projects.get(origin.project_id)
        studio.projects.archive(origin.project_id, original.version)
    incoming, expected = tmp_path / "new.csv", tmp_path / "new-expected.csv"
    incoming.write_bytes(b"id,amount,status\nX,250,open\nY,450,open\nZ,900,closed\n")
    expected.write_bytes(b"id,amount\nY,450\n")
    model.add_case(incoming, expected, "result")
    checked = model.verify()
    assert checked["status"] == "passed", checked
    assert checked["runtime_model_authorized"] is False
    with model.service() as studio:
        library = studio.handlers.get("saved.filter", "1.0.0")
        assert library["origin"]["project_id"] == origin.project_id
        assert library["origin"]["report_id"] == report["report_id"]
        assert studio.projects.get(target.project_id).current_revision_id == ir.revision_id
    model.decide_tool(approved, "revoke", "reviewer", "stop library version")
    with pytest.raises(StudioError):
        model.compile()


@pytest.mark.parametrize("field", ["program", "tests", "argument_types", "purpose"])
def test_reuse_cannot_replace_fixed_program_or_tests(tmp_path, field):
    model, _, _, _, item = source_handler(tmp_path)
    model.decide_tool(item, "approve", "reviewer", "fixture")
    model.create_project("重用", "使用固定工具", "符合新樣本")
    source = model.import_text("來源")
    ir = model.initialize_flow(
        model.project().version,
        source.source_id,
        source.version,
        source.manifest["files"][0]["path"],
        "saved.filter@1.0.0",
        output_path="out.csv",
    )
    node = copy.deepcopy(ir.ir["nodes"][0])
    node["parameters"][field] = {
        "program": "def transform(text, params):\n    return text",
        "tests": [],
        "argument_types": {},
        "purpose": "different",
    }[field]
    before = model.project()
    with pytest.raises(StudioError, match="不可改寫"):
        model.edit_graph(before.version, upsert_nodes=[node])
    assert model.project() == before


def test_template_clone_keeps_body_and_requires_new_approval(tmp_path):
    model, _, _, _, item = source_handler(tmp_path)
    model.decide_tool(item, "approve", "reviewer", "first version")
    descriptor = item["tool"].model_dump()
    descriptor.update(version="1.1.0", title="新版名稱")
    cloned = model.register_tool(
        descriptor,
        template_source=("saved.filter", "1.0.0"),
        actor="reviewer",
        reason="new description same code",
    )
    assert cloned["trust_status"] == "untrusted"
    one, two = (
        model.handler_template("saved.filter", "1.0.0"),
        model.handler_template("saved.filter", "1.1.0"),
    )
    assert one["template_hash"] == two["template_hash"]
    assert one["parameters"] == two["parameters"]
    with model.service() as studio:
        with pytest.raises(sqlite3.IntegrityError):
            studio.repository.connection.execute(
                "UPDATE studio_handler_templates SET parameters_json='{}'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            studio.repository.connection.execute("DELETE FROM studio_handler_templates")


def test_unbound_import_marker_cannot_claim_fixed_handler(tmp_path):
    model = StudioUIModel(tmp_path)
    with model.service() as studio:
        descriptor = studio.tools.get("data.transform_csv", "1.0.0")["tool"].model_dump()
    descriptor.update(tool_id="fake.template", test_evidence=[MARKER + "0" * 64])
    item = model.register_tool(descriptor)
    with pytest.raises(StudioError):
        model.decide_tool(item, "approve", "reviewer", "missing body must fail")
    record = next(r for r in model.tool_records() if r["tool_id"] == "fake.template")
    assert not record["compiler_supported"] and not record["available"]


def test_unverified_and_stale_build_cannot_enter_library(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path, program_ir(csv_parameters(), "csv"))
    build = studio.compile(p.project_id, revision.revision_id, policy)
    with pytest.raises(StudioError) as exc:
        studio.handlers.from_build(
            p.project_id,
            3,
            build["build_id"],
            build["package_hash"],
            "copy",
            policy,
            tool_id="saved.invalid",
            version="1.0.0",
            title="not verified",
            actor="fixture",
            reason="must fail",
        )
    assert exc.value.code == "TEST_FAILED"
    assert (
        studio.repository.connection.execute(
            "SELECT count(*) FROM studio_handler_templates"
        ).fetchone()[0]
        == 0
    )
    studio.close()


def test_discovery_uses_interface_without_private_code_examples_or_defaults(tmp_path):
    params = {
        "purpose": "加上文字前綴",
        "program": 'def transform(text, params):\n    # LIBRARY_PRIVATE_BODY\n    return params["prefix"] + text',
        "arguments": {"prefix": "LIBRARY_PRIVATE_DEFAULT"},
        "argument_types": {"prefix": "string"},
        "tests": [
            {
                "input": "PRIVATE_SYNTHETIC_A",
                "expected": "A_PRIVATE_SYNTHETIC_A",
                "arguments": {"prefix": "A_"},
            },
            {
                "input": "PRIVATE_SYNTHETIC_B",
                "expected": "B_PRIVATE_SYNTHETIC_B",
                "arguments": {"prefix": "B_"},
            },
        ],
    }
    model, _, _, _, item = source_handler(
        tmp_path, params, text="source", expected="LIBRARY_PRIVATE_DEFAULTsource", format="txt"
    )
    model.decide_tool(item, "approve", "reviewer", "interface only shared with model")
    target = model.create_project("前綴", "加上 correct_ 前綴", "固定範例符合")
    source = model.import_text("把 correct_ 前綴加到輸入文字。")

    class Provider:
        model, destination, external = "fixture", "fixture://local", False

        def __init__(self):
            self.requests = []

        def stream(self, messages, max_output_tokens):
            self.requests.append(messages)
            context = json.loads(messages[1]["content"])
            ir = valid_ir()
            ir.update(
                goal=context["goal"],
                workflow_id=context["workflow_id"],
                revision=context["next_revision"],
            )
            evidence = context["sources"][0]
            ir["evidence"][0].update(
                source_id=evidence["source_id"],
                source_version=evidence["source_version"],
                evidence_locator=evidence["path"],
            )
            ir["nodes"][0].update(
                tool="saved.filter", parameters={"arguments": {"prefix": "correct_"}}
            )
            ir["nodes"][0]["outputs"]["result"]["format"] = "txt"
            yield Chunk(text=json.dumps({"analysis": "重用固定介面", "ir": ir, "questions": []}))
            yield Chunk(done=True)

    provider = Provider()
    with model.service() as studio:
        planner = studio.discovery(
            provider, Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000), model.policy
        )
        prepared = planner.prepare(target.project_id, [source.source_id])
        for secret in ("LIBRARY_PRIVATE_BODY", "LIBRARY_PRIVATE_DEFAULT", "PRIVATE_SYNTHETIC_A"):
            assert secret not in prepared.messages_json
        events = list(planner.run(prepared))
        revision = studio.projects.get_revision(target.project_id, events[-1]["revision_id"])
        assert revision.ir["nodes"][0]["parameters"]["program"] == params["program"]
        assert revision.ir["nodes"][0]["parameters"]["arguments"] == {"prefix": "correct_"}
        public = studio.handlers.public_ir(revision.ir)
        assert public["nodes"][0]["parameters"] == {"arguments": {"prefix": "correct_"}}
        assert "LIBRARY_PRIVATE_BODY" not in json.dumps(public)


def test_v011_upgrade_and_backup_restore(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    with sqlite3.connect(root / "studio.sqlite3") as db:
        for version in range(1, 12):
            for statement in MIGRATIONS[version]:
                db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=11")
        db.execute(
            "INSERT INTO studio_projects VALUES ('old','kept','{}','active',1,NULL,'before','before')"
        )
    repo = SQLiteRepository(root)
    assert repo.schema_version == SCHEMA_VERSION
    snapshot = next((root / "backups").glob("before-v011-*.sqlite3"))
    repo.close()
    restored = SQLiteRepository.restore(snapshot, tmp_path / "restored")
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert db.execute("SELECT name FROM studio_projects").fetchone()[0] == "kept"
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='studio_handler_templates'"
        ).fetchone()


def test_repair_uses_template_arguments_without_transmitting_fixed_body(tmp_path):
    parameters = csv_parameters()
    parameters["program"] = parameters["program"].replace(
        "    rows =", "    # PRIVATE_LIBRARY_BODY\n    rows ="
    )
    parameters["tests"][0]["input"] = parameters["tests"][0]["input"].replace(
        "A,", "PRIVATE_SYNTHETIC_ID,"
    )
    parameters["tests"][0]["expected"] = parameters["tests"][0]["expected"].replace(
        "A,", "PRIVATE_SYNTHETIC_ID,"
    )
    model, _, _, _, item = source_handler(tmp_path, parameters)
    model.decide_tool(item, "approve", "reviewer", "reviewed template")
    model.create_project("新專案", "取至少 300 的未結帳項目", "符合獨立案例")
    source = model.import_text("金額門檻是 300。")
    model.initialize_flow(
        model.project().version,
        source.source_id,
        source.version,
        source.manifest["files"][0]["path"],
        "saved.filter@1.0.0",
        output_path="result.csv",
        parameters={"arguments": {"minimum": 50}},
    )
    incoming, expected = tmp_path / "repair.csv", tmp_path / "repair-expected.csv"
    incoming.write_bytes(b"id,amount,status\nX,100,open\nY,400,open\n")
    expected.write_bytes(b"id,amount\nY,400\n")
    model.add_case(incoming, expected)
    failed = model.verify()
    assert failed["status"] == "failed"

    class Provider:
        model, destination, external = "fixture", "fixture://local", False

        def __init__(self):
            self.requests = []

        def stream(self, messages, max_output_tokens):
            self.requests.append(messages)
            context = json.loads(messages[1]["content"])
            proposal = context["current_ir"]
            assert proposal["nodes"][0]["parameters"] == {"arguments": {"minimum": 50}}
            proposal["nodes"][0]["parameters"]["arguments"]["minimum"] = 300
            yield Chunk(
                text=json.dumps({"analysis": "調整固定工具的參數", "ir": proposal, "questions": []})
            )
            yield Chunk(done=True)

    model.provider = Provider()
    model.budget = Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000)
    prepared = model.prepare_repair(failed["report_id"])
    result = model.repair_model(
        prepared, lambda e: None, lambda: False, authorized_hash=prepared.request_hash
    )
    assert result["status"] == "passed", result
    transmitted = json.dumps(model.provider.requests)
    assert "PRIVATE_LIBRARY_BODY" not in transmitted
    assert "PRIVATE_SYNTHETIC_ID" not in transmitted
    assert model.revision().ir["nodes"][0]["parameters"]["program"] == parameters["program"]


def test_old_verified_build_cannot_export_a_newly_edited_step(tmp_path):
    model, _, _, report, item = source_handler(tmp_path)
    old_build, old_hash = report["build_id"], report["package_hash"]
    model.edit_node("copy", arguments={"minimum": 200})
    with pytest.raises(StudioError) as exc:
        model.save_handler_tool(
            model.project().version,
            old_build,
            old_hash,
            "copy",
            tool_id="saved.stale",
            version="1.0.0",
            title="stale",
            actor="fixture",
            reason="must reverify",
        )
    assert exc.value.code == "REVISION_CONFLICT"


def test_expansion_limit_preserves_target_project_and_library(tmp_path, monkeypatch):
    from workflow_studio.registry import handlers

    model, _, _, _, item = source_handler(tmp_path)
    model.decide_tool(item, "approve", "reviewer", "fixture")
    model.create_project("展開上限", "使用固定處理", "符合案例")
    source = model.import_text("來源")
    before = model.project()
    snapshot = model.handler_template("saved.filter", "1.0.0")
    with monkeypatch.context() as guard:
        guard.setattr(handlers, "MAX_EXPANDED_IR_BYTES", 1000)
        with pytest.raises(StudioError) as exc:
            model.initialize_flow(
                before.version,
                source.source_id,
                source.version,
                source.manifest["files"][0]["path"],
                "saved.filter@1.0.0",
                output_path="result.csv",
            )
        assert exc.value.code == "INPUT_INVALID"
    assert model.project() == before
    assert model.revision() is None
    assert model.handler_template("saved.filter", "1.0.0") == snapshot


def test_template_size_limit_does_not_leave_partial_registration(tmp_path, monkeypatch):
    from workflow_studio.registry import handlers

    monkeypatch.setattr(handlers, "MAX_TEMPLATE_BYTES", 1)
    with pytest.raises(StudioError) as exc:
        source_handler(tmp_path)
    assert exc.value.code == "INPUT_INVALID"
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        assert (
            studio.repository.connection.execute(
                "SELECT COUNT(*) FROM studio_handler_templates"
            ).fetchone()[0]
            == 0
        )
        assert (
            studio.repository.connection.execute(
                "SELECT COUNT(*) FROM studio_tools WHERE tool_id='saved.filter'"
            ).fetchone()[0]
            == 0
        )
