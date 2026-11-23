"""Run validated pure-data nodes. Checkpoints use the Runtime's existing job namespace."""

import uuid
from datetime import UTC, datetime

from routinekeel_contracts.references import parse_reference

from ..blueprints.sessions import ASSET, Session, refs
from ..blueprints.source import require
from ..runtime import FlowError, atomic_json, digest, read_json
from .capabilities import resolve_graph
from .controls import gate_map, predicate_value, readiness


def checked_value(value, port):
    from math import isfinite

    typ = port["type"]
    valid = {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float) and (not isinstance(value, float) or isfinite(value)),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
        "boolean": type(value) is bool,
        "null": value is None,
        "file": isinstance(value, str) and ASSET.fullmatch(value) is not None,
        "json": True,
    }
    require(valid[typ], "值與資料埠型別不符：" + typ)
    if typ == "array" and port.get("items"):
        for item in value:
            checked_value(item, port["items"])
    return value


class GraphSession(Session):
    @classmethod
    def begin(
        cls,
        runtime,
        plan,
        request,
        directory,
        bindings,
        *,
        key=None,
        offline=False,
        owner_session=None,
        known_assets=(),
    ):
        session = cls(runtime, "job_" + (key or uuid.uuid4().hex))
        with runtime.lock("blueprint:" + session.id):
            if session.path.is_file():
                state = read_json(session.path)
                require(
                    state["plan_hash"] == digest(plan) and state["request_hash"] == digest(request),
                    "工作版本或輸入已變更",
                )
            else:
                require(
                    digest(plan["graph"]) == plan["graph_hash"]
                    and digest(bindings) == plan["bindings_hash"],
                    "Graph 或綁定指紋不符",
                )
                ports = plan["graph"]["inputs"]
                profile = None
                inputs = request["inputs"]
                if request.get("profile_id") is not None:
                    from ..quality import load_profile
                    profile = load_profile(runtime, plan["graph"], request["profile_id"], request.get("profile_revision"))
                    require(isinstance(inputs, dict), "工作輸入須為物件")
                    defaults = {name: value for name, value in profile["settings"].items() if name in ports}
                    inputs = {**defaults, **inputs}
                data = session.assets.ingest(inputs, directory, runtime.workspace_root, known_assets)
                require(isinstance(data, dict) and data.keys() <= ports.keys(), "輸入含有未知埠")
                for name, port in ports.items():
                    require(name in data or not port.get("required", True), "缺少必要輸入：" + name)
                    if name in data:
                        checked_value(data[name], port)
                state = {
                    "session_id": session.id,
                    "owner_session": owner_session,
                    "plan": plan,
                    "plan_hash": digest(plan),
                    "request_hash": digest(request),
                    "offline": offline,
                    "inputs": data,
                    "inputs_hash": digest(data),
                    "config": request.get("config", {}),
                    "bindings": bindings,
                    "nodes": {
                        key: {
                            "instance_id": f"{session.id}/{key}",
                            "status": "pending",
                            "attempt": 0,
                        }
                        for key in plan["order"]
                    },
                    "history": [],
                    "audit": [],
                    "pending": None,
                }
                if profile is not None:
                    require(not (profile["settings"].keys() & state["config"].keys()),"工作設定與指定的設定版本重複")
                    state.update(profile=profile,config={**profile["settings"],**state["config"]})
                atomic_json(session.path, state)
            return session.advance(state)

    @staticmethod
    def value(state, source):
        ref = parse_reference(source)
        if ref.scheme == "input":
            require(ref.owner in state["inputs"], "找不到輸入值：" + source)
            return state["inputs"][ref.owner]
        parent = state["nodes"][ref.owner]
        require(
            parent["status"] == "succeeded" and ref.port in parent["outputs"],
            "依賴產物尚未就緒：" + source,
        )
        require(digest(parent["outputs"]) == parent["outputs_hash"], "依賴產物指紋不同")
        return parent["outputs"][ref.port]

    def checkpoint(self, state, node_id, event, **details):
        state["audit"].append(
            {
                "sequence": len(state["audit"]) + 1,
                "node_id": node_id,
                "event": event,
                "timestamp_utc": datetime.now(UTC).isoformat(),
                **details,
            }
        )
        atomic_json(self.path, state)

    def cancellation(self, state):
        marker = self.folder / "cancel-request.json"
        if not marker.exists() or state.get("cancelled"):
            return bool(state.get("cancelled"))
        request = read_json(marker)
        require(request["plan_hash"] == state["plan_hash"], "取消請求的工作指紋不符")
        if any(w.get("claimed") for w in state.get("pending_tasks", {}).values()):
            return False
        children = {
            w["child_id"] for w in state.get("pending_tasks", {}).values() if w.get("child_id")
        }
        for child_id in children:
            child = GraphSession(self.runtime, child_id)
            view = child.interact("status", {})
            if view["status"] not in {"complete", "cancelled"}:
                child.interact(
                    "cancel", {"revision": view["revision"], "request_id": request["id"]}
                )
        for record in state["nodes"].values():
            if record["status"] not in {"succeeded", "failed", "skipped"}:
                record["status"] = "cancelled"
        state.update(cancelled=True, pending_tasks={})
        self.checkpoint(state, None, "cancel", request_id=request["id"])
        return True

    def request_cancel(self, payload):
        state = read_json(self.path)
        marker = self.folder / "cancel-request.json"
        identity = payload.get("request_id") or digest(payload)
        with self.runtime.lock("graph-cancel:" + self.id):
            if marker.exists():
                require(read_json(marker)["id"] == identity, "已有另一個取消請求")
            else:
                require(payload.get("revision") == digest(state), "工作版本已變更")
                require(self.view(state)["status"] != "complete", "已完成的工作不可取消")
                require(
                    not any(w.get("claimed") for w in state.get("pending_tasks", {}).values()),
                    "請先查核已領取的操作",
                )
                atomic_json(marker, {"id": identity, "plan_hash": state["plan_hash"]})
        try:
            with self.runtime.lock("blueprint:" + self.id, timeout=0):
                state = read_json(self.path)
                self.cancellation(state)
                return self.view(state)
        except FlowError as exc:
            if exc.code != "BUSY":
                raise
            return {**self.view(state), "status": "cancel_requested"}

    def dependencies(self, state):
        deps = state["plan"].get("dependencies")
        if deps is None:
            from routinekeel_contracts.graph_validator import validate_blueprint

            deps = {}
            validate_blueprint(state["plan"]["graph"], dependencies=deps)
        return deps

    def complete_node(self, state, node, outputs, evidence):
        require(outputs.keys() == node["outputs"].keys(), "節點輸出埠不符")
        for name, port in node["outputs"].items():
            checked_value(outputs[name], port)
        for reference in set(refs(outputs)):
            self.assets.resolve(reference)
        record = state["nodes"][node["id"]]
        if evidence.get("kind")=="review" and evidence.get("user_text"):
            record["user_reviewed"]=True
        record.update(status="succeeded", outputs=outputs, outputs_hash=digest(outputs))
        state["history"].append(
            {
                "step": node["id"],
                "attempt": record["attempt"],
                "outputs": outputs,
                "evidence": evidence,
            }
        )
        self.checkpoint(
            state,
            node["id"],
            "succeeded",
            attempt=record["attempt"],
            outputs_hash=record["outputs_hash"],
        )

    def wait_for(self, state, node, kind, values):
        record = state["nodes"][node["id"]]
        identity = digest(
            [
                self.id,
                state["plan_hash"],
                node["id"],
                record["attempt"],
                record["inputs_hash"],
                kind,
            ]
        )
        task = {
            "id": identity,
            "step": node["id"],
            "kind": kind,
            "inputs": values,
            "inputs_hash": record["inputs_hash"],
            "prompt": node.get("prompt"),
            "outputs": node["outputs"],
        }
        if kind == "route_review":
            task["prompt"] = "條件資料不足，請在對話選擇此步可以採用的路徑。"
            task["allowed_routes"] = list(node["routes"])
        if kind in {"authorize_external", "host", "external"}:
            from .external import scope_for

            parameters = node.get("parameters")
            scope, scope_hash = scope_for(
                self.id,
                node["id"],
                record["attempt"],
                state["bindings"][node["id"]],
                values,
                state["config"],
                parameters=parameters,
            )
            task.update(
                scope=scope,
                scope_hash=scope_hash,
                payload_hash=digest([values, parameters]) if parameters else digest(values),
                idempotency_key=digest([self.id, node["id"], record["attempt"], scope_hash]),
                claim_required=kind in {"external", "host"},
                claimed=False,
                prompt="請確認這次操作的工具、資料與範圍。"
                if kind == "authorize_external"
                else "等待宿主工具回執。",
            )
        record["status"] = "waiting"
        state["pending_tasks"][identity] = task
        self.checkpoint(state, node["id"], "waiting", task_id=identity)

    def execute(self, state, node):
        key = node["id"]
        record = state["nodes"][key]
        bindings = node["inputs"]
        fallback_used = []
        if node["kind"] == "join":
            if node["policy"] == "selected_branch":
                chosen = state["nodes"][node["switch"]]["selected_route"]
                bindings = {
                    k: {"source": v, "port": node["outputs"][k]}
                    for k, v in node["branches"][chosen].items()
                }
            elif node["policy"] == "all_done":
                bindings = dict(bindings)
                for k, reference in node.get("fallbacks", {}).items():
                    ref = parse_reference(bindings[k]["source"])
                    if (
                        ref.scheme == "artifact"
                        and state["nodes"][ref.owner]["status"] != "succeeded"
                    ):
                        bindings[k] = {"source": reference, "port": node["outputs"][k]}
                        fallback_used.append(k)
        values = {
            k: checked_value(self.value(state, b["source"]), b["port"]) for k, b in bindings.items()
        }
        tested = (
            self.value(state, node["condition"]["source"]) if node["kind"] == "switch" else None
        )
        loop_node = node["kind"] in {"foreach", "repeat_until"}
        if loop_node:
            tested = self.value(
                state, node["items"] if node["kind"] == "foreach" else node["initial"]["source"]
            )
        record.update(
            status="running",
            attempt=record["attempt"] + (0 if loop_node and "loop" in record else 1),
            inputs_hash=digest([values, tested, node["parameters"]])
            if node.get("parameters")
            else digest([values, tested]),
        )
        self.checkpoint(
            state, key, "running", attempt=record["attempt"], inputs_hash=record["inputs_hash"]
        )
        if loop_node:
            from .iterations import run_loop

            return run_loop(self, state, node)
        if node["kind"] == "human_review":
            self.wait_for(state, node, "human", values)
            return
        if node["kind"] == "switch":
            try:
                selected = (
                    "true"
                    if predicate_value(node["condition"], tested)
                    else ("false" if "false" in node["routes"] else "default")
                )
            except (TypeError, ValueError):
                if node["on_error"] == "review_required":
                    self.wait_for(
                        state,
                        node,
                        "route_review",
                        {"condition_source": node["condition"]["source"]},
                    )
                    return
                require(node["on_error"] == "route", "條件資料錯誤，已停止此路徑")
                selected = "error"
            record.update(
                selected_route=selected,
                condition_source=node["condition"]["source"],
                condition_hash=digest(tested),
            )
            self.complete_node(state, node, {}, {"kind": "route", "selected": selected})
            return
        if node["kind"] == "join":
            parents = self.dependencies(state)[key]
            record["handled_failures"] = (
                [p for p in parents if state["nodes"][p]["status"] == "failed"]
                if node["policy"] == "all_done"
                else []
            )
            self.complete_node(
                state,
                node,
                values,
                {"kind": "join", "policy": node["policy"], "fallback_used": fallback_used},
            )
            return
        tool = node["capability"]
        binding = state["bindings"][key]
        if binding["provider"] == "portable_local":
            from .qualification import execute_local

            outputs = execute_local(binding, node, values, node.get("parameters", {}))
            self.complete_node(state, node, outputs, {
                "kind": "local_execution", "qualification_receipt": binding["qualification_receipt"],
            })
            return
        if binding["provider"] == "desktop_host":
            elevated = (
                binding["side_effect"] == "external"
                or bool(binding.get("permissions"))
                or (binding.get("resources") or {}).get("cost_micro_usd", 0) > 0
            )
            self.wait_for(state, node, "authorize_external" if elevated else "host", values)
            return
        cache_key = None
        if (state.get("profile", {}).get("settings", {}).get("cache_pure_tasks") is True
                and binding["provider"]=="builtin" and node["side_effect"]=="none"):
            cache_key = digest([tool, binding, values, node["outputs"], state["plan_hash"]])
            for previous in state["nodes"].values():
                if previous.get("cache_key")==cache_key and previous["status"]=="succeeded":
                    require(digest(previous["outputs"])==previous["outputs_hash"],"純計算快取指紋不符")
                    self.complete_node(state,node,previous["outputs"],{"kind":"local_cache","source":previous["instance_id"]})
                    return
        value = next(iter(values.values()))
        if tool == "text.upper":
            value = value.upper()
        else:
            require(tool in {"text.copy", "json.identity"}, "尚未實作指定工具")
        output = next(iter(node["outputs"]))
        if cache_key is not None:
            record["cache_key"]=cache_key
        self.complete_node(state, node, {output: value}, {"kind": "local_execution"})

    def advance(self, state):
        graph = state["plan"]["graph"]
        require(
            digest(state["plan"]) == state["plan_hash"]
            and digest(graph) == state["plan"]["graph_hash"],
            "工作版本或 Graph 指紋已變更",
        )
        require(digest(state["bindings"]) == state["plan"]["bindings_hash"], "工作能力指紋已變更")
        if "inputs_hash" not in state:
            state["inputs_hash"] = digest(state["inputs"])
        require(digest(state["inputs"]) == state["inputs_hash"], "工作輸入指紋已變更")
        state.setdefault("pending_tasks", {})
        state.setdefault("requests", {})
        deps = self.dependencies(state)
        if state.get("cancelled"):
            return self.view(state)
        for key, record in state["nodes"].items():
            if record["status"] == "succeeded":
                require(
                    digest(record["outputs"]) == record["outputs_hash"],
                    "已完成的產物指紋不同：" + key,
                )
            if record["status"] == "running":
                record["status"] = "invalidated"
                self.checkpoint(state, key, "recovered_interruption")
        nested_lock = state.get("owner_session") and any(
            "lock_hash" in value for value in state["bindings"].values()
        )
        if not nested_lock and any(r["status"] not in {"succeeded", "skipped"} for r in state["nodes"].values()):
            observed = resolve_graph(
                graph, self.runtime.workspace_root, admission=state["bindings"], allow_waiting=True
            )
            require(
                observed["fingerprint"] == state["plan"]["capability_fingerprint"],
                "能力或權限已變更",
            )
        gates = gate_map(graph)
        for node in graph["nodes"]:
            if self.cancellation(state):
                break
            key = node["id"]
            record = state["nodes"][key]
            if record["status"] not in {"pending", "invalidated", "ready"}:
                continue
            disposition = readiness(node, state, deps[key], gates)
            if disposition == "skip":
                record["status"] = "skipped"
                self.checkpoint(state, key, "skipped", reason="unselected_path")
                continue
            if disposition != "ready":
                continue
            record["status"] = "ready"
            self.checkpoint(state, key, "ready")
            try:
                self.execute(state, node)
            except Exception as exc:  # noqa: BLE001 -- preserve the node failure checkpoint for adapter errors
                record.update(status="failed", error=getattr(exc, "code", type(exc).__name__))
                self.checkpoint(state, key, "failed", error=record["error"])
        result=self.view(state)
        if result["status"]=="complete" and not state.get("owner_session") and not state.get("assessment_id"):
            from ..quality import assess
            state["assessment_id"]=assess(self.runtime,state,result["outputs"])
            self.checkpoint(state,None,"quality_assessed",assessment_id=state["assessment_id"])
            result=self.view(state)
        return result

    def view(self, state):
        nodes = state["nodes"]
        waits = list(state.get("pending_tasks", {}).values())
        handled = {
            key
            for n in nodes.values()
            if n["status"] == "succeeded"
            for key in n.get("handled_failures", [])
        }
        done = all(
            n["status"] in {"succeeded", "skipped"} or k in handled for k, n in nodes.items()
        )
        status = "running"
        if state.get("cancelled"):
            status = "cancelled"
        elif any(n["status"] == "failed" and k not in handled for k, n in nodes.items()):
            status = "failed"
        elif waits:
            status = "waiting"
        elif done:
            status = "complete"
        elif any(n["status"] == "failed" for n in nodes.values()):
            status = "failed"
        result = {
            "session_id": self.id,
            "workflow_id": state["plan"]["workflow"],
            "version": state["plan"]["version"],
            "status": status,
            "completed_steps": sum(n["status"] == "succeeded" for n in nodes.values()),
            "pending": waits[0] if waits else None,
            "pending_tasks": waits,
            "revision": digest(state),
            "nodes": nodes,
        }
        if any(p.get("claimed") for p in waits):
            result.update(code="RECONCILE_REQUIRED", next_action="reconcile")
        if status == "complete":
            result["outputs"] = {
                k: self.value(state, ref) for k, ref in state["plan"]["final_outputs"].items()
            }
            if state.get("assessment_id") and not state.get("owner_session"):
                from ..quality import preview
                quality=preview(self.runtime,state["assessment_id"])
                result.update(outputs=quality["outputs"],assessment_id=quality["assessment_id"],
                              execution_status="succeeded",result_status=quality["result_status"],
                              quality_revision=quality["revision"],preview_id=quality["preview_id"],
                              delivery_status=quality["delivery_status"])
        return result

    def invalidate(self, state, key, *, retry_iteration=False):
        state.pop("assessment_id",None)
        target = key
        deps = self.dependencies(state)
        require(key in deps, "找不到要修訂的節點")
        affected = {key}
        for child in state["plan"]["order"]:
            if affected.intersection(deps[child]):
                affected.add(child)
        require(
            not any(
                w["step"] in affected and w.get("claimed") for w in state["pending_tasks"].values()
            ),
            "對外操作結果待查核，不可直接回修",
        )
        for node in state["plan"]["graph"]["nodes"]:
            if node["id"] in affected and node.get("side_effect") == "external":
                require(
                    state["nodes"][node["id"]]["status"] not in {"running", "succeeded"},
                    "對外操作不可自動重送",
                )
        for node_id in sorted(affected):
            previous = state["nodes"][node_id]
            if retry_iteration and node_id == target and previous.get("loop"):
                require(previous.get("error") == "CHILD_FAILED", "請重新檢視迭代上限或已停止的工作")
                previous.update(status="ready", retry_child=True)
                continue
            require(
                not previous.get("loop", {}).get("external_claim"), "此迭代已有對外記錄，不能倒帶"
            )
            require(
                not previous.get("external_claim")
                or previous.get("error") == "EXTERNAL_NOT_EXECUTED",
                "已領取的外部操作須先有未執行的獨立證據才可回修",
            )
            state["nodes"][node_id] = {
                "instance_id": f"{self.id}/{node_id}",
                "status": "invalidated",
                "attempt": state["nodes"][node_id]["attempt"],
            }
        state["pending_tasks"] = {
            i: w for i, w in state["pending_tasks"].items() if w["step"] not in affected
        }
        return sorted(affected)

    def interact(self, operation, payload, *, workflow=None, simulation=False):
        if operation == "status":
            return self.view(read_json(self.path))
        if operation == "cancel":
            return self.request_cancel(payload)
        with self.runtime.lock("blueprint:" + self.id):
            state = read_json(self.path)
            require(workflow is None or workflow == state["plan"]["workflow"], "工作流識別不符")
            if operation == "next":
                if any(w.get("claimed") for w in state.get("pending_tasks", {}).values()):
                    return self.view(state)
                return self.advance(state)
            state.setdefault("requests", {})
            state.setdefault("pending_tasks", {})
            request_id = payload.get("request_id") or digest([operation, payload])
            stamp = digest([operation, payload])
            if request_id in state["requests"]:
                require(state["requests"][request_id] == stamp, "請求識別碼已用於不同操作")
                return self.view(state)
            require(payload.get("revision") == digest(state), "工作版本已更新，請重讀待辦")
            require(not state.get("cancelled"), "工作已取消")
            pending = state["pending_tasks"].get(payload.get("task_id"), {})
            if pending.get("child_id") and operation not in {"cancel", "revise", "retry"}:
                from .iterations import forward_reply

                return forward_reply(
                    self, state, pending, operation, payload, request_id, stamp, simulation
                )
            if operation in {"revise", "retry"}:
                key = payload.get("step")
                if operation == "retry":
                    require(
                        state["nodes"].get(key, {}).get("status") == "failed", "只能重試失敗的節點"
                    )
                require(bool(payload.get("reason")), "請提供回修理由")
                changed = self.invalidate(state, key, retry_iteration=operation == "retry")
            elif operation in {"authorize", "decline", "claim", "reconcile", "stream_delta"} or (
                operation == "complete"
                and state["pending_tasks"].get(payload.get("task_id"), {}).get("kind")
                in {"host", "external"}
            ):
                return self.external_action(
                    state, operation, payload, request_id, stamp, simulation
                )
            elif operation == "complete":
                task = state["pending_tasks"].get(payload.get("task_id"))
                require(task is not None, "待辦不存在或工作版本已變更")
                node = next(n for n in state["plan"]["graph"]["nodes"] if n["id"] == task["step"])
                evidence = payload.get("evidence") or {}
                require(
                    task["kind"] in {"human", "route_review", "iteration_review"}
                    and isinstance(evidence.get("user_text"), str)
                    and evidence["user_text"].strip(),
                    "請轉交桌面對話中對此待辦的實際回覆",
                )
                outputs = payload.get("outputs", {})
                if task["kind"] == "iteration_review":
                    require(
                        evidence.get("decision") == "accept_manual_result",
                        "請明確確認修正後的交付；達到上限不能自動通過",
                    )
                    if (
                        node["kind"] == "repeat_until"
                        and node["condition"]["source"] == node["body"]["outputs"]["value"]
                    ):
                        require(
                            "value" in outputs
                            and predicate_value(node["condition"], outputs["value"]),
                            "修正後的狀態仍未通過品質條件",
                        )
                if task["kind"] == "route_review":
                    selected = payload.get("route")
                    require(selected in node["routes"], "請指定已宣告的路徑")
                    state["nodes"][node["id"]].update(
                        selected_route=selected,
                        route_reason="user_reply",
                        condition_source=node["condition"]["source"],
                    )
                require(
                    isinstance(outputs, dict) and outputs.keys() == node["outputs"].keys(),
                    "回覆的輸出欄位不符",
                )
                for k, port in node["outputs"].items():
                    checked_value(outputs[k], port)
                state["requests"][request_id] = stamp
                del state["pending_tasks"][task["id"]]
                self.complete_node(
                    state, node, outputs, {"kind": "review", "user_text": evidence["user_text"]}
                )
                return self.advance(state)
            else:
                require(False, "不支援的 Graph 工作操作")
            state["requests"][request_id] = stamp
            self.checkpoint(state, payload.get("step"), operation, affected=changed)
            return self.advance(state)

    def external_action(self, state, operation, payload, request_id, stamp, simulation):
        from .external import read_observation

        task = state["pending_tasks"].get(payload.get("task_id"))
        require(task is not None, "待辦不存在或已完成")
        key = task["step"]
        node = next(n for n in state["plan"]["graph"]["nodes"] if n["id"] == key)
        record = state["nodes"][key]
        if operation in {"authorize", "decline"}:
            allowed_kind = task["kind"] == "authorize_external" or (
                operation == "decline"
                and task["kind"] in {"human", "route_review", "iteration_review"}
            )
            require(
                allowed_kind
                and isinstance(payload.get("user_text"), str)
                and payload["user_text"].strip(),
                "需要這項操作實際的對話回覆",
            )
            if "scope_hash" in task:
                require(
                    payload.get("scope_hash") == task["scope_hash"]
                    and payload.get("payload_hash") == task["payload_hash"],
                    "核准資料或範圍不符",
                )
            record["decision"] = {
                "origin": "desktop_conversation",
                "task_id": task["id"],
                "scope": task.get("scope"),
                "scope_hash": task.get("scope_hash"),
                "payload_hash": task.get("payload_hash", task["inputs_hash"]),
                "revision": payload["revision"],
                "user_text": payload["user_text"],
                "decision": operation,
                "timestamp_utc": datetime.now(UTC).isoformat(),
            }
            del state["pending_tasks"][task["id"]]
            state["requests"][request_id] = stamp
            if operation == "decline":
                record.update(status="failed", error="REVIEW_DECLINED")
                self.checkpoint(state, key, "declined")
            else:
                self.wait_for(state, node, "external", task["inputs"])
            return self.view(state)
        require(task["kind"] in {"host", "external"}, "此待辦不接受工具回執")
        if operation == "claim":
            require(not (self.folder / "cancel-request.json").exists(), "工作已要求取消")
            if task.get("claimed"):
                return self.view(state)
            require(
                simulation
                or not (state.get("offline") and state["bindings"][key].get("network", True)),
                "離線工作不能領取網路工具操作",
            )
            authority = state
            owners = set()
            while authority.get("owner_session"):
                parent = authority["owner_session"]
                require(parent not in owners, "子工作的綁定鏈包含循環")
                owners.add(parent)
                authority = read_json(GraphSession(self.runtime, parent).path)
                require(
                    digest(authority["plan"]) == authority["plan_hash"]
                    and digest(authority["bindings"]) == authority["plan"]["bindings_hash"],
                    "父工作的綁定或執行計畫已變更",
                )
            live = resolve_graph(
                authority["plan"]["graph"], self.runtime.workspace_root,
                admission=authority["bindings"],
            )
            require(
                live["fingerprint"] == authority["plan"]["capability_fingerprint"], "工具或權限已變更"
            )
            require(
                task["kind"] != "external"
                or record.get("decision", {}).get("scope_hash") == task["scope_hash"],
                "缺少本次操作核准",
            )
            task["claimed"] = True
            if task["kind"] == "external":
                record["external_claim"] = task["idempotency_key"]
            state["requests"][request_id] = stamp
            self.checkpoint(state, key, "claimed", idempotency_key=task["idempotency_key"])
            return {
                **self.view(state),
                "dispatch": {
                    "tool": task["scope"]["tool"],
                    "arguments": task["inputs"],
                    **(
                        {"parameters": task["scope"]["parameters"]}
                        if "parameters" in task["scope"]
                        else {}
                    ),
                    "idempotency_key": task["idempotency_key"],
                    "scope_hash": task["scope_hash"],
                },
            }
        require(task.get("claimed"), "尚未領取此操作")
        if operation == "stream_delta":
            seq = task.get("delta_count", 0)
            text = payload.get("text")
            require(
                (state["bindings"][key].get("resources") or {}).get("model_calls", 0) > 0
                and type(payload.get("sequence")) is int
                and payload["sequence"] == seq
                and isinstance(text, str)
                and 0 < len(text) <= 65536,
                "串流模型、序號或文字無效",
            )
            task["stream_hash"] = digest([task.get("stream_hash"), seq, text])
            task["delta_count"] = seq + 1
            state["requests"][request_id] = stamp
            self.checkpoint(
                state, key, "model_delta", sequence=seq, stream_hash=task["stream_hash"]
            )
            return {**self.view(state), "event": {"type": "delta", "sequence": seq, "text": text}}
        receipt = read_observation(
            self.runtime.workspace_root, task["idempotency_key"], task["scope_hash"]
        )
        if receipt is None:
            return self.view(state)
        if receipt["outcome"] == "succeeded":
            outputs = receipt["outputs"]
            resource = state["bindings"][key].get("resources") or {}
            if resource.get("model_calls", 0) > 0:
                require(
                    task.get("delta_count", 0) > 0
                    and receipt.get("stream")
                    == {
                        "completed": True,
                        "count": task["delta_count"],
                        "hash": task["stream_hash"],
                    },
                    "模型串流尚未完整，暫不接受產物",
                )
            if resource.get("model_calls", 0) > 0 or resource.get("cost_micro_usd", 0) > 0:
                usage = receipt.get("usage") or {}
                require(
                    all(
                        type(usage.get(f)) is int and usage[f] >= 0 and type(resource.get(f)) is int
                        for f in ("model_calls", "cost_micro_usd")
                    ),
                    "用量缺失或超過已授權上限",
                )
                record["usage"] = usage
                if any(usage[f] > resource[f] for f in ("model_calls", "cost_micro_usd")):
                    record.update(
                        status="failed", error="RESOURCE_LIMIT", receipt=receipt["provider_receipt"]
                    )
                    del state["pending_tasks"][task["id"]]
                    state["requests"][request_id] = stamp
                    self.checkpoint(state, key, "resource_overrun", usage=usage)
                    return self.view(state)
            state["requests"][request_id] = stamp
            del state["pending_tasks"][task["id"]]
            self.complete_node(
                state,
                node,
                outputs,
                {"kind": "host_receipt", "receipt": receipt["provider_receipt"]},
            )
        else:
            del state["pending_tasks"][task["id"]]
            record.update(
                status="failed",
                error="EXTERNAL_" + receipt["outcome"].upper(),
                receipt=receipt["provider_receipt"],
            )
            state["requests"][request_id] = stamp
            self.checkpoint(state, key, "reconciled", outcome=receipt["outcome"])
        return self.advance(state)
