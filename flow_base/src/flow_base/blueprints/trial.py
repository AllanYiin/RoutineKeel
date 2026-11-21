"""Offline candidate verifier in a separate interpreter and disposable workspace."""

import shutil
import sys
from pathlib import Path

from ..outputs import reject_links
from ..runtime import atomic_json, digest, read_json
from .sessions import Session, ASSET
from .source import require


def stage_fixtures(root, trial, value):
    if isinstance(value, list):
        for item in value:
            stage_fixtures(root, trial, item)
    if isinstance(value, dict):
        if set(value) == {"$file"}:
            rel = Path(value["$file"])
            require(
                not rel.is_absolute()
                and not rel.drive
                and len(rel.parts) > 1
                and rel.parts[0] == "fixtures"
                and ".." not in rel.parts
                and not any(p.startswith(".") for p in rel.parts),
                "請將驗收素材放到工作區 fixtures/ 內",
            )
            src = root / rel
            reject_links(src)
            require(
                src.is_file() and src.stat().st_size <= 512 * 1024**2, "驗收素材無效"
            )
            dest = trial / rel
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
            and all(
                k in actual and matches(actual[k], v, assets)
                for k, v in expected.items()
            )
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(matches(a, b, assets) for a, b in zip(actual, expected))
        )
    return actual == expected


def exercise(root, workflow):
    from ..core.service import WorkflowService

    service = WorkflowService(root)
    results = []
    for index, case in enumerate(read_json(root / "cases.json")):
        request = root / f"input-{index}.json"
        require(
            isinstance(case.get("inputs"), dict) and case.get("expected"),
            "案例缺少輸入或預期成果",
        )
        atomic_json(
            request, {"inputs": case["inputs"], "config": case.get("config", {})}
        )
        options = {"task": workflow, "source": request, "offline": True}
        planned = service.plan_workflow(options)
        require(not planned.plan["blocked"], "候選包計畫無法執行")
        require(
            service.prepare_workflow(workflow, offline=True)["ok"]
            and service.verify_workflow(workflow)["ok"],
            "環境或流程驗證失敗",
        )
        report = service.run_workflow_sync(options)
        view = report["blueprint"]
        session = Session(service.workspace.runtime, view["session_id"])
        mocks = []
        for reply in case.get("replies", []):
            pending = view["pending"]
            require(
                pending
                and pending["step"] == reply["step"]
                and pending["kind"] == reply["kind"],
                "案例待辦不符",
            )
            payload = {"revision": view["revision"], "task_id": pending["id"]}
            if pending.get("claim_required"):
                view = session.interact("claim", payload)
                payload["revision"] = view["revision"]
            mocks.append({"step": pending["step"], "kind": pending["kind"]})
            view = session.interact(
                "complete",
                {
                    **payload,
                    "outputs": reply.get("outputs", {}),
                    "evidence": {
                        "actor": "trial-fixture",
                        "decision": "approved",
                        "receipt": "fixture-only",
                    },
                },
            )
        require(view["status"] == "complete", "驗收案例仍有未完成步驟")
        require(
            set(view["outputs"]) == set(case["expected"])
            and matches(view["outputs"], case["expected"], session.assets),
            "實際輸出與預期成果不符",
        )
        if mocks:
            atomic_json(request, {"session_id": session.id})
            report = service.run_workflow_sync(
                {**options, "output": root / f"completed-{index}"}
            )
        require(report["blueprint"]["status"] == "complete", "執行交付未完成")
        # Preserve the artifacts actually checked by the independent verifier.
        label = f"case-{index + 1}"
        evidence_dir = root / "evidence" / label
        shutil.copytree(
            Path(report["output"]) / "artifacts", evidence_dir / "artifacts"
        )
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
                }
                for r in snapshot["history"]
            ],
        )
        results.append(
            {
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
    try:
        atomic_json(trial / "result.json", exercise(trial, task))
    except Exception as exc:
        atomic_json(trial / "result.json", {"passed": False, "error": str(exc)[:1000]})
        sys.exit(1)
