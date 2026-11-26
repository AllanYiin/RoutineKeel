"""Prepare a pinned wheel via completion and exercise the actual installed candidate."""

import io
import json
import os
import sys
from importlib.metadata import entry_points

import pytest
from flow_base.blueprints.wheel import write_wheel
from flow_base.graph.preparation import WheelPreparation, preparation_id, prepare_wheels
from flow_base.runtime import FlowError, sha256
from pydantic import ValidationError
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_toolchain_completion import report_graph


def test_candidate_checks_use_shorter_windows_workspace_staging(tmp_path, monkeypatch):
    from pathlib import Path

    from flow_base.blueprints import installer

    workspace = tmp_path / "workspace"
    deep_temp = tmp_path / "sandbox-with-a-long-temporary-directory-name"
    workspace.mkdir()
    deep_temp.mkdir()
    monkeypatch.setattr(installer.tempfile, "gettempdir", lambda: str(deep_temp))
    with installer._temporary_check(workspace, "rk-trial-") as temporary:
        path = Path(temporary)
        assert path.parent == (workspace if sys.platform == "win32" else deep_temp)
        assert path.is_dir()
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only short-staging fallback")
def test_silent_windows_uv_failure_retries_in_short_staging(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from flow_base.graph import preparation
    from flow_base.runtime import Runtime, read_json

    host, workspace = tmp_path / "host", tmp_path / "workspace"
    host.mkdir()
    workspace.mkdir()
    recipe = WheelPreparation.model_validate(preparation_recipe(host))
    runtime = Runtime(workspace / "share_states")
    invocations = []
    real_run = preparation.subprocess.run

    def quiet_first(argv, **kwargs):
        if "--target" in argv:
            invocations.append(kwargs["cwd"])
            if len(invocations) == 1:
                return SimpleNamespace(returncode=1, stderr=b"")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(preparation.subprocess, "run", quiet_first)
    receipt = prepare_wheels(runtime, recipe, host)
    assert len(invocations) == 2 and len(str(invocations[1])) < len(str(invocations[0]))
    diagnostic = read_json(runtime.home / "toolchain_preparations" / receipt["operation_id"] / "preparation-diagnostic.json")
    assert diagnostic["fallback"] == "short_staging" and diagnostic["initial_failure"]["code"] == "STAGING_PATH_UNUSABLE"


@pytest.mark.parametrize("stderr,loader_error", [
    ("ImportError: DLL load failed while importing _core: 檔名太長", "DLL load failed while importing _core: 檔名太長"),
    ("RuntimeError: private sample transcript", None),
])
def test_prepared_runner_preserves_loader_diagnostic_without_tool_output(tmp_path, monkeypatch, stderr, loader_error):
    from types import SimpleNamespace

    from flow_base.graph import preparation

    recipe = preparation_recipe(tmp_path)
    monkeypatch.setattr(preparation.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=1, stderr=stderr.encode("utf-8"),
    ))
    with pytest.raises(FlowError) as failure:
        preparation.execute_prepared(tmp_path, {"preparation": recipe}, {}, {})
    assert failure.value.details.get("loader_error") == loader_error
    assert "private sample transcript" not in str(failure.value.details)


def preparation_recipe(host, *, package="rk_fixture_prepared", tool="fixture.prepared", recipe_id="fixture.prepare_upper"):
    wheel = host / f"{package}-1.0.0-py3-none-any.whl"
    write_wheel(
        wheel,
        package,
        "1.0.0",
        {
            f"{package}/__init__.py": b'def apply(inputs, parameters):\n    return {"result": inputs["source"].upper()}\n',
            f"{package}-1.0.0.dist-info/entry_points.txt": (
                f"[routinekeel.portable_adapters]\n{tool}={package}:apply\n".encode()
            ),
        },
        dependencies=(),
    )
    return {
        "recipe_schema_version": 1,
        "id": recipe_id,
        "version": "1.0.0",
        "action": "install_locked_wheels",
        "tool": tool,
        "distribution": package.replace("_", "-"),
        "platforms": [sys.platform],
        "wheels": [
            {
                "artifact": wheel.name,
                "sha256": sha256(wheel),
                "distribution": package.replace("_", "-"),
                "version": "1.0.0",
            }
        ],
    }


def _approval_branch(name, recipe, allowed):
    text = {"type": "string", "required": True}
    capability = f"fixture.{name}_text"
    record = adapter(recipe["tool"], cap=capability, locus="local_runtime")
    record.update(inputs={"source": text}, outputs={"result": text}, preparation=recipe, preparation_allowed=allowed)
    record["portable"].update(distribution=recipe["distribution"], inputs=record["inputs"], outputs=record["outputs"])
    node = {"id": name, "kind": "task", "capability": capability, "capability_version": "1.0.0",
            "permissions": [], "side_effect": "none", "inputs": {"source": {"source": "input://source", "port": text}},
            "outputs": {"result": text}, "capability_requirement": {"id": name, "capability": capability,
                "version": "1.0.0", "required": True, "portability": "flexible"}}
    return record, node


def _approval_graph(*nodes):
    graph = report_graph()
    graph.update(graph_contract_version="2.3", inputs={"source": {"type": "string", "required": True}}, nodes=list(nodes),
                 outputs={node["id"]: f"artifact://{node['id']}/result" for node in nodes})
    graph["quality_contract"] = {
        "golden_cases": [{"inputs": {"source": "example"}, "expected": {node["id"]: "EXAMPLE" for node in nodes}}],
        "adapter_cases": {node["id"]: [{"id": word, "inputs": {"source": word}, "expected": {"result": word.upper()}}
                                          for word in ("first", "second")] for node in nodes},
    }
    return graph


def test_waiting_approval_reuses_independent_qualification_after_restart(observed_inventory):
    from flow_base.core.service import WorkflowService

    workspace = observed_inventory()
    host = workspace.parent / "host"
    left_recipe = preparation_recipe(host)
    right_recipe = preparation_recipe(host, package="rk_fixture_right", tool="fixture.right", recipe_id="fixture.prepare_right")
    left, left_node = _approval_branch("left", left_recipe, True)
    right, right_node = _approval_branch("right", right_recipe, False)
    observed_inventory(left, right)
    service, draft, _ = import_graph(workspace, _approval_graph(left_node, right_node))
    pending = service.blueprint_completion("start", payload={"draft_id": draft, "intent": "install",
                                                             "offline": True, "request_id": "two-branches"})
    assert pending["status"] == "needs_input"
    assert pending["qualification"]["left"]["report"]["verdict"] == "PASS"
    assert "right" not in pending["preparations"]
    assert not service.workspace.discover(read_only=True)

    # The first adapter is already prepared and qualified; revoking permission to prepare
    # it again must not discard that receipt when the second branch becomes eligible.
    observed_inventory({**left, "preparation_allowed": False}, {**right, "preparation_allowed": True})
    reopened = WorkflowService(workspace)
    resumed = reopened.blueprint_completion("recheck", pending["work_id"], {
        "expected_revision": pending["revision"], "request_id": "second-branch-approved",
    })
    installed = wait_completion(reopened, resumed, ("installed", "needs_input"), timeout=120)
    assert installed["status"] == "installed", installed.get("gaps")
    assert installed["work_id"] == pending["work_id"]
    assert installed["qualification"]["left"] == pending["qualification"]["left"]
    assert installed["preparations"]["left"] == pending["preparations"]["left"]
    assert installed["qualification"]["right"]["report"]["verdict"] == "PASS"
    request = workspace / "input.json"
    request.write_text('{"inputs":{"source":"verified"}}', encoding="utf-8")
    output = reopened.run_workflow_sync({"task": "toolchain_report", "source": request, "offline": True})
    assert output["blueprint"]["outputs"] == {"left": "VERIFIED", "right": "VERIFIED"}


def test_revoked_preparation_grant_is_rechecked_at_dispatch(observed_inventory, monkeypatch):
    from flow_base.blueprints.binding_operations import BindingOperations

    workspace = observed_inventory()
    recipe = preparation_recipe(workspace.parent / "host")
    tool, node = _approval_branch("left", recipe, True)
    observed_inventory(tool)
    service, draft, _ = import_graph(workspace, _approval_graph(node))
    run = BindingOperations.run

    def revoke_before_claim(store, jobs, **kwargs):
        if any(operation.kind == "prepare" for operation, _, _ in jobs):
            observed_inventory({**tool, "preparation_allowed": False})
        return run(store, jobs, **kwargs)

    with monkeypatch.context() as queued:
        queued.setattr(BindingOperations, "run", revoke_before_claim)
        pending = service.blueprint_completion("start", payload={"draft_id": draft, "intent": "install",
                                                                 "offline": True, "request_id": "revoked-at-boundary"})
    assert pending["status"] == "needs_input" and not service.workspace.discover(read_only=True)
    assert pending["preparations"]["left"]["code"] == "PREPARATION_AUTHORIZATION_CHANGED"
    assert not (service.workspace.runtime.home / "toolchain_preparations").exists()

    observed_inventory(tool)
    resumed = service.blueprint_completion("recheck", pending["work_id"], {
        "expected_revision": pending["revision"], "request_id": "restore-revoked-grant",
    })
    installed = wait_completion(service, resumed, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")
    assert installed["work_id"] == pending["work_id"]
    assert installed["preparations"]["left"]["operation_id"] == pending["preparations"]["left"]["operation_id"]


def test_official_wheel_source_requires_download_grant_then_continues_same_work(observed_inventory, monkeypatch):
    workspace = observed_inventory()
    artifact_root = workspace.parent / "host"
    recipe = preparation_recipe(artifact_root)
    item = recipe["wheels"][0]
    wheel = artifact_root / item["artifact"]
    body = wheel.read_bytes()
    wheel.rename(artifact_root / "fixture-response.bin")
    source = f"https://files.pythonhosted.org/packages/fixtures/{item['artifact']}"
    item["source"] = {"url": source, "size": len(body), "license": "MIT (fixture)"}
    requests = []

    class Response(io.BytesIO):
        def geturl(self):
            return source

    def download(request, *, timeout):
        requests.append(request.full_url)
        return Response(body[:-1] + bytes([body[-1] ^ 1]) if len(requests) == 1 else body)

    monkeypatch.setattr("flow_base.graph.preparation._open_wheel", download)
    tool, node = _approval_branch("left", recipe, True)
    observed_inventory(tool)
    service, draft, _ = import_graph(workspace, _approval_graph(node))
    pending = service.blueprint_completion("start", payload={"draft_id": draft, "intent": "install",
                                                             "request_id": "download-awaiting-grant"})
    assert pending["status"] == "needs_input" and not requests
    assert pending["preparations"]["left"]["code"] == "PREPARATION_DOWNLOAD_APPROVAL_REQUIRED"
    option = next(row for gap in pending["gaps"] for row in gap.get("binding_options", {}).get("options", [])
                  if row["action"] == "prepare_adapter")
    assert option["download_allowed"] is False and option["download_sources"] == [item]
    offline = service.blueprint_completion("start", payload={"draft_id": draft, "intent": "install", "offline": True,
                                                             "request_id": "source-explicitly-offline"})
    assert offline["preparations"]["left"]["code"] == "OFFLINE_WHEEL_MISSING" and not requests

    observed_inventory({**tool, "download_allowed": True})
    resumed = service.blueprint_completion("recheck", pending["work_id"], {
        "expected_revision": pending["revision"], "request_id": "wheel-download-approved",
    })
    assert resumed["status"] == "needs_input" and requests == [source]
    assert resumed["preparations"]["left"]["code"] == "RESOURCE_HASH"
    target = service.workspace.runtime.home / "toolchain_preparations" / preparation_id(WheelPreparation.model_validate(recipe))
    assert not target.exists()
    retried = service.blueprint_completion("recheck", pending["work_id"], {
        "expected_revision": resumed["revision"], "request_id": "retry-verified-wheel-source",
    })
    installed = wait_completion(service, retried, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")
    assert installed["work_id"] == pending["work_id"] and requests == [source, source]
    receipt = installed["preparations"]["left"]["receipt"]
    assert receipt["wheel_sources"] == [{"artifact": item["artifact"], "version": "1.0.0",
                                         "sha256": item["sha256"], **item["source"]}]
    assert prepare_wheels(service.workspace.runtime, WheelPreparation.model_validate(recipe), artifact_root,
                          offline=True) == receipt
    assert requests == [source, source]


@pytest.mark.parametrize("url", [
    "http://files.pythonhosted.org/packages/rk_fixture_prepared-1.0.0-py3-none-any.whl",
    "https://downloads.example.invalid/packages/rk_fixture_prepared-1.0.0-py3-none-any.whl",
    "https://files.pythonhosted.org/packages/rk_fixture_prepared-1.0.1-py3-none-any.whl",
    "https://files.pythonhosted.org/packages/rk_fixture_prepared-1.0.0-py3-none-any.whl?token=untrusted",
])
def test_locked_wheel_source_rejects_untrusted_or_mismatched_urls(tmp_path, url):
    recipe = preparation_recipe(tmp_path)
    recipe["wheels"][0]["source"] = {"url": url, "size": 500, "license": "MIT (fixture)"}
    with pytest.raises(ValidationError):
        WheelPreparation.model_validate(recipe)


@pytest.mark.parametrize("authorized_at_start", [True, False], ids=["approved", "approval-later"])
def test_completion_prepares_only_the_selected_recipe(observed_inventory, authorized_at_start):
    workspace = observed_inventory()
    host = workspace.parent / "host"
    recipes = [preparation_recipe(host), preparation_recipe(
        host, package="rk_fixture_alternative", tool="fixture.alternative", recipe_id="fixture.prepare_alternative",
    )]
    port, capability = {"type": "string", "required": True}, "fixture.prepared_contract"
    records = []
    for recipe in recipes:
        record = adapter(recipe["tool"], cap=capability, locus="local_runtime")
        record.update(inputs={"source": port}, outputs={"result": port}, preparation=recipe, preparation_allowed=True)
        record["portable"].update(distribution=recipe["distribution"], inputs=record["inputs"], outputs=record["outputs"])
        records.append(record)
    observed_inventory(*(records if authorized_at_start else [
        {**record, "preparation_allowed": False} for record in records
    ]))
    graph = report_graph()
    graph.update(graph_contract_version="2.3", inputs={"source": port}, outputs={"result": "artifact://reconcile/result"})
    graph["nodes"][0].update(
        capability=capability, inputs={"source": {"source": "input://source", "port": port}}, outputs={"result": port},
        capability_requirement={"id": "prepared_tool", "capability": capability, "version": "1.0.0",
                                "required": True, "portability": "flexible"},
    )
    cases = [{"inputs": {"source": word}, "expected": {"result": word.upper()}} for word in ("one", "two")]
    graph["quality_contract"] = {"golden_cases": cases, "adapter_cases": {"reconcile": [
        {"id": f"case_{index}", **case} for index, case in enumerate(cases)
    ]}}
    service, draft, _ = import_graph(workspace, graph)
    work = service.blueprint_completion("start", payload={"draft_id": draft, "intent": "install",
                                                           "offline": True, "request_id": "choose-recipe"})
    root = service.workspace.runtime.home / "toolchain_preparations"
    if not authorized_at_start:
        assert work["status"] == "needs_input" and not root.exists()
        assert not work["binding_plan"]["tool_selection"]
        original = work["work_id"]
        research = work["binding_plan"]["research_required"][0]
        source = "https://docs.astral.sh/uv/reference/cli/#uv-pip-install--require-hashes"
        work = service.blueprint_completion("submit", original, {
            "action": "propose_bindings", "request_id": "record-wheel-source", "expected_revision": work["revision"],
            "proposals": [{"gap_id": research["gap_id"], "scope_hash": research["scope_hash"], "options": [{
                "title": "依版本與 SHA-256 核對 wheel", "approach": "local_tool", "sources": [source],
                "plan": "確認核准清冊的版本和校驗值後，從本機 wheel 建立隔離 Adapter。",
            }]}],
        })
        assert work["binding_research"]["reconcile"][0]["sources"] == [source] and not root.exists()
        observed_inventory(*records)
        work = service.blueprint_completion("recheck", original, {
            "request_id": "refresh-approval", "expected_revision": work["revision"],
        })
        assert work["work_id"] == original
    options = work["binding_plan"]["tool_selection"][0]
    choices = [row for row in options["options"] if row["action"] == "prepare_adapter"]
    assert {row["title"] for row in choices} == {item["tool"] for item in recipes}
    assert not root.exists()
    selected = next(row for row in choices if row["title"] == "fixture.alternative")
    started = service.blueprint_completion("submit", work["work_id"], {
        "action": "select_bindings", "request_id": "select-recipe", "expected_revision": work["revision"],
        "user_text": "採用此測試中的第二個已核對配方", "selections": [{
            "gap_id": options["gap_id"], "scope_hash": options["scope_hash"], "option_id": selected["id"],
        }],
    })
    installed = wait_completion(service, started, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")
    assert installed["work_id"] == work["work_id"]
    assert installed["preparations"]["reconcile"]["operation_id"] == selected["preparation_id"]
    assert installed["qualification"]["reconcile"]["report"]["adapter"] == "fixture.alternative@1.0.0"
    assert (root / selected["preparation_id"] / "receipt.json").is_file()
    assert not (root / preparation_id(WheelPreparation.model_validate(recipes[0])) / "receipt.json").exists()


@pytest.mark.parametrize("chained", [False, True], ids=["direct", "toolchain"])
def test_authorized_recipe_prepares_then_qualifies_and_runs(observed_inventory, chained):  # noqa: F811
    workspace = observed_inventory()
    host = workspace.parent / "host"
    recipe = preparation_recipe(host)
    text = {"type": "string", "required": True}
    tool = adapter("fixture.prepared", cap="fixture.prepared_contract", locus="local_runtime")
    tool.update(
        inputs={"source": text},
        outputs={"result": text},
        preparation=recipe,
        preparation_allowed=True,
    )
    tool["portable"].update(
        distribution="rk-fixture-prepared", inputs=tool["inputs"], outputs=tool["outputs"]
    )
    observed_inventory(tool)
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"source": text},
        outputs={"result": "artifact://reconcile/result"},
    )
    node = graph["nodes"][0]
    node.update(
        capability="fixture.prepared_contract",
        inputs={"source": {"source": "input://source", "port": text}},
        outputs={"result": text},
        capability_requirement={
            "id": "prepared_tool",
            "capability": "fixture.prepared_contract",
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
        },
    )
    examples = [("one", "ONE"), ("two", "TWO"), ("three", "THREE")]
    if chained:
        names = {entry.name for entry in entry_points(group="routinekeel.portable_adapters")}
        if "routinekeel.csv_read" not in names:
            if os.environ.get("ROUTINEKEEL_E2E_ENFORCE") == "1":
                pytest.fail("The Data Adapter wheel is required for the preparation toolchain case")
            pytest.skip("The optional Data Adapter wheel is not installed")
        rows = {"type": "array", "required": True}
        tool["portable"].update(
            capability="fixture.normalize_csv",
            inputs={"source": {**text, "schema_id": "fixture.raw_csv"}},
            outputs={"result": {**text, "schema_id": "fixture.normalized_csv"}},
        )
        reader = adapter("routinekeel.csv_read", cap="fixture.csv_reader", locus="local_runtime")
        reader.update(inputs={"source": text}, outputs={"rows": rows})
        reader["portable"].update(
            inputs={"source": {**text, "schema_id": "fixture.normalized_csv"}},
            outputs={"rows": {**rows, "schema_id": "fixture.uppercase_rows"}},
        )
        observed_inventory(tool, reader)
        node["capability_requirement"].update(
            input_semantics={"source": "fixture.raw_csv"},
            output_semantics={"result": "fixture.uppercase_rows"},
        )
        node["outputs"] = {"result": rows}
        examples = [(f"name\n{value}\n", [{"NAME": expected}]) for value, expected in examples]
    cases = [
        {"inputs": {"source": value}, "expected": {"result": expected}}
        for value, expected in examples[:2]
    ]
    graph["quality_contract"] = {
        "golden_cases": cases,
        "adapter_cases": {
            "reconcile": [{"id": f"case_{index}", **case} for index, case in enumerate(cases)],
        },
    }
    service, draft, _ = import_graph(workspace, graph)
    initial = service.resolve_blueprint_capabilities(draft, offline=True)
    assert any(
        item["code"] == "DEPENDENCY_NOT_INSTALLED" and item.get("recipe_id") == recipe["id"]
        for item in initial["portable_decisions"][0]["rejections"]
    )
    request = {
        "draft_id": draft,
        "intent": "install",
        "offline": True,
        "request_id": "prepare-install",
    }
    started = service.blueprint_completion("start", payload=request)
    final = wait_completion(service, started, ("installed", "needs_input"))
    assert final["status"] == "installed", final.get("gaps")
    prepared = final["preparations"]["reconcile"]
    assert (
        prepared["status"] == "prepared"
        and final["qualification"]["reconcile"]["report"]["verdict"] == "PASS"
    )
    if chained:
        assert final["qualification"]["reconcile"]["report"]["toolchain_graph_hash"]
    assert service.blueprint_completion("start", payload=request) == started
    assert (
        prepare_wheels(service.workspace.runtime, WheelPreparation.model_validate(recipe), host)
        == prepared["receipt"]
    )
    path = workspace / "input.json"
    path.write_text(json.dumps({"inputs": {"source": examples[2][0]}}), encoding="utf-8")
    output = service.run_workflow_sync(
        {"task": "toolchain_report", "source": path, "offline": True}
    )
    assert output["blueprint"]["outputs"] == {"result": examples[2][1]}

    runtime = service.workspace.runtime
    bad = json.loads(json.dumps(recipe))
    bad["wheels"][0]["sha256"] = "f" * 64
    invalid = WheelPreparation.model_validate(bad)
    with pytest.raises(FlowError, match="校驗值"):
        prepare_wheels(runtime, invalid, host)
    target = runtime.home / "toolchain_preparations"
    assert not (target / preparation_id(invalid) / "receipt.json").exists()
    path = target / prepared["operation_id"] / "site/rk_fixture_prepared/__init__.py"
    path.write_bytes(path.read_bytes() + b"\n# drift\n")
    stale = service.resolve_blueprint_capabilities(draft, offline=True)
    assert stale["gaps"][0]["code"] == "BINDING_STALE"
