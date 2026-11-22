"""Local, evidence-backed workflow changes."""

from ..blueprints.source import require
from ..runtime import atomic_json, digest, read_json
from ..studio import studio_request
from .comparison import compare
from .promotion import activate, check_promotion
from .proposals import append, current, proposed, root

__all__ = ["change", "check_promotion", "current", "proposed", "root"]


def change(service, identity, expected_revision, action, params):
    runtime = service.workspace.runtime
    folder = root(runtime, identity)
    with runtime.lock("evolution:" + identity, timeout=30):
        state = current(folder)
        request = params.get("request_id")
        require(isinstance(request, str) and request.strip(), "請提供修訂請求識別碼")
        stamp = digest([action, expected_revision, params])
        if state.get("last_request") == request:
            require(state.get("request_hash") == stamp, "提案請求重複但資料不同")
            if state["phase"] not in {"activating", "rolling_back"}:
                return state
        else:
            require(state["revision"] == expected_revision, "提案版本已更新")
        updated = {**state, "last_request": request, "request_hash": stamp}
        task = service.workspace.task(state["workflow_id"], read_only=True)
        from ..graph.session import GraphSession

        baseline = read_json(GraphSession(runtime, state["session_id"]).path)["plan"]["graph"]
        if action == "reject":
            require(
                state["phase"]
                in {
                    "proposed",
                    "prepared",
                    "profile_candidate",
                    "exported",
                    "comparison_failed",
                    "compared",
                }
                and isinstance(params.get("user_text"), str)
                and params["user_text"].strip(),
                "請提供拒絕理由",
            )
            return append(folder, {**updated, "user_decision": params["user_text"]}, "rejected")
        if action == "prepare":
            require(
                state["phase"] in {"proposed", "prepared", "comparison_failed"}
                and task.manifest.sha256 == state["base_wheel_hash"],
                "基準或提案狀態已變更",
            )
            if state["category"] in {"performance", "profile"}:
                from ..quality import save_profile

                settings = params["settings"]
                if state["category"] == "performance":
                    require(
                        set(settings) == {"cache_pure_tasks"},
                        "效能提案只能調整已支援且核定的純計算快取",
                    )
                profile = save_profile(
                    runtime,
                    baseline,
                    params["profile_id"],
                    params["profile_revision"],
                    settings,
                    "candidate",
                    state["hypothesis"],
                    request_id=digest([identity, request]),
                )
                return append(folder, {**updated, "profile": profile}, "profile_candidate")
            from ..graph.source import inspect_graph

            graph, _ = inspect_graph(params["graph"])
            graph["provenance"] = {}
            require(
                graph["workflow"]["id"] == state["workflow_id"]
                and tuple(map(int, graph["workflow"]["version"].split(".")))
                > tuple(map(int, state["base_version"].split(".")))
                and graph["workflow"]["revision"] > baseline["workflow"]["revision"],
                "候選須保留工作流 ID 並增加版本",
            )
            if state["category"] == "logic":
                require(
                    graph["workflow"]["goal"] == baseline["workflow"]["goal"]
                    and all(
                        graph["quality_contract"].get(k) == baseline["quality_contract"].get(k)
                        for k in (
                            "invariants",
                            "success_criteria",
                            "required_checks",
                            "evaluators",
                            "review_policy",
                        )
                    ),
                    "工作目標或硬性判準變更須另列為目標修訂",
                )
            candidate = studio_request(
                service.workspace.root,
                "create",
                {
                    "name": state["workflow_id"] + " 候選修訂",
                    "task": graph["workflow"]["goal"],
                    "request_id": "evolution-" + identity,
                },
            )
            pid = candidate["project_id"]
            project = studio_request(service.workspace.root, "get", {"project_id": pid})
            # A Studio mutation and the local proposal revision live in different stores.
            # Reuse the original expected revision after a crash between their commits.
            ticket = folder / ("design-request-" + digest(request) + ".json")
            if ticket.is_file():
                intent = read_json(ticket)
                require(intent["request_hash"] == stamp, "修訂請求內容已變更")
            else:
                intent = {"expected_revision": project["revision"], "request_hash": stamp}
                atomic_json(ticket, intent)
            proposal = studio_request(
                service.workspace.root,
                "prepare_blueprint",
                {
                    "project_id": pid,
                    "expected_revision": intent["expected_revision"],
                    "request_id": request,
                    "graph": graph,
                    "design": params["design"],
                    "skills": params.get("skills", []),
                    "lineage": {
                        "base_source_hash": state["base_source_hash"],
                        "improvement_id": identity,
                    },
                },
            )
            return append(
                folder,
                {
                    **updated,
                    "studio_project_id": pid,
                    "design_id": proposal["proposal_id"],
                    "design_revision": proposal["revision"],
                    "display_text": proposal["display_text"],
                    "candidate_version": graph["workflow"]["version"],
                },
                "prepared",
            )
        if action == "export":
            require(
                state["phase"] == "prepared" and params["design_id"] == state["design_id"],
                "請先檢視同一候選摘要",
            )
            artifact = studio_request(
                service.workspace.root,
                "export_blueprint",
                {
                    "project_id": state["studio_project_id"],
                    "expected_revision": state["design_revision"],
                    "request_id": request,
                    "proposal_id": state["design_id"],
                    "decision": "approve",
                    "user_text": params["user_text"],
                },
            )
            draft = service.install_workflow(archive=artifact["path"])
            imported = service.install_workflow(draft_id=draft["draft_id"])["source"]["graph"]
            existing = baseline.get("provenance", {}).get("skill_files", {})
            require(
                not existing
                or existing == imported["provenance"].get("skill_files")
                or params.get("confirm_skill_changes") is True,
                "既有技能檔案有變更，請核對後確認",
            )
            return append(
                folder,
                {
                    **updated,
                    "candidate_key": draft["draft_id"],
                    "candidate_source": artifact["sha256"],
                },
                "exported",
            )
        if action == "compare":
            require(
                state["phase"] in {"exported", "comparison_failed", "compared"}
                and task.manifest.sha256 == state["base_wheel_hash"],
                "正式版本或比較來源已變更",
            )
            return compare(service, folder, updated, baseline, params)
        if action == "activate":
            require(
                isinstance(params.get("user_text"), str) and params["user_text"].strip(),
                "請先在對話確認此項變更",
            )
            if state["phase"] == "profile_candidate":
                from ..quality import save_profile

                require(task.manifest.sha256 == state["base_wheel_hash"], "正式版本已變更")

                profile = state["profile"]
                approved = save_profile(
                    runtime,
                    baseline,
                    profile["profile_id"],
                    profile["revision"],
                    profile["settings"],
                    "approved",
                    params["user_text"],
                    request_id=digest([identity, request]),
                )
                return append(
                    folder,
                    {
                        **updated,
                        "profile": approved,
                        "user_decision": params["user_text"],
                    },
                    "profile_approved",
                )
            return activate(service, folder, updated, params)
        if action == "rollback":
            require(
                state["phase"] in {"active", "rolling_back"} and params.get("user_text"),
                "沒有可回復的已核准更新",
            )
            if state["phase"] != "rolling_back":
                updated = append(folder, updated, "rolling_back")
            receipt = service.rollback_workflow_update(state["update_id"])
            return append(
                folder,
                {**updated, "rollback": receipt, "user_decision": params["user_text"]},
                "rolled_back",
            )
        require(False, "未知的修訂操作")
