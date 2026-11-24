from ..blueprints.source import require
from ..outputs import reject_links
from ..runtime import digest, read_json
from .proposals import append


def case_content(root, value):
    if isinstance(value, list):
        return [case_content(root, item) for item in value]
    if isinstance(value, dict):
        if value.keys() == {"$file"}:
            from ..blueprints.trial import fixture_path
            from ..runtime import sha256

            return {"$file_sha256": sha256(fixture_path(root, value["$file"]))}
        return {k: case_content(root, v) for k, v in value.items()}
    return value


def case_id(case):
    return digest([case.get("inputs"), case.get("config", {}), case.get("replies", [])])


def unique(cases):
    by_key = {}
    require(isinstance(cases, list), "比較案例須為清單")
    for case in cases:
        require(
            isinstance(case, dict)
            and isinstance(case.get("inputs"), dict)
            and isinstance(case.get("expected"), dict),
            "比較案例需要輸入與獨立預期結果",
        )
        key = case_id(case)
        require(key not in by_key or by_key[key] == case, "同一案例包含不一致的判準")
        by_key[key] = case
    return list(by_key.values())


def replay(service, key, cases):
    from ..runtime import FlowError

    try:
        binding = service.resolve_blueprint_capabilities(key)
        require(binding["status"] == "ready", "比較所需工具有缺口")
        build = service.install_workflow(
            draft_id=key,
            resolution={"capability_fingerprint": binding["fingerprint"]},
            cases=cases,
        )
    except FlowError as exc:
        from ..blueprints.installer import draft_dir

        failed_id = exc.details.get("build_id")
        observations = []
        if failed_id:
            artifact = (
                draft_dir(service.workspace, key) / "builds" / failed_id / "trial-failure.json"
            )
            if artifact.is_file():
                observations = read_json(artifact).get("measurements", [])
        return {
            "passed": False,
            "error": exc.code,
            "reason": exc.details.get("reason", str(exc)),
            "case_count": len(cases),
            "build_id": exc.details.get("build_id"),
            "observed_case_count": len(observations),
            "measurement": "local_trial" if observations else "not_available",
            **{
                field: sum(row[field] for row in observations) if observations else None
                for field in ("review_count", "cost_micro_usd", "model_calls")
            },
        }
    trials = build["evidence"]["cases"]
    return {
        "passed": True,
        "archive": build["archive"],
        "archive_hash": build["archive_sha256"],
        "build_id": build["build_id"],
        "install_hash": build["install_hash"],
        "receipt": build["builder_receipt"],
        "case_count": len(trials),
        "review_count": sum(
            sum(
                w["kind"]
                in {"human", "review", "authorize_external", "iteration_review", "route_review"}
                for w in r["simulated_host_and_review"]
            )
            for r in trials
        ),
        "model_calls": sum(r["usage"]["model_calls"] for r in trials),
        "cost_micro_usd": sum(r["usage"]["cost_micro_usd"] for r in trials),
        "measurement": "local_trial",
    }


def compare(service, folder, state, baseline, params):
    source = service.install_workflow(draft_id=state["candidate_key"])["source"]
    require(
        source["graph"]["provenance"].get("lineage")
        == {"base_source_hash": state["base_source_hash"], "improvement_id": state["proposal_id"]},
        "候選藍圖與改善提案或原版本不符",
    )
    old = unique(baseline["quality_contract"]["golden_cases"])
    new = unique(source["graph"]["quality_contract"]["golden_cases"])
    evidence_ids = {event["feedback_id"] for event in state["evidence"]}
    defect_cases = params["defect_cases"]
    require(
        isinstance(defect_cases, list)
        and all(
            isinstance(case, dict) and case.get("feedback_id") in evidence_ids
            for case in defect_cases
        ),
        "缺陷案例須指向本提案的實際回饋",
    )
    defects = unique(
        [{k: v for k, v in case.items() if k != "feedback_id"} for case in defect_cases]
    )
    require(defects, "請提供對應本次回饋的新缺陷案例")
    selected = service._path(params["holdout_file"], allow_external=False)
    reject_links(selected)
    require(
        selected.is_file()
        and selected.suffix == ".json"
        and selected.stat().st_size <= 2 * 1024**2,
        "未見資料須為本機案例 JSON",
    )
    held = unique(read_json(selected))
    workspace = service.workspace.root
    seen = {digest(case_content(workspace, c["inputs"])) for c in old + new + defects}
    require(
        held and not any(digest(case_content(workspace, c["inputs"])) in seen for c in held),
        "未見案例與已用資料重疊",
    )
    require(
        isinstance(params.get("holdout_origin"), str)
        and params["holdout_origin"].strip()
        and params.get("unseen_by_proposer") is True,
        "請提供獨立資料來源及未提供給提案模型的記錄",
    )
    full = unique(old + new + defects + held)
    corpus_hash = digest(case_content(workspace, full))
    caps = params.get("limits", {})
    require(
        isinstance(caps, dict)
        and set(caps) <= {"review_count", "model_calls", "cost_micro_usd"}
        and all(type(v) is int and v >= 0 for v in caps.values()),
        "比較預算或人工負擔上限無效",
    )
    base_key = state["base_source_hash"]
    prior = {
        group: replay(service, base_key, unique(old + extra))
        for group, extra in (("fixed", []), ("defects", defects), ("holdout", held))
    }
    candidate = replay(service, state["candidate_key"], full)
    require(digest(case_content(workspace, full)) == corpus_hash, "比較期間案例素材已改變")
    report = {
        "base": prior,
        "candidate": candidate,
        "base_hash": state["base_wheel_hash"],
        "holdout_origin": params["holdout_origin"],
        "holdout_hash": digest(case_content(workspace, held)),
        "corpus_hash": corpus_hash,
        "fixed_count": len(old),
        "defect_count": len(defects),
        "holdout_count": len(held),
    }
    report["limits"] = caps
    eligible = (
        prior["fixed"]["passed"]
        and candidate["passed"]
        and all(candidate[k] <= v for k, v in caps.items())
    )
    if eligible:
        install = service.install_workflow(
            draft_id=state["candidate_key"],
            build_id=candidate["build_id"],
            install_hash=candidate["install_hash"],
        )
        require(install["status"] == "update_required", "候選必須對應原本的正式工作流")
        update = service.preview_workflow_update(candidate["archive"], task=state["workflow_id"])
        report.update(
            update_plan=update["plan_hash"],
            from_version=update["from_version"],
            to_version=update["to_version"],
        )
    return append(
        folder,
        {**state, "comparison": report, "comparison_hash": digest(report)},
        "compared" if eligible else "comparison_failed",
    )
