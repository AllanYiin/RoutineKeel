"""Durable, version-pinned host tasks. One atomic state file per conversation-independent job."""

import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..outputs import reject_links
from ..runtime import atomic_copy, atomic_json, digest, read_json, sha256
from .recipe import local_operations, transform
from .source import require, validate_value

ASSET = re.compile(r"asset:([a-f0-9]{64})(\.[a-z0-9]{1,8})")


class Assets:
    def __init__(self, runtime):
        self.root = runtime.home / "blueprint_files"
        reject_links(self.root)
        self.root.mkdir(exist_ok=True, parents=True)

    def store(self, path):
        require(path.is_file() and path.stat().st_size <= 512 * 1024**2, "素材不存在或過大")
        token = "asset:" + sha256(path) + path.suffix.lower()
        dest = self.path(token)
        if not dest.exists():
            atomic_copy(path, dest)
        self.resolve(token)
        return token

    def path(self, token):
        m = ASSET.fullmatch(token)
        require(m, "不支援的素材名稱")
        path = self.root / (m[1] + m[2])
        reject_links(path)
        return path

    def resolve(self, token):
        path = self.path(token)
        require(path.is_file() and sha256(path) == token[6:70], "素材遺失或指紋不符")
        return path

    def ingest(self, data, directory, workspace, allowed=()):
        if isinstance(data, dict):
            if set(data) == {"$file"}:
                name = Path(data["$file"])
                require(
                    not name.is_absolute() and not name.drive and ".." not in name.parts,
                    "素材必須是相對路徑",
                )
                path = directory / name
                reject_links(path)
                rel = path.resolve().relative_to(workspace)
                require(
                    rel.parts
                    and rel.parts[0] not in {"flow_base", "share_states", "share_logs"}
                    and not any(p.startswith(".") for p in rel.parts),
                    "素材超出輸入範圍",
                )
                return self.store(path)
            return {k: self.ingest(v, directory, workspace, allowed) for k, v in data.items()}
        if isinstance(data, list):
            return [self.ingest(v, directory, workspace, allowed) for v in data]
        if isinstance(data, str) and data.startswith("asset:"):
            require(data in allowed, "請用 $file 引用已授權素材")
            self.resolve(data)
        return data


def refs(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)
    elif isinstance(value, str) and value.startswith("asset:"):
        yield value


def select_value(data, selector):
    for part in selector.split("/"):
        require(isinstance(data, dict) and part in data, "宿主參數映射引用未知欄位")
        data = data[part]
    return data


def check_boundary(binding, side, value):
    schema = binding.get(side + "_schema")
    if schema is not None:
        validate_value({side: {"schema": schema}}, side, value)


def asset_fields(value, fields):
    if not fields:
        require(isinstance(value, str) and ASSET.fullmatch(value), "需要實際素材檔案")
    elif fields[0] == "*":
        require(isinstance(value, list), "需要素材清單")
        for item in value:
            asset_fields(item, fields[1:])
    else:
        require(isinstance(value, dict) and fields[0] in value, "缺少素材欄位")
        asset_fields(value[fields[0]], fields[1:])


class Session:
    def __init__(self, runtime, session_id):
        require(
            isinstance(session_id, str) and re.fullmatch(r"job_[a-f0-9]{32}", session_id),
            "工作編號無效",
        )
        self.runtime = runtime
        self.folder = runtime.home / "blueprint_jobs" / session_id
        self.path = self.folder / "state.json"
        reject_links(self.path)
        self.assets = Assets(runtime)
        self.id = session_id

    @classmethod
    def begin(cls, runtime, plan, request, directory, bindings, *, key=None):
        session = cls(runtime, "job_" + (key or uuid.uuid4().hex))
        with runtime.lock("blueprint:" + session.id):
            if not session.path.exists():
                data = session.assets.ingest(request["inputs"], directory, runtime.workspace_root)
                config = session.assets.ingest(
                    request.get("config", {}), directory, runtime.workspace_root
                )
                state = {
                    "session_id": session.id,
                    "plan": plan,
                    "bindings": bindings,
                    "inputs": data,
                    "config": config,
                    "history": [],
                    "audit": [],
                    "pending": None,
                }
                profile_id = request.get("profile_id")
                if profile_id is not None:
                    first = plan["steps"][0]
                    require(
                        first["reuse_approved"] and re.fullmatch("[a-f0-9]{64}", profile_id),
                        "角色設定編號無效",
                    )
                    profile = runtime.home / "blueprint_profiles" / (profile_id + ".json")
                    reject_links(profile)
                    require(profile.is_file(), "角色設定已撤回或不在此工作區")
                    saved = read_json(profile)
                    for name, value in saved["inputs"].items():
                        require(
                            name not in data or data[name] == value,
                            "角色來源已變更，請重新送審",
                        )
                        data[name] = value
                    require(
                        session.profile_key(state, first) == profile_id,
                        "角色設定不適用於這個版本或設定",
                    )
                require(set(plan["inputs"]) == set(data), "輸入名稱不符")
                for k, v in data.items():
                    validate_value(plan["contracts"], k, v)
                atomic_json(session.path, state)
            return session.advance(read_json(session.path))

    def scope(self, s, node):
        values = dict(s["inputs"])
        for entry in s["history"]:
            values.update(entry["outputs"])
        return {name: values[name] for name in node["inputs"]}

    def profile_key(self, s, node):
        keys = node["profile_config"]
        config = s["config"] if keys is None else {k: s["config"][k] for k in keys}
        return digest([s["plan"], self.scope(s, node), config, s["bindings"]])

    def profile_path(self, s, node):
        path = self.runtime.home / "blueprint_profiles" / (self.profile_key(s, node) + ".json")
        reject_links(path)
        return path

    def wait(self, s, node, kind, values, **extra):
        s["pending"] = {
            "id": uuid.uuid4().hex,
            "step": node["id"],
            "title": node["title"],
            "kind": kind,
            "inputs": values,
            "input_hash": digest([values, s["config"]]),
            "skills": node["skills"],
            **extra,
        }
        self.bind_review_scope(s, node)
        atomic_json(self.path, s)
        return self.view(s)

    def bind_review_scope(self, s, node):
        from ..graph.external import scope_for

        p = s["pending"]
        target = node
        binding = p.get("binding") or {"tool": "human_review", "version": "1"}
        arguments = p.get("arguments", p.get("candidate", {}).get("outputs", p["inputs"]))
        if p["kind"] == "review":
            index = len(s["history"]) + 1
            if index < len(s["plan"]["steps"]) and s["plan"]["steps"][index]["mode"] == "external":
                target = s["plan"]["steps"][index]
                binding = s["bindings"][target["capability"]]
                values = self.scope({**s, "history": [*s["history"], p["candidate"]]}, target)
                data = {**values, "inputs": values, "config": s["config"]}
                arguments = {
                    dest: select_value(data, src) for src, dest in binding["mapping"].items()
                }
        if binding.get("kind") == "host_probe":
            from ..graph.capabilities import live_tool

            observed = live_tool(self.runtime.workspace_root, binding["tool"], binding["version"])
            binding = {
                **binding,
                "permissions": observed.get("permissions", []),
                "resources": observed.get("resources"),
                "authority_hash":digest(observed),
                "destination":observed.get("destination",binding["tool"]),
            }
        scope, stamp = scope_for(self.id, target["id"], 1, binding, arguments, s["config"])
        p.update(scope=scope, scope_hash=stamp, payload_hash=digest(arguments))
        if p["kind"] == "external":
            p["idempotency_key"] = digest([self.id, node["id"], stamp, p["id"]])
            p["authorization_required"] = not any(
                r.get("approval", {}).get("scope_hash") == stamp
                and r["approval"].get("session_id") == self.id
                for r in s["history"]
            )

    def approval(self, s, evidence, inputs, outputs):
        return {
            **evidence,
            "config_hash": digest(s["config"]),
            "session_id": self.id,
            "task_id": s["pending"]["id"],
            "scope_hash": s["pending"]["scope_hash"],
            "payload_hash": s["pending"]["payload_hash"],
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "covered": {k: digest(v) for k, v in {**inputs, **outputs}.items()},
        }

    def advance(self, s):
        plan = s["plan"]
        while not s["pending"] and len(s["history"]) < len(plan["steps"]):
            node = plan["steps"][len(s["history"])]
            values = self.scope(s, node)
            if node["reuse_approved"]:
                profile = self.profile_path(s, node)
                if profile.is_file():
                    record = read_json(profile)["record"]
                    for token in refs(record["outputs"]):
                        self.assets.resolve(token)
                    s["history"].append(record)
                    atomic_json(self.path, s)
                    continue
            if node["mode"] in {"host", "external", "human"}:
                binding = s["bindings"].get(node["capability"])
                if binding and binding.get("kind") == "host_probe":
                    from ..graph.capabilities import validate_bindings

                    validate_bindings(self.runtime.workspace_root, {node["capability"]: binding})
                arguments = {}
                if binding:
                    scope = {**values, "inputs": values, "config": s["config"]}
                    arguments = {
                        dest: select_value(scope, src) for src, dest in binding["mapping"].items()
                    }
                    check_boundary(binding, "input", arguments)
                if node["mode"] == "external":
                    approved = next(
                        (r["approval"] for r in reversed(s["history"]) if r.get("approval")),
                        {},
                    )
                    require(
                        approved.get("config_hash") == digest(s["config"])
                        and all(approved["covered"].get(k) == digest(v) for k, v in values.items()),
                        "對外操作的所有輸入與設定均須經最近一次人審確認",
                    )
                return self.wait(
                    s,
                    node,
                    node["mode"],
                    values,
                    binding=binding,
                    arguments=arguments,
                    claim_required=node["mode"] == "external"
                    or bool(binding and binding.get("claim_required")),
                )
            self.process(s, node, values, {}, {})
        return self.view(s)

    def process(self, s, node, values, answer, evidence):
        context = {
            "inputs": values,
            "config": s["config"],
            "answer": answer,
            "result": answer,
        }
        allowed = set(refs(context))
        context["texts"] = {}
        for key, fields in node["text_inputs"].items():
            token = values
            for field in fields:
                token = token[field]
            path = self.assets.resolve(token)
            require(
                token in allowed
                and path.suffix in {".txt", ".md", ".json", ".srt"}
                and path.stat().st_size <= 256 * 1024,
                "文字素材格式或大小不符",
            )
            context["texts"][key] = path.read_text(encoding="utf-8-sig")
        if node["operation"]:
            ep = local_operations().get(node["operation"])
            require(
                ep and ep.dist.version == node["operation_version"],
                "本機元件版本已變更",
            )

            def resolve(token):
                require(token in allowed, "元件不可讀取未宣告素材")
                return self.assets.resolve(token)

            param = transform(node["arguments"], context) if node["arguments"] else values
            with tempfile.TemporaryDirectory(dir=self.folder) as tmp:
                context["result"] = ep.load()(param, Path(tmp), resolve, self.assets.store)
            allowed.update(refs(context["result"]))
        outputs = transform(node["result"], context)
        require(
            isinstance(outputs, dict) and set(outputs) == set(node["outputs"]),
            "步驟輸出名稱不符",
        )
        for name, value in outputs.items():
            validate_value(s["plan"]["contracts"], name, value)
        for fields in node["asset_fields"]:
            asset_fields(outputs, fields)
        if node["assertion"]:
            require(
                transform(node["assertion"], {**context, "outputs": outputs}) is True,
                "步驟完成條件不符",
            )
        for token in refs(outputs):
            require(token in allowed, "產物引用未知素材")
            self.assets.resolve(token)
        record = {"step": node["id"], "outputs": outputs, "evidence": evidence}
        if node["mode"] == "human":
            record["approval"] = self.approval(s, evidence, values, outputs)
        if node["review"]:
            self.wait(
                s,
                node,
                "review",
                values,
                candidate=record,
                shows=node["review"]["shows"],
            )
        else:
            s["history"].append(record)
            s["pending"] = None
            atomic_json(self.path, s)

    def view(self, s):
        done = len(s["history"]) == len(s["plan"]["steps"]) and not s["pending"]
        result = {
            "session_id": self.id,
            "workflow_id": s["plan"]["workflow"],
            "version": s["plan"]["version"],
            "status": "complete" if done else "waiting",
            "completed_steps": len(s["history"]),
            "pending": s["pending"],
            "revision": digest(s),
        }
        if s["history"] and s["plan"]["steps"][0]["reuse_approved"]:
            result["profile_id"] = self.profile_key(s, s["plan"]["steps"][0])
        if s["pending"]:
            result["config"] = s["config"]
            node = s["plan"]["steps"][len(s["history"])]
            result["next_action"] = (
                "reconcile_receipt"
                if s["pending"].get("claimed")
                else "claim"
                if s["pending"].get("claim_required")
                else "complete"
            )
            result["skill_documents"] = node.get("skill_text", {})
            result["blueprint_step"] = node["blueprint_step"]
            result["output_contracts"] = {k: s["plan"]["contracts"][k] for k in node["outputs"]}
            if s["pending"]["kind"] in {"review", "human"}:
                result["prior_evidence"] = [r["evidence"] for r in s["history"]]
            result["assets"] = {
                t: str(self.assets.resolve(t)) for t in set(refs([s["pending"], s["config"]]))
            }
        if done:
            values = {k: v for row in s["history"] for k, v in row["outputs"].items()}
            result["outputs"] = {k: values[k] for k in s["plan"]["final_outputs"]}
        return result

    def interact(self, operation, payload, *, workflow=None):
        with self.runtime.lock("blueprint:" + self.id):
            require(self.path.is_file(), "找不到藍圖工作")
            s = read_json(self.path)
            require(workflow is None or workflow == s["plan"]["workflow"], "工作流識別不符")
            if operation == "status":
                return self.view(s)
            if operation == "next":
                return self.advance(s)
            p = s["pending"]
            require(payload.get("revision") == digest(s), "工作內容已變更，請重新讀取")
            steps = s["plan"]["steps"]
            if operation == "revise":
                require(
                    not any(n["mode"] == "external" for n in steps[: len(s["history"])])
                    and not (p and p.get("claimed")),
                    "對外操作已送出，須先查核回執",
                )
                i = next(
                    (i for i, n in enumerate(steps) if n["id"] == payload.get("step")),
                    -1,
                )
                require(
                    0 <= i <= len(s["history"]) and payload.get("reason"),
                    "退回步驟或理由無效",
                )
                s["audit"].append(
                    {
                        "event": "revise",
                        "step": steps[i]["id"],
                        "reason": payload["reason"],
                        "invalidated": s["history"][i:],
                    }
                )
                if steps[i]["reuse_approved"]:
                    self.profile_path(s, steps[i]).unlink(missing_ok=True)
                s["history"] = s["history"][:i]
                s["pending"] = None
                atomic_json(self.path, s)
                return self.advance(s)
            require(p and payload.get("task_id") == p["id"], "待辦已變更")
            node = steps[len(s["history"])]
            if "scope_hash" not in p:
                self.bind_review_scope(s, node)
                atomic_json(self.path, s)
                return self.view(s)
            if operation in {"authorize", "decline"}:
                require(
                    p.get("authorization_required")
                    or operation == "decline"
                    and p["kind"] in {"review", "human"},
                    "此待辦不需額外核准",
                )
                require(
                    payload.get("scope_hash") == p["scope_hash"]
                    and payload.get("payload_hash") == p["payload_hash"]
                    and isinstance(payload.get("user_text"), str)
                    and payload["user_text"].strip(),
                    "請回覆顯示的具體操作範圍",
                )
                s["audit"].append(
                    {
                        "event": operation,
                        "scope_hash": p["scope_hash"],
                        "revision": payload["revision"],
                        "user_text": payload["user_text"],
                        "task_id": p["id"],
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                    }
                )
                p["authorization_required"] = operation != "authorize"
                p["declined"] = operation == "decline"
                atomic_json(self.path, s)
                return self.view(s)
            if (
                p.get("binding")
                and p["binding"].get("kind") == "host_probe"
                and operation == "claim"
            ):
                from ..graph.capabilities import validate_bindings

                validate_bindings(self.runtime.workspace_root, {"pending": p["binding"]})
            if operation == "claim":
                require(
                    p.get("claim_required")
                    and not p.get("claimed")
                    and not p.get("authorization_required")
                    and not p.get("declined"),
                    "已領取或不需領取，請依既有回執核對",
                )
                p["claimed"] = True
                atomic_json(self.path, s)
                return self.view(s)
            require(
                operation in {"complete", "reconcile"} and not p.get("declined"),
                "未知或已拒絕的宿主操作",
            )
            evidence = payload.get("evidence", {})
            if p["kind"] in {"review", "human"}:
                require(
                    isinstance(evidence.get("user_text"), str)
                    and evidence["user_text"].strip()
                    and evidence.get("decision") == "approved"
                    and payload.get("scope_hash") == p["scope_hash"]
                    and payload.get("payload_hash") == p["payload_hash"],
                    "需要對應這次範圍與版本的對話回覆；退回請用 revise",
                )
                evidence = {
                    **evidence,
                    "origin": "desktop_conversation",
                    "revision": payload["revision"],
                }
            if p.get("claim_required"):
                require(p.get("claimed"), "尚未領取這次操作")
                if p.get("idempotency_key"):
                    from ..graph.external import read_observation

                    observed = read_observation(
                        self.runtime.workspace_root, p["idempotency_key"], p["scope_hash"]
                    )
                    if observed is None:
                        return {**self.view(s), "code": "RECONCILE_REQUIRED"}
                    require(observed["outcome"] == "succeeded", "工具未回報成功，請核對結果")
                    payload = {**payload, "outputs": observed["outputs"]}
                    evidence = {
                        "receipt": observed["provider_receipt"],
                        "scope_hash": p["scope_hash"],
                    }
                else:
                    require(evidence.get("receipt"), "需要原請求回執；結果不明請先查核")
            if p["kind"] == "review":
                record = p["candidate"]
                record["approval"] = self.approval(s, evidence, p["inputs"], record["outputs"])
                if node["reuse_approved"]:
                    atomic_json(
                        self.profile_path(s, node),
                        {"record": record, "inputs": self.scope(s, node)},
                    )
                s["history"].append(record)
                s["pending"] = None
                atomic_json(self.path, s)
            else:
                reply = payload.get("outputs", {})
                if p.get("binding"):
                    binding = p["binding"]
                    check_boundary(binding, "output", reply)
                    if binding.get("output_mapping"):
                        reply = {
                            k: select_value(reply, selector)
                            for k, selector in binding["output_mapping"].items()
                        }
                answer = self.assets.ingest(
                    reply,
                    self.runtime.workspace_root,
                    self.runtime.workspace_root,
                    set(refs([p["inputs"], s["config"]])),
                )
                self.process(s, node, p["inputs"], answer, evidence)
            return self.advance(s)
