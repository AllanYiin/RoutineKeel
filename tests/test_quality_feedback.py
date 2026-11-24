import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from flow_base.graph.session import GraphSession
from flow_base.quality import load_profile
from flow_base.runtime import FlowError, Runtime
from flow_base.storage import migrations
from flow_base.storage.delivery import delivery_root
from test_graph_builder import data_graph, import_graph
from test_graph_scheduler import setup


def quality_graph(*, human=False):
    graph = data_graph()
    graph["quality_contract"].update(
        success_criteria=["交付值須為 OK"],
        evaluators=[
            {
                "id": "value",
                "evaluator": "equals",
                "version": "1",
                "output": "upper",
                "expected": "OK",
            }
        ],
        required_checks=["value"],
        review_policy={"required": human},
        personalization={"parameters": {"length": {"type": "integer", "minimum": 1, "maximum": 8}}},
    )
    return graph


def decision(service, sid, kind, *, request=None, outputs=None):
    p = service.workflow_quality(sid)
    args = {
        "expected_revision": p["revision"],
        "request_id": request or kind,
        "preview_id": p["preview_id"],
        "decision": kind,
        "reason": "本次驗收",
        "user_text": "確認本次" + kind,
        "scope": "this_run",
        "tags": ["測試"],
    }
    if outputs is not None:
        args["outputs"] = outputs
    return service.workflow_quality(sid, "feedback", args), args


def test_processed_rejected_preview_leaves_official_artifacts_and_pointer_unchanged(
    tmp_path, monkeypatch
):
    svc, key, res = import_graph(tmp_path, quality_graph())
    build = svc.install_workflow(draft_id=key, resolution=res)
    svc.install_workflow(
        draft_id=key, build_id=build["build_id"], install_hash=build["install_hash"]
    )
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"inputs": {"text": "ok"}}))
    first = svc.run_workflow_sync({"task": "graph_copy", "source": source})
    assert first["result_status"] == "accepted" and first["delivery_status"] == "published"
    target = Path(first["output"])
    pointer = delivery_root(target) / "current.json"
    before = pointer.read_bytes()
    from flow_base import quality

    with monkeypatch.context() as fault:
        fault.setattr(
            quality,
            "evaluate",
            lambda *args, **kwargs: {
                "result_status": "blocked",
                "checks": [
                    {
                        "id": "value",
                        "severity": "required",
                        "on_failure": "reject",
                        "status": "not_run",
                    }
                ],
            },
        )
        withheld = svc.run_workflow_sync({"task": "graph_copy", "source": source, "output": target})
    assert withheld["delivery_status"] == "withheld" and pointer.read_bytes() == before
    source.write_text(json.dumps({"inputs": {"text": "bad"}}))
    with pytest.raises(FlowError, match="another job"):
        svc.run_workflow_sync({"task": "graph_copy", "source": source, "output": target})
    second_target = tmp_path / "second-job"
    failed = svc.run_workflow_sync(
        {"task": "graph_copy", "source": source, "output": second_target}
    )
    assert not (delivery_root(second_target) / "current.json").exists()
    assert (
        failed["execution_status"] == "succeeded"
        and failed["result_status"] == "rejected"
        and failed["delivery_status"] == "withheld"
    )
    assert (
        pointer.read_bytes() == before
        and json.loads((target / "artifacts/results.json").read_bytes())["upper"] == "OK"
    )
    assert Path(failed["output"]) != target
    assert (
        json.loads((Path(failed["output"]) / "artifacts/results.json").read_bytes())["upper"]
        == "BAD"
    )
    assert svc.list_run_artifacts(failed["run_id"])
    sid = failed["blueprint"]["session_id"]
    with pytest.raises(FlowError):
        decision(svc, sid, "accept")
    event, args = decision(svc, sid, "correct", outputs={"original": "bad", "upper": "OK"})
    assert (
        event["after"]["result_status"] == "accepted"
        and event["before_hash"] != event["after_hash"]
    )
    assert svc.workflow_quality(sid, "feedback", args) == event
    with pytest.raises(FlowError):
        svc.workflow_quality(sid, "feedback", {**args, "user_text": "different"})
    assert (
        json.loads((Path(failed["output"]) / "artifacts/results.json").read_bytes())["upper"]
        == "BAD"
    )
    published = svc.blueprint_task(sid, "next")
    assert published["delivery_status"] == "published" and published["outputs"]["upper"] == "OK"


def test_human_rejection_withdrawal_and_missing_evaluator(tmp_path):
    from flow_base.core.service import WorkflowService

    graph = quality_graph(human=True)
    rt, plan, b = setup(tmp_path, graph)
    session = GraphSession.begin(rt, plan, {"inputs": {"text": "ok"}}, tmp_path, b)
    sid = session["session_id"]
    svc = WorkflowService(tmp_path)
    assert svc.workflow_quality(sid)["result_status"] == "review_required"
    rejected, _ = decision(svc, sid, "reject")
    assert rejected["after"]["result_status"] == "rejected"
    withdrawn, _ = decision(svc, sid, "withdraw")
    assert withdrawn["after"]["result_status"] == "review_required"
    accepted, _ = decision(svc, sid, "accept")
    assert accepted["after"]["result_status"] == "accepted"
    assert len(svc.workflow_quality(sid, "history")) == 3
    graph["quality_contract"]["required_checks"].append("unavailable")
    rt, plan, b = setup(tmp_path, graph)
    blocked = GraphSession.begin(rt, plan, {"inputs": {"text": "ok"}}, tmp_path, b)
    assert blocked["result_status"] == "blocked"
    with pytest.raises(FlowError):
        decision(svc, blocked["session_id"], "accept")


def test_profile_revision_must_be_approved_bounded_and_pinned_to_graph(tmp_path):
    from flow_base.core.service import WorkflowService

    rt, plan, b = setup(tmp_path, quality_graph())
    view = GraphSession.begin(rt, plan, {"inputs": {"text": "ok"}}, tmp_path, b)
    svc = WorkflowService(tmp_path)
    params = {
        "identity": "short",
        "expected_revision": 0,
        "settings": {"length": 2},
        "scope": "candidate",
        "user_text": "建議長度",
    }
    saved = svc.workflow_quality(view["session_id"], "save_profile", params)
    assert saved["revision"] == 1
    with pytest.raises(FlowError):
        load_profile(rt, plan["graph"], "short", 1)
    with pytest.raises(FlowError):
        svc.workflow_quality(
            view["session_id"], "save_profile", {**params, "settings": {"invariant": False}}
        )
    with pytest.raises(FlowError):
        svc.workflow_quality(
            view["session_id"], "save_profile", {**params, "settings": {"length": 30}}
        )
    svc.workflow_quality(
        view["session_id"], "save_profile", {**params, "scope": "approved", "expected_revision": 1}
    )
    applied = GraphSession.begin(
        rt,
        plan,
        {"inputs": {"text": "ok"}, "profile_id": "short", "profile_revision": 2},
        tmp_path,
        b,
    )
    snapshot = json.loads(GraphSession(rt, applied["session_id"]).path.read_bytes())
    assert snapshot["config"]["length"] == 2 and snapshot["profile"]["revision"] == 2


def test_v4_database_backup_can_restore_before_quality_migration(tmp_path, monkeypatch):
    home = tmp_path / "share_states"
    with monkeypatch.context() as m:
        m.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:4])
        Runtime(home).initialize()
        with sqlite3.connect(home / "state.sqlite3") as db:
            db.execute("INSERT INTO flows VALUES('flow_old','task','1','hash','Old',1,1)")
    Runtime(home).initialize()
    backup = home / "state.sqlite3.before-v005.sqlite3"
    assert (
        hashlib.sha256(backup.read_bytes()).hexdigest()
        == Path(str(backup) + ".sha256").read_text().strip()
    )
    restored = tmp_path / "restored.sqlite3"
    with (
        sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as src,
        sqlite3.connect(restored) as dst,
    ):
        src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert dst.execute("PRAGMA user_version").fetchone()[0] == 4
        assert dst.execute("SELECT flow_id FROM flows").fetchone()[0] == "flow_old"


def test_required_evaluator_not_run_blocks_builder_receipt(tmp_path):
    graph = quality_graph()
    graph["quality_contract"]["required_checks"].append("missing-evaluator")
    svc, key, res = import_graph(tmp_path, graph)
    with pytest.raises(FlowError):
        svc.install_workflow(draft_id=key, resolution=res)
    failed = svc.install_workflow(draft_id=key)["builds"][0]
    assert failed["status"] == "blocked" and "品質評估器" in failed["reason"]


def test_missing_source_asset_cannot_be_accepted_from_an_old_preview(tmp_path):
    from flow_base.blueprints.sessions import Assets
    from flow_base.core.service import WorkflowService

    graph = quality_graph(human=True)
    file_port = {"type": "file"}
    task = graph["nodes"][1]
    task.update(
        capability="json.identity",
        inputs={"source": {"source": "input://source", "port": file_port}},
        outputs={"file": file_port},
    )
    graph.update(
        inputs={"source": file_port},
        nodes=[task],
        outputs={"file": "artifact://copy/file"},
        quality_contract={"golden_cases": [{"fixture": True}], "review_policy": {"required": True}},
    )
    rt, plan, b = setup(tmp_path, graph)
    original = tmp_path / "sample.txt"
    original.write_text("initial", encoding="utf-8")
    view = GraphSession.begin(
        rt, plan, {"inputs": {"source": {"$file": "sample.txt"}}}, tmp_path, b
    )
    svc = WorkflowService(tmp_path)
    seen = svc.workflow_quality(view["session_id"])
    Assets(rt).resolve(seen["outputs"]["file"]).unlink()
    with pytest.raises(FlowError):
        svc.workflow_quality(
            view["session_id"],
            "feedback",
            {
                "expected_revision": seen["revision"],
                "request_id": "no-source",
                "preview_id": seen["preview_id"],
                "decision": "accept",
                "reason": "original",
                "user_text": "確認",
            },
        )
    assert original.read_text() == "initial" and not svc.workflow_quality(
        view["session_id"], "history"
    )
