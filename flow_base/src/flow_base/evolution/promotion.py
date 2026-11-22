import io
import json
import zipfile
from contextlib import closing

from ..blueprints.installer import checked_candidate, draft_dir
from ..blueprints.source import require
from ..outputs import reject_links
from ..runtime import atomic_json, digest, read_json
from .proposals import append, current, require_active_feedback, root


def activate(service, folder, state, params):
    from ..package_updates import update_root

    require(
        state["phase"] in {"compared", "activating"}
        and params.get("comparison_hash") == state["comparison_hash"],
        "需要同一版本通過的比較與本次核准",
    )
    candidate = state["comparison"]["candidate"]
    task = service.workspace.task(state["workflow_id"], read_only=True)
    if state["phase"] != "activating":
        require(task.manifest.sha256 == state["base_wheel_hash"], "正式版本已變更")
        state = append(folder, {**state, "user_decision": params["user_text"]}, "activating")
    atomic_json(
        grant_path(service.workspace, candidate["archive_hash"]),
        {"proposal_id": state["proposal_id"]},
    )
    receipt = (
        service.update_workflow(
            candidate["archive"],
            plan_hash=state["comparison"]["update_plan"],
            task=state["workflow_id"],
        )
        if task.manifest.sha256 == state["base_wheel_hash"]
        else None
    )
    if not receipt or not receipt.get("update_id"):
        for path in update_root(service.workspace).glob("update_*/record.json"):
            record = read_json(path)
            if (
                record.get("status") == "applied"
                and record["plan_hash"] == state["comparison"]["update_plan"]
                and task.manifest.version == state["candidate_version"]
            ):
                receipt = record
                break
    require(receipt and receipt.get("update_id"), "更新狀態待查核")
    return append(folder, {**state, "update_id": receipt["update_id"]}, "active")


def grant_path(workspace, archive_hash):
    path = workspace.runtime.home / "graph_upgrade_grants" / (archive_hash + ".json")
    reject_links(path)
    return path


def check_promotion(workspace, plan, files):
    live = workspace.task(plan["workflow_id"], read_only=True)
    installed_ir = live.folder / "workflow.ir.json"

    def is_graph(body):
        return isinstance(body, dict) and body.get("blueprint_schema_version") == 2

    def graph_plan(wheel):
        with zipfile.ZipFile(wheel) as archive:
            name = live.manifest.python_module + "/plan.json"
            require(name in archive.namelist(), "藍圖工作流缺少執行計畫")
            item = archive.getinfo(name)
            from ..package_updates import MAX_ZIP

            require(item.file_size <= MAX_ZIP, "執行計畫過大")
            return json.loads(archive.read(item)).get("format") == 2

    if not (
        installed_ir.is_file()
        and is_graph(read_json(installed_ir))
        or "workflow.ir.json" in files
        and is_graph(json.loads(files["workflow.ir.json"]))
        or live.manifest.python_module.startswith("rk_bp_")
        and (
            graph_plan(live.folder / live.manifest.package)
            or graph_plan(io.BytesIO(files[json.loads(files["flow.json"])["package"]]))
        )
    ):
        return
    pointer = grant_path(workspace, plan["archive_sha256"])
    require(pointer.is_file(), "Graph 更新需要先比較候選版本並在對話確認")
    state = current(root(workspace.runtime, read_json(pointer)["proposal_id"]))
    report = state["comparison"]
    candidate = report["candidate"]
    require(
        state["phase"] == "activating"
        and state.get("user_decision")
        and digest(report) == state["comparison_hash"]
        and report["update_plan"] == plan["plan_hash"]
        and candidate["archive_hash"] == plan["archive_sha256"]
        and candidate["passed"]
        and live.manifest.sha256 == state["base_wheel_hash"]
        and live.manifest.id == state["workflow_id"],
        "更新核准與候選或正式版不符",
    )
    with closing(workspace.runtime.connect()) as db:
        for evidence in state["evidence"]:
            row = db.execute(
                "SELECT body FROM graph_feedback WHERE id=?", (evidence["feedback_id"],)
            ).fetchone()
            require(row, "找不到原始回饋")
            require_active_feedback(db, json.loads(row[0]))
    folder = draft_dir(workspace, state["candidate_key"]) / "builds" / candidate["build_id"]
    reject_links(folder)
    record, *_ = checked_candidate(workspace, folder, candidate["install_hash"])
    require(record["archive_sha256"] == plan["archive_sha256"], "更新附件與已驗證成品不符")
