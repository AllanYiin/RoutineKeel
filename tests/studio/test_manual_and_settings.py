import copy
import os

import pytest
from compiler_helpers import build_ir, execute_package, runtime_python
from test_branches import branch_ir
from test_llm_runtime import llm_ir
from test_verifier import verification_case
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.parameters import numeric
from workflow_studio.conversation.session import StudioSession
from workflow_studio.llm.usage import UsageLedger
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.verifier.authorization import RuntimeAuthorization


def test_manual_first_flow_runs_without_model_or_code_editing(tmp_path):
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
    project = model.create_project("手動複製", "保留輸入檔案內容", "輸出位元組完全相同")
    source = model.import_text("需要完整保留輸入內容，不改寫檔案。")
    revision = model.initialize_flow(
        model.project().version,
        source.source_id,
        source.version,
        source.manifest["files"][0]["path"],
        "file.copy@1.0.0",
        output_path="artifacts/copy.txt",
    )
    assert revision.ir["revision"] == 1
    assert revision.ir["evidence"][0]["confidence_label"] == "inferred"
    with model.service() as studio:
        assert UsageLedger(studio.repository).summary(project.project_id)["calls"] == 0
    data = tmp_path / "actual.txt"
    data.write_bytes("中文實際資料\n".encode())
    model.add_case(data, data)
    assert model.verify()["status"] == "passed"
    with pytest.raises(StudioError, match="已有流程"):
        model.initialize_flow(
            model.project().version,
            source.source_id,
            source.version,
            source.manifest["files"][0]["path"],
            "file.copy@1.0.0",
            output_path="other.txt",
        )


@pytest.mark.parametrize("fault", ["format", "path", "source", "stale", "tool"])
def test_failed_manual_initialization_is_atomic(tmp_path, fault):
    model = StudioSession(tmp_path)
    model.create_project("初稿", "整理文字", "符合成功案例")
    source = model.import_text("來源")
    before = model.project()
    arguments = [
        before.version,
        source.source_id,
        source.version,
        source.manifest["files"][0]["path"],
        "file.copy@1.0.0",
    ]
    settings = {"output_path": "result.txt"}
    if fault == "format":
        arguments[-1] = "text.normalize@1.0.0"
    elif fault == "path":
        settings["output_path"] = "../outside.txt"
    elif fault == "source":
        arguments[1] = "other-project-source"
    elif fault == "stale":
        arguments[0] -= 1
    else:
        arguments[-1] = "shell.execute@1.0.0"
    with pytest.raises(StudioError):
        model.initialize_flow(*arguments, **settings)
    assert model.project() == before
    assert model.revision() is None


@pytest.mark.parametrize(
    "content,expected,selected",
    [(b"   long   ", b"long", "when_true"), (b" a ", b" a ", "when_false")],
)
def test_branch_settings_execute_both_paths_with_common_typed_output(
    tmp_path, content, expected, selected
):
    studio, p, original, policy = build_ir(tmp_path, branch_ir())
    studio.close()
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
    model.select_project(p.project_id)
    params = copy.deepcopy(original.ir["nodes"][0]["parameters"])
    for side in ("when_true", "when_false"):
        params[side]["parameters"]["final_newline"] = False
    changed = model.update_node_settings(
        model.project().version,
        "copy",
        parameters=params,
        output_path="branch-result.txt",
        timeout_seconds=90,
        cacheable=False,
    )
    assert changed.ir["nodes"][0]["outputs"]["result"]["format"] == "txt"
    with model.service() as studio:
        build = studio.compile(p.project_id, changed.revision_id, policy)
        execute_package(build["path"] / "package", tmp_path / "runtime", content)
        import json

        output = tmp_path / "runtime/output/artifacts/result"
        assert (output / "branch-result.txt").read_bytes() == expected
        assert json.loads((output / "branch.json").read_text())["selected"] == selected
        downstream = studio.editor(policy).template(
            p.project_id,
            "next",
            "text.normalize@1.0.0",
            {"source": "artifact://copy/result"},
            {"result": "final.txt"},
        )
        advanced = studio.editor(policy).apply(
            p.project_id,
            model.project().version,
            upsert_nodes=[downstream],
            outputs={"result": "artifact://next/result"},
        )
        assert studio.compile(p.project_id, advanced.revision_id, policy)


def test_incompatible_branch_and_missing_completion_contract_are_rejected(tmp_path):
    studio, p, original, policy = build_ir(tmp_path, branch_ir())
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    before = model.project()
    params = copy.deepcopy(original.ir["nodes"][0]["parameters"])
    params["when_false"] = {"tool": "file.report", "version": "1.0.0", "parameters": {}}
    with pytest.raises(StudioError):
        model.update_node_settings(before.version, "copy", parameters=params)
    with pytest.raises(StudioError):
        model.update_node_settings(before.version, "copy", postconditions=[])
    assert model.project() == before
    assert model.revision() == original


def test_llm_settings_invalidate_authorization_and_enforce_aggregate_budget(tmp_path, monkeypatch):
    policy = ToolPolicy(
        "windows" if os.name == "nt" else "linux",
        frozenset({"workspace.write", "model.call", "secret.read"}),
        True,
        True,
        100_000,
    )
    studio, p, build, cases, verifier, policy = verification_case(
        tmp_path, payload=llm_ir(), policy=policy
    )
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    model.policy = policy
    model.build_id = build["build_id"]
    prior = model.prepare_verification()
    node = model.revision().ir["nodes"][0]
    parameters = {
        **node["parameters"],
        "prompt": "根據完整輸入產生摘要",
        "output_fields": {"summary": "string"},
    }
    settings = {**node["llm_policy"], "max_output_tokens": 256, "budget_micro_usd": 80_000}
    changed = model.update_node_settings(
        model.project().version, "copy", parameters=parameters, llm_policy=settings
    )
    fresh = model.prepare_verification()
    assert fresh["request_hash"] != prior["request_hash"]
    monkeypatch.setattr(
        "workflow_studio.verifier.report.replay",
        lambda *a, **k: pytest.fail("stale authorization must not execute"),
    )
    report = model.verify(
        authorization=RuntimeAuthorization(prior["request_hash"], {"provider_key": "fixture"})
    )
    assert report["error_code"] == "APPROVAL_REQUIRED"
    version = model.project().version
    with pytest.raises(StudioError, match="預算"):
        model.update_node_settings(
            version, "copy", llm_policy={**settings, "budget_micro_usd": 200_000}
        )
    with pytest.raises(StudioError):
        model.update_node_settings(version, "copy", cacheable=True)
    assert model.revision() == changed


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e9999999", "1.5", ""])
def test_numeric_integer_form_rejects_nonfinite_or_fractional_values(bad):
    with pytest.raises(StudioError):
        numeric(bad, integer=True)


def test_money_form_rounds_up_to_micro_unit():
    assert numeric("0.0000001", money=True) == 1
    assert numeric("1.234567", money=True) == 1_234_567
    assert numeric("12", integer=True) == 12


def test_new_branch_and_model_templates_use_current_policy_and_symbolic_secret(tmp_path):
    from workflow_studio.conversation.graph import GraphDraft

    studio, p, original, _ = build_ir(tmp_path)
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    model.configure_provider(
        "https://fixture.invalid/chat",
        "fixture-model",
        1.0,
        1.0,
        1.0,
        runtime_enabled=True,
        runtime_budget_usd=0.2,
    )
    draft = GraphDraft(model.revision().ir)
    branch = model.node_template(
        "branch",
        "control.branch@1.0.0",
        "input://source",
        "branch-result.txt",
        **model.node_defaults("control.branch@1.0.0"),
    )
    defaults = model.node_defaults("llm.structured@1.0.0", remaining_budget=100_000)
    llm = model.node_template(
        "model", "llm.structured@1.0.0", "input://source", "answer.json", **defaults
    )
    draft.ir["nodes"].extend([branch, llm])
    draft.ir["secrets"] = ["provider_key"]
    draft.ir["outputs"] = {
        "branch": "artifact://branch/result",
        "model": "artifact://model/result",
        "copy": "artifact://copy/result",
    }
    changed = model.edit_graph(model.project().version, **draft.changes())
    assert changed.ir["nodes"][-1]["llm_policy"]["api_key"] == "secret://provider_key"
    assert model.compile()["status"] == "candidate"
    with pytest.raises(StudioError, match="預算"):
        model.node_defaults("llm.structured@1.0.0", remaining_budget=0)
