"""Business acceptance is versioned independently of execution and artifact publication."""

import json
import re
from contextlib import closing

from .blueprints.sessions import Assets, refs
from .blueprints.source import check_schema, require
from .core.lifecycle import utc_now
from .runtime import canonical, digest


def trusted_measurement(runtime, rule, value):
    """Optional, explicitly trusted local evaluator. No module path comes from the blueprint."""
    from importlib.metadata import entry_points
    from math import isfinite

    from .graph.capabilities import live_tool
    from .runtime import FlowError

    if runtime is None:
        return None
    try:
        grant = live_tool(runtime.workspace_root, rule["capability"], rule["version"], offline=True)
        if (
            grant.get("kind") != "quality_evaluator"
            or grant.get("side_effect") != "none"
            or grant.get("permissions")
        ):
            return None
        matches = [
            e
            for e in entry_points(group="routinekeel.quality_evaluators")
            if e.name == rule["capability"]
            and e.dist.name == grant.get("distribution")
            and e.dist.version == rule["version"]
        ]
        if len(matches) != 1:
            return None
        result = matches[0].load()(value, rule.get("parameters", {}))
        return result if type(result) in (float, int) and isfinite(result) else None
    except (FlowError, KeyError, OSError, ValueError, TypeError, ImportError):
        return None


def step_checks(policy, node_id, outputs):
    rules = policy.get("critical_steps", {}).get(node_id, {}).get("machine", [])
    checks = []
    for rule in rules:
        settings = {key: rule[key] for key in ("expected", "min", "max") if key in rule}
        specification = {
            **settings, "id": f"step/{node_id}/{rule['id']}", "output": rule["output"],
            "evaluator": rule["evaluator"], "version": "1",
            "severity": "warning" if rule.get("tolerate") else "required", "on_failure": "reject",
        }
        (check,) = evaluate({"evaluators": [specification]}, outputs)["checks"]
        if rule.get("tolerate"):
            check["follow_up"] = rule["follow_up"]
        checks.append(check)
    return checks


def recorded_step_checks(nodes):
    checks = []
    for node in nodes.values():
        checks.extend(node.get("output_checks", []))
        for entry in node.get("loop", {}).get("items", []):
            checks.extend(entry.get("output_checks", []))
    return checks


def evaluate(contract, outputs, nodes=None, *, runtime=None, patterns=(), extra_checks=()):
    results = []
    rules = contract.get("evaluators", [])
    require(isinstance(rules, list), "品質評估器清單無效")
    hard = contract.get("invariants", [])
    require(
        isinstance(hard, list) and all(isinstance(v, (str, dict)) for v in hard),
        "不可變規則清單無效",
    )
    hard_ids = {v if isinstance(v, str) else v["id"] for v in hard}
    rules = [*rules, *(v for v in hard if isinstance(v, dict))]
    names = set()
    for rule in rules:
        name = rule["id"]
        require(isinstance(name, str) and name and name not in names, "評估器識別重複")
        names.add(name)
        output = rule["output"]
        value = outputs.get(output)
        kind = rule["evaluator"]
        measured = None
        status = "not_run"
        if rule.get("version") == "1" and output in outputs:
            if kind == "equals":
                require("expected" in rule, "等值規則缺少預期值")
                measured = digest(value)
                status = "pass" if measured == digest(rule["expected"]) else "fail"
            elif kind in {"length", "range"}:
                measured = (
                    (len(value) if isinstance(value, (list, str, dict)) else None)
                    if kind == "length"
                    else value
                )
                low, high = rule.get("min"), rule.get("max")
                require(
                    (low is not None or high is not None)
                    and all(n is None or type(n) in {int, float} for n in (low, high))
                    and (low is None or high is None or low <= high),
                    "品質門檻無效",
                )
                status = (
                    "pass"
                    if type(measured) in (int, float)
                    and (low is None or measured >= low)
                    and (high is None or measured <= high)
                    else "fail"
                )
            elif kind == "json_schema":
                from jsonschema import Draft202012Validator, FormatChecker

                check_schema(rule["schema"])
                matched = Draft202012Validator(
                    rule["schema"], format_checker=FormatChecker()
                ).is_valid(value)
                measured = matched
                status = "pass" if matched else "fail"
        if kind == "capability" and output in outputs:
            measured = trusted_measurement(runtime, rule, value)
            if measured is not None:
                low, high = rule.get("min"), rule.get("max")
                require(low is not None or high is not None, "能力評估器須有明確門檻")
                status = (
                    "pass"
                    if (low is None or measured >= low) and (high is None or measured <= high)
                    else "fail"
                )
        severity = (
            "required"
            if name in contract.get("required_checks", [])
            else rule.get("severity", "required")
        )
        action = rule.get("on_failure", "reject")
        if name in hard_ids:
            severity, action = "required", "reject"
        require(
            severity in {"required", "warning"} and action in {"reject", "review"},
            "品質規則失敗政策無效",
        )
        results.append(
            {
                "id": name,
                "evaluator": kind,
                "version": rule.get("version"),
                "output": output,
                "measured": measured,
                "status": status,
                "severity": severity,
                "on_failure": action,
            }
        )
    for missing in sorted((set(contract.get("required_checks", [])) | hard_ids) - names):
        results.append(
            {"id": missing, "status": "not_run", "severity": "required", "on_failure": "review"}
        )
    for pattern in patterns:
        from routinekeel_contracts.references import parse_reference

        policy = pattern.get("quality_contract", {})
        if policy:
            artifacts = {}
            for name, text in pattern.get("quality_outputs", {}).items():
                ref = parse_reference(text)
                producer = (nodes or {}).get(ref.owner, {})
                values = producer.get("outputs", {})
                if (
                    producer.get("status") == "succeeded"
                    and ref.port in values
                    and digest(values) == producer.get("outputs_hash")
                ):
                    artifacts[name] = values[ref.port]
            evaluated = evaluate(policy, artifacts, nodes, runtime=runtime)
            prefix = "pattern/" + pattern["namespace"] + "/"
            results.extend({**check, "id": prefix + check["id"]} for check in evaluated["checks"])
            review = policy.get("review_policy", {})
            if review.get("required"):
                user_reviewed = (
                    (nodes or {}).get(review.get("approved_node"), {}).get("user_reviewed")
                )
                results.append(
                    {
                        "id": prefix + "human_review",
                        "status": "pass" if user_reviewed else "fail",
                        "severity": "required",
                        "on_failure": "review",
                    }
                )
        gate = pattern.get("gate")
        if not gate:
            continue
        ref = parse_reference(gate["source"])
        node = (nodes or {}).get(ref.owner, {})
        value = node.get("outputs", {})
        status = "not_run"
        measured = None
        if (
            node.get("status") == "succeeded"
            and ref.port in value
            and digest(value) == node.get("outputs_hash")
        ):
            measured = digest(value[ref.port])
            status = "pass" if measured == digest(gate["equals"]) else "fail"
        results.append(
            {
                "id": "pattern/" + pattern["namespace"],
                "evaluator": "pattern_delivery_gate",
                "version": "1",
                "output": gate["source"],
                "status": status,
                "measured": measured,
                "severity": "required",
                "on_failure": "reject",
            }
        )
    if "execution_review" in contract:
        results.extend(recorded_step_checks(nodes or {}))
        results.append({
            "id": "workflow/output_contract", "status": "pass", "severity": "required",
            "on_failure": "reject",
        })
    results.extend(extra_checks)
    required = [r for r in results if r["severity"] == "required"]
    policy = contract.get("review_policy", {})
    gate = policy.get("approved_node")
    reviewed = bool(
        gate
        and nodes
        and nodes.get(gate, {}).get("status") == "succeeded"
        and nodes[gate].get("user_reviewed")
    )
    if any(r["status"] == "not_run" for r in required):
        status = "blocked"
    elif any(r["status"] == "fail" and r["on_failure"] == "reject" for r in required):
        status = "rejected"
    elif any(r["status"] == "fail" for r in required) or policy.get("required") and not reviewed:
        status = "review_required"
    elif required or reviewed:
        status = "accepted"
    else:
        status = "processed"
    return {"result_status": status, "checks": results}


def validate_delivery_outputs(graph, outputs):
    from routinekeel_contracts.graph_validator import port_for

    from .graph.session import checked_value

    require(outputs.keys() == graph.outputs.keys(), "具名交付輸出與契約不同")
    nodes = {node.id: node for node in graph.nodes}
    for name, ref in graph.outputs.items():
        checked_value(outputs[name], port_for(graph, nodes, ref).model_dump())


def corrected_observations(graph, nodes, outputs):
    """Build assessment-only observations while keeping the Run history immutable."""
    from routinekeel_contracts.graph import Join
    from routinekeel_contracts.references import parse_reference

    contract = graph.quality_contract
    policy = contract.get("execution_review")
    patterns = graph.provenance.get("pattern_compositions", [])
    if not policy and not patterns:
        return nodes, []
    steps = {node.id: node for node in graph.nodes}
    updates = {}
    for name, source in graph.outputs.items():
        ref = parse_reference(source)
        while ref.scheme == "artifact":
            values = updates.setdefault(ref.owner, {})
            require(
                ref.port not in values or digest(values[ref.port]) == digest(outputs[name]),
                "同一產物的具名別名須維持相同內容",
            )
            values[ref.port] = outputs[name]
            step = steps[ref.owner]
            if not isinstance(step, Join):
                break
            if step.policy == "selected_branch":
                chosen = nodes[step.switch]["selected_route"]
                ref = parse_reference(step.branches[chosen][ref.port])
            else:
                port = ref.port
                ref = parse_reference(step.inputs[port].source)
                if (
                    step.policy == "all_done" and ref.scheme == "artifact"
                    and nodes[ref.owner]["status"] != "succeeded"
                ):
                    ref = parse_reference(step.fallbacks[port])
    changed = {
        node: values for node, values in updates.items()
        if any(digest(value) != digest(nodes[node]["outputs"][key]) for key, value in values.items())
    }
    observations = dict(nodes)
    for node, edits in changed.items():
        values = {**nodes[node]["outputs"], **edits}
        observations[node] = {**nodes[node], "outputs": values, "outputs_hash": digest(values)}
        if policy:
            observations[node]["output_checks"] = step_checks(policy, node, values)
    reviewed = any(
        data.get("human") for data in (policy or {}).get("critical_steps", {}).values()
    ) or contract.get("review_policy", {}).get("required") or any(
        pattern.get("quality_contract", {}).get("review_policy", {}).get("required")
        for pattern in patterns
    )
    if changed and reviewed:
        return observations, [{
            "id": "workflow/corrected_output_review", "status": "not_run",
            "severity": "required", "on_failure": "review",
        }]
    return observations, []


def assess(runtime, state, outputs):
    graph = state["plan"]["graph"]
    if "execution_review" in graph["quality_contract"]:
        from routinekeel_contracts.graph_validator import validate_blueprint

        design, _ = validate_blueprint(graph)
        validate_delivery_outputs(design, outputs)
    identity = digest(
        [
            state["session_id"],
            state["plan_hash"],
            state["inputs_hash"],
            outputs,
            [(k, v["attempt"]) for k, v in sorted(state["nodes"].items())],
        ]
    )
    with closing(runtime.connect()) as db:
        if not db.execute("SELECT 1 FROM graph_assessments WHERE id=?", (identity,)).fetchone():
            result = evaluate(
                graph["quality_contract"],
                outputs,
                state["nodes"],
                runtime=runtime,
                patterns=graph["provenance"].get("pattern_compositions", []),
            )
            body = {
                "outputs": outputs,
                "original_hash": digest(outputs),
                "source_refs": sorted(set(refs(state["inputs"]))),
                "quality_contract": graph["quality_contract"],
                "input_hash": state["inputs_hash"],
                "assessment_id": identity,
                "session_id": state["session_id"],
                "workflow_id": state["plan"]["workflow"],
                "revision": 0,
                **result,
            }
            db.execute(
                "INSERT OR IGNORE INTO graph_assessments VALUES (?,?,?,?,?,?,?)",
                (
                    identity,
                    state["plan"]["workflow"],
                    state["session_id"],
                    state["plan"]["graph_hash"],
                    digest(outputs),
                    canonical(body),
                    utc_now(),
                ),
            )
            db.commit()
    return identity


def latest(db, identity):
    row = db.execute("SELECT body FROM graph_assessments WHERE id=?", (identity,)).fetchone()
    require(row is not None, "找不到品質評估")
    feedback = db.execute(
        "SELECT body FROM graph_feedback WHERE assessment_id=? ORDER BY revision DESC LIMIT 1",
        (identity,),
    ).fetchone()
    return json.loads(feedback[0])["after"] if feedback else json.loads(row[0])


def preview(runtime, identity):
    with closing(runtime.connect()) as db:
        body = latest(db, identity)
        published = db.execute(
            "SELECT p.run_id FROM graph_published p JOIN deliveries d USING(run_id) WHERE p.assessment_id=? AND p.revision=? AND d.status='published'",
            (identity, body["revision"]),
        ).fetchone()
    assets = Assets(runtime)
    for token in set(refs(body["outputs"])) | set(body.get("source_refs", [])):
        assets.resolve(token)
    return {
        **body,
        "output_hash": digest(body["outputs"]),
        "delivery_status": "published"
        if published
        else "eligible"
        if body["result_status"] == "accepted"
        else "withheld",
        "preview_id": digest([identity, body["revision"], body["outputs"]]),
    }


def feedback(
    runtime,
    identity,
    expected_revision,
    request_id,
    preview_id,
    decision,
    reason,
    user_text,
    *,
    outputs=None,
    tags=(),
    scope="this_run",
):
    require(
        decision in {"accept", "reject", "correct", "withdraw"}
        and isinstance(reason, str)
        and reason.strip()
        and isinstance(user_text, str)
        and user_text.strip(),
        "回饋須有實際決定、原因與對話回覆",
    )
    require(
        isinstance(request_id, str)
        and 0 < len(request_id) <= 128
        and isinstance(tags, (tuple, list))
        and all(isinstance(tag, str) for tag in tags)
        and scope in {"this_run", "workflow", "preference"},
        "回饋識別或作用範圍無效",
    )
    request_hash = digest(
        [
            identity,
            expected_revision,
            preview_id,
            decision,
            reason,
            user_text,
            outputs,
            list(tags),
            scope,
        ]
    )
    with closing(runtime.connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT request_hash,body FROM graph_feedback WHERE assessment_id=? AND request_id=?",
            (identity, request_id),
        ).fetchone()
        if prior:
            require(prior[0] == request_hash, "相同回饋請求識別碼已有不同內容")
            return json.loads(prior[1])
        before = latest(db, identity)
        require(
            before["revision"] == expected_revision
            and preview_id == digest([identity, expected_revision, before["outputs"]]),
            "預覽版本已變更",
        )
        assets = Assets(runtime)
        for token in set(refs(before["outputs"])) | set(before.get("source_refs", [])):
            assets.resolve(token)
        after = dict(before)
        if decision == "withdraw":
            row = db.execute(
                "SELECT body FROM graph_feedback WHERE assessment_id=? AND revision=?",
                (identity, expected_revision),
            ).fetchone()
            require(row is not None, "沒有可撤回的回饋")
            after = json.loads(row[0])["before"]
        elif decision == "correct":
            require(
                isinstance(outputs, dict) and outputs.keys() == before["outputs"].keys(),
                "修正須保留具名交付",
            )
            from routinekeel_contracts.graph_validator import validate_blueprint

            from .graph.session import GraphSession
            from .runtime import read_json

            session = read_json(GraphSession(runtime, before["session_id"]).path)
            graph, _ = validate_blueprint(session["plan"]["graph"])
            validate_delivery_outputs(graph, outputs)
            for token in set(refs(outputs)):
                assets.resolve(token)
            observations, rechecks = corrected_observations(graph, session["nodes"], outputs)
            after.update(
                outputs=outputs,
                **evaluate(
                    before["quality_contract"],
                    outputs,
                    observations,
                    runtime=runtime,
                    patterns=graph.provenance.get("pattern_compositions", []),
                    extra_checks=rechecks,
                ),
            )
        elif decision == "accept":
            require(
                outputs is None
                and not any(
                    c["severity"] == "required"
                    and (
                        c["status"] == "not_run"
                        or c["status"] == "fail"
                        and c["on_failure"] == "reject"
                    )
                    for c in before["checks"]
                ),
                "目前缺少必要評估或違反硬性條件",
            )
            after["result_status"] = "accepted"
        else:
            require(outputs is None, "拒絕請保留原成果；修改成果使用 correct")
            after["result_status"] = "rejected"
        after["revision"] = expected_revision + 1
        event = {
            "feedback_id": "feedback_" + request_hash,
            "assessment_id": identity,
            "decision": decision,
            "reason": reason,
            "scope": scope,
            "tags": list(tags),
            "user_text": user_text,
            "before_hash": digest(before["outputs"]),
            "after_hash": digest(after["outputs"]),
            "before_ref": {
                "assessment_id": identity,
                "revision": expected_revision,
                "sha256": digest(before["outputs"]),
            },
            "after_ref": {
                "assessment_id": identity,
                "revision": expected_revision + 1,
                "sha256": digest(after["outputs"]),
            },
            "before": before,
            "after": after,
        }
        db.execute(
            "INSERT INTO graph_feedback VALUES (?,?,?,?,?,?,?)",
            (
                event["feedback_id"],
                identity,
                after["revision"],
                request_id,
                request_hash,
                canonical(event),
                utc_now(),
            ),
        )
        db.commit()
        return event


def save_profile(
    runtime, graph, identity, expected_revision, settings, scope, user_text, *, request_id=None
):
    from jsonschema import Draft202012Validator

    require(
        isinstance(identity, str)
        and re.fullmatch("[a-z][a-z0-9_-]{0,63}", identity)
        and isinstance(user_text, str)
        and user_text.strip()
        and scope in {"approved", "candidate", "archived"}
        and isinstance(settings, dict),
        "設定檔資料無效",
    )
    permitted = graph["quality_contract"].get("personalization", {}).get("parameters", {})
    require(settings.keys() <= permitted.keys(), "只有藍圖明定的可調參數能寫入設定檔")
    for k, value in settings.items():
        check_schema(permitted[k])
        require(Draft202012Validator(permitted[k]).is_valid(value), "設定超出已核定範圍：" + k)
    body = {
        "settings": settings,
        "scope": scope,
        "user_text": user_text,
        "workflow_id": graph["workflow"]["id"],
        "graph_hash": digest(graph),
        "profile_id": identity,
        "revision": expected_revision + 1,
    }
    if request_id is not None:
        require(isinstance(request_id, str) and request_id.strip(), "設定請求識別無效")
        body["request_id"] = request_id
    with closing(runtime.connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        if request_id is not None:
            prior = db.execute(
                "SELECT content_hash FROM graph_profiles WHERE workflow=? AND profile_id=? AND revision=?",
                (body["workflow_id"], identity, body["revision"]),
            ).fetchone()
            if prior and prior[0] == digest(body):
                return {**body, "content_hash": prior[0]}
        version = (
            db.execute(
                "SELECT MAX(revision) FROM graph_profiles WHERE workflow=? AND profile_id=?",
                (body["workflow_id"], identity),
            ).fetchone()[0]
            or 0
        )
        require(version == expected_revision, "設定版本衝突")
        db.execute(
            "INSERT INTO graph_profiles VALUES (?,?,?,?,?,?)",
            (body["workflow_id"], identity, version + 1, digest(body), canonical(body), utc_now()),
        )
        db.commit()
    return {**body, "content_hash": digest(body)}


def load_profile(runtime, graph, profile_id, revision):
    with closing(runtime.connect()) as db:
        row = db.execute(
            "SELECT body,content_hash FROM graph_profiles WHERE workflow=? AND profile_id=? AND revision=?",
            (graph["workflow"]["id"], profile_id, revision),
        ).fetchone()
        newest = db.execute(
            "SELECT body FROM graph_profiles WHERE workflow=? AND profile_id=? ORDER BY revision DESC LIMIT 1",
            (graph["workflow"]["id"], profile_id),
        ).fetchone()
    require(
        row is not None and newest and json.loads(newest[0])["scope"] != "archived",
        "找不到可使用的設定版本",
    )
    body = json.loads(row[0])
    require(
        body["scope"] == "approved"
        and body["graph_hash"] == digest(graph)
        and digest(body) == row[1],
        "設定尚未核准或圖形版本不同",
    )
    return {**body, "content_hash": row[1]}
