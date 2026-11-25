"""Offline candidate verifier in a separate interpreter and disposable workspace."""

import shutil
import sys
from contextlib import suppress
from pathlib import Path

from ..outputs import reject_links
from ..runtime import atomic_json, digest, read_json
from .sessions import ASSET, Session
from .source import require


def fixture_destination(root, name):
    require(isinstance(name, str), "驗收素材路徑須為文字")
    rel = Path(name)
    require(
        not rel.is_absolute() and not rel.drive and len(rel.parts)>1 and rel.parts[0]=="fixtures"
        and ".." not in rel.parts and not any(p.startswith(".") for p in rel.parts),
        "請將驗收素材放到工作區 fixtures/ 內",
    )
    src = root / rel
    reject_links(src)
    return src


def fixture_path(root, name):
    src = fixture_destination(root, name)
    require(src.is_file() and src.stat().st_size<=512*1024**2,"驗收素材無效")
    return src


def stage_fixtures(root, trial, value):
    if isinstance(value, list):
        for item in value:
            stage_fixtures(root, trial, item)
    if isinstance(value, dict):
        if set(value) == {"$file"}:
            src = fixture_path(root,value["$file"])
            dest = trial / src.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        else:
            for child in value.values():
                stage_fixtures(root, trial, child)


def matches(actual, expected, assets):
    if expected == {"$artifact": True}:
        return (
            isinstance(actual, str)
            and ASSET.fullmatch(actual)
            and assets.resolve(actual).stat().st_size > 0
        )
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and bool(expected)
            and all(k in actual and matches(actual[k], v, assets) for k, v in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(matches(a, b, assets) for a, b in zip(actual, expected))
        )
    return actual == expected


def _write_progress(path, snapshot):
    if path is not None:
        with suppress(PermissionError):
            atomic_json(path, snapshot)


def exercise(root, workflow, measurements=None, progress=None, progress_path=None):
    from ..core.service import WorkflowService

    service = WorkflowService(root)
    results = []
    for index, case in enumerate(read_json(root / "cases.json")):
        if progress is not None:
            progress.update(case=index + 1, phase="prepare")
            _write_progress(progress_path, progress)
        request = root / f"input-{index}.json"
        require(
            isinstance(case.get("inputs"), dict) and case.get("expected"),
            "案例缺少輸入或預期成果",
        )
        atomic_json(request, {"inputs": case["inputs"], "config": case.get("config", {})})
        options = {"task": workflow, "source": request, "offline": True}
        planned = service.plan_workflow(options)
        require(not planned.plan["blocked"], "候選包計畫無法執行")
        require(
            service.prepare_workflow(workflow, offline=True)["ok"]
            and service.verify_workflow(workflow)["ok"],
            "環境或流程驗證失敗",
        )
        report = service.run_workflow_sync(options)
        if progress is not None:
            progress["phase"] = "exercise"
            _write_progress(progress_path, progress)
        view = report["blueprint"]
        session = Session(service.workspace.runtime, view["session_id"])
        is_graph = read_json(session.path)["plan"].get("format") == 2
        if is_graph:
            from ..graph.iterations import merged_meter
            from ..graph.session import GraphSession

            session = GraphSession(service.workspace.runtime, view["session_id"])
        mocks = []
        for reply in case.get("replies", []):
            candidates=[t for t in view.get("pending_tasks",[view["pending"]]) if t and t["step"]==reply["step"]
                        and t["kind"]==reply["kind"] and (not reply.get("child_step") or t.get("child_step")==reply["child_step"])]
            require(len(candidates)==1,"案例待辦缺失或不明確")
            pending=candidates[0]
            require(
                pending and pending["step"] == reply["step"] and pending["kind"] == reply["kind"],
                "案例待辦不符",
            )
            payload = {"revision": view["revision"], "task_id": pending["id"]}
            payload.update(
                scope_hash=pending.get("scope_hash"), payload_hash=pending.get("payload_hash")
            )
            if pending["kind"] == "authorize_external":
                view = session.interact(
                    "authorize",
                    {
                        **payload,
                        "scope_hash": pending["scope_hash"],
                        "payload_hash": pending["payload_hash"],
                        "user_text": "模擬此案例的具體授權",
                    },
                )
                mocks.append({"step": pending["step"], "kind": pending["kind"]})
                continue
            if pending.get("claim_required"):
                view = session.interact(
                    "claim", payload, **({"simulation": True} if is_graph else {})
                )
                payload["revision"] = view["revision"]
                if pending.get("idempotency_key"):
                    from ..graph.external import record_observation
                    stream=None
                    chunks=reply.get("deltas",[])
                    if chunks:
                        stream_hash=None
                        for sequence,text in enumerate(chunks):
                            view=session.interact("stream_delta",{**payload,"sequence":sequence,"text":text})
                            payload["revision"]=view["revision"]
                            stream_hash=digest([stream_hash,sequence,text])
                        stream={"count":len(chunks),"hash":stream_hash,"completed":reply.get("stream_completed") is True}
                    record_observation(
                        root,
                        pending["idempotency_key"],
                        pending["scope_hash"],
                        "trial-fixture",
                        "succeeded",
                        reply.get("outputs", {}),
                        usage=reply.get("usage"),stream=stream,
                    )
            mocks.append({"step": pending["step"], "kind": pending["kind"]})
            view = session.interact(
                "complete",
                {
                    **payload,
                    "outputs": reply.get("outputs", {}),
                    "route": reply.get("route"),
                    "evidence": {
                        "user_text": "試做案例提供的校閱回覆",
                        "actor": "trial-fixture",
                        "decision": "approved",
                        "receipt": "fixture-only",
                    },
                },
            )
        if is_graph and measurements is not None:
            usage=merged_meter(read_json(session.path)["nodes"])
            measurements.append({"case":index,"run_id":report["run_id"],
                "review_count":sum(t["kind"] in {"human","review","authorize_external","iteration_review","route_review"} for t in mocks),
                **usage,"result_status":view.get("result_status"),"output_hash":digest(view.get("outputs"))})
        require(view["status"] == "complete", "驗收案例仍有未完成步驟")
        if progress is not None:
            progress["phase"] = "compare"
            _write_progress(progress_path, progress)
        require(
            set(view["outputs"]) == set(case["expected"])
            and matches(view["outputs"], case["expected"], session.assets),
            "實際輸出與預期成果不符",
        )
        if view.get("assessment_id"):
            require(view["result_status"]!="blocked","候選工作流缺少必要品質評估器")
            if case.get("expected_result_status"):
                require(view["result_status"]==case["expected_result_status"],"試做的業務判定不符")
            else:
                require(view["result_status"]!="rejected","候選成果未通過必要品質條件")
        if mocks:
            atomic_json(request, {"session_id": session.id})
            report = service.run_workflow_sync({**options, "output": root / f"completed-{index}"})
        require(report["blueprint"]["status"] == "complete", "執行交付未完成")
        # Preserve the artifacts actually checked by the independent verifier.
        label = f"case-{index + 1}"
        evidence_dir = root / "evidence" / label
        shutil.copytree(Path(report["output"]) / "artifacts", evidence_dir / "artifacts")
        snapshot = read_json(session.path)
        atomic_json(
            evidence_dir / "observations.json",
            [
                {
                    "step": r["step"],
                    "outputs_hash": digest(r["outputs"]),
                    "evidence_kind": "fixture"
                    if any(x["step"] == r["step"] for x in mocks)
                    else "local_execution",
                    **({"selected_route": r["evidence"]["selected"]}
                       if r["evidence"].get("kind") == "route" else {}),
                    **({"fallback_used": r["evidence"].get("fallback_used", [])}
                       if r["evidence"].get("kind") == "join" else {}),
                }
                for r in snapshot["history"]
            ],
        )
        results.append(
            {
                **({"usage": merged_meter(snapshot["nodes"])} if is_graph else {}),
                "case": index,
                "run_id": report["run_id"],
                "observed_outputs_hash": digest(view["outputs"]),
                "artifacts": "trials/" + label + "/artifacts",
                "simulated_host_and_review": mocks,
            }
        )
    return {"passed": True, "cases": results, "provider_calls": 0}


if __name__ == "__main__":
    trial, task = Path(sys.argv[1]), sys.argv[2]
    measurements=[]
    progress = {}
    progress_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    try:
        if sys.argv[4:] == ["--verify-load"]:
            from ..core.service import WorkflowService

            service = WorkflowService(trial)
            require(
                service.prepare_workflow(task, offline=True)["ok"]
                and service.verify_workflow(task)["ok"],
                "候選套件的隔離載入或綁定驗證失敗。",
            )
            result = {"passed": True, "runtime_load": "PASS", "cases": [], "provider_calls": 0}
        else:
            result = exercise(trial, task, measurements, progress, progress_path)
        atomic_json(trial / "result.json", result)
    except Exception as exc:  # noqa: BLE001 -- the verifier subprocess must report any candidate failure as a failed trial
        atomic_json(trial / "result.json", {
            "passed": False, "error": str(exc)[:1000], "code": getattr(exc, "code", type(exc).__name__),
            "measurements": measurements, **progress,
        })
        sys.exit(1)
