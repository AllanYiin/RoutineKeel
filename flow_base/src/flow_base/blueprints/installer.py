"""First installation. Drafts and trials live outside discoverable workflow folders."""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from ..execution.process import worker_environment
from ..outputs import reject_links
from ..package_updates import (
    MAX_ZIP,
    _idle,
    _read_archive,
    ensure_no_pending,
    package_lock,
)
from ..runtime import FlowError, atomic_bytes, atomic_json, digest, read_json, sha256
from ..workspace import Manifest
from .adapter import ADAPTER_SOURCE
from .ir import POLICY_VERSION, check_with_studio, studio_ir
from .recipe import compile_plan, local_operations
from .source import inspect_blueprint, require
from .wheel import write_wheel


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode()


def admission(ir, package_hash, validation, evidence):
    from routinekeel_contracts.builder import issue_admission

    return issue_admission(
        ir,
        package_hash,
        validation["status"],
        [
            {
                "case_id": str(row["case"]),
                "status": "passed" if evidence["passed"] else "failed",
                "output_hash": row["observed_outputs_hash"],
            }
            for row in evidence["cases"]
        ],
    )


def read_archive(path):
    require(path.is_file() and path.stat().st_size <= MAX_ZIP, "附件不存在或過大")
    with path.open("rb") as f:
        raw = f.read(MAX_ZIP + 1)
    require(len(raw) <= MAX_ZIP, "附件過大")
    return raw


def draft_dir(ws, key):
    require(isinstance(key, str) and re.fullmatch("[a-f0-9]{64}", key), "草稿編號無效")
    folder = ws.runtime.home / "blueprint_drafts" / key
    reject_links(folder)
    return folder


def import_source(ws, path):
    raw = read_archive(path)
    suggested = None
    # Portable packages carry the original, inert blueprint. Recompile locally.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        executable = any(n.endswith(".whl") for n in archive.namelist()) and not any(
            n.endswith("/workflow.definition.json") for n in archive.namelist()
        )
    if executable:
        manifest, files, _, _ = _read_archive(path)
        with zipfile.ZipFile(io.BytesIO(files[manifest.package])) as wheel:
            item = wheel.getinfo(manifest.python_module + "/source.blueprint")
            require(item.file_size <= MAX_ZIP, "藍圖來源過大")
            raw = wheel.read(item)
            suggested = json.loads(wheel.read(manifest.python_module + "/conversion.json"))
    src, files = inspect_blueprint(raw)
    if "graph" not in src:
        try:
            from ..graph.source import convert_linear

            src["graph"], src["graph_order"] = convert_linear(src)
        except ImportError:
            src["findings"].append({"kind": "graph_conversion", "code": "VALIDATOR_UNAVAILABLE"})
        except FlowError as exc:
            src["findings"].append(
                {"kind": "graph_conversion", "code": exc.code, "reason": str(exc)}
            )
    folder = draft_dir(ws, src["source_hash"])
    with package_lock(ws):
        atomic_bytes(folder / "source.zip", raw)
        atomic_json(folder / "blueprint.json", src)
        if suggested is not None:
            atomic_json(folder / ("proposal-" + digest(suggested) + ".json"), suggested)
    return {
        "status": "draft",
        "draft_id": src["source_hash"],
        "workflow_id": src["manifest"]["id"],
        "version": src["manifest"]["version"],
        "steps": src["steps"],
        "blueprint_schema_version": src.get("blueprint_schema_version", 1),
        "dependencies": src["dependencies"],
        "gaps": src["gaps"],
        "review": src["definition"]["review"],
        "draft": str(folder),
        "suggested_resolution": suggested,
        "findings": src["findings"],
        "local_operations": {k: v.dist.version for k, v in local_operations().items()},
    }


def read_member(ws, key, name, offset):
    folder = draft_dir(ws, key)
    raw = read_archive(folder / "source.zip")
    require(hashlib.sha256(raw).hexdigest() == key, "草稿來源已變更")
    _src, files = inspect_blueprint(raw)
    require(
        name in files and not name.lower().endswith((".ttf", ".otf")),
        "請選擇藍圖內的文字檔",
    )
    content = files[name].decode("utf-8-sig")
    require(type(offset) is int and 0 <= offset <= len(content), "文字位移無效")
    return {
        "draft_id": key,
        "member": name,
        "offset": offset,
        "total_chars": len(content),
        "text": content[offset : offset + 16384],
    }


def pack(directory, src, plan, raw, resolution, ir, registry):
    manifest = dict(src["manifest"])
    module = "rk_bp_" + manifest["id"]
    version = plan["version"]
    wheel = directory / f"{module}-{version}-py3-none-any.whl"
    guide = (
        "此流程透過底座接受 JSON 工作輸入。執行 run 後查看 blueprint.pending；"
        "透過 blueprint-task 完成各步並依回執續跑。見 flow_base/AGENTS.md。"
    )
    if plan["format"] == 2:
        manifest.update(
            schema_version=1,
            runtime_api="0.2",
            description=manifest["title"],
            keywords=[manifest["id"]],
        )
    payload = {
        "adapter.py": ADAPTER_SOURCE,
        "__init__.py": b"",
        "plan.json": encoded(plan),
        "tool-signatures.json": encoded(registry),
        "source.blueprint": raw,
        "conversion.json": encoded({k: v for k, v in resolution.items() if k != "bindings"}),
    }
    # Keep attachment code inert. Only selected Markdown is exposed at execution.
    write_wheel(wheel, module, version, {module + "/" + k: v for k, v in payload.items()})
    manifest.update(
        module=module,
        version=version,
        package=wheel.name,
        sha256=sha256(wheel),
        actions=[],
        input_extensions=[".json"],
        output_layout="categorized-v1",
        outputs={},
        defaults={},
        rules=[],
        parameter_paths=[],
    )
    Manifest.model_validate(manifest)
    release = {
        "flow.json": encoded(manifest),
        wheel.name: wheel.read_bytes(),
        "AGENTS.md": guide.encode(),
        "README.md": (manifest["title"] + "\n\n" + guide).encode(),
        "workflow.ir.json": encoded(ir),
        "PROVENANCE.md": (
            "Blueprint SHA-256: "
            + src["source_hash"]
            + "\nIR SHA-256: "
            + digest(ir)
            + "\nRuntime plan SHA-256: "
            + digest(plan)
        ).encode(),
        "settings.example.json": b"{}",
    }
    path = directory / "workflow.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, body in sorted(release.items()):
            member = zipfile.ZipInfo(manifest["id"] + "/" + name)
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(member, body)
    _read_archive(path)
    return path


def verify(ws, key, resolution, cases):
    folder = draft_dir(ws, key)
    raw = read_archive(folder / "source.zip")
    src, files = inspect_blueprint(raw)
    require(src["source_hash"] == key, "草稿來源已變更")
    build = uuid.uuid4().hex
    dest = folder / "builds" / build
    reject_links(dest)
    dest.mkdir(parents=True)
    atomic_json(dest / "resolution.json", resolution)
    validation = {"policy_version": POLICY_VERSION, "status": "NOT_RUN"}
    try:
        if src.get("blueprint_schema_version") == 2:
            from ..graph.builder import build_graph

            plan, ir, registry, cases = build_graph(src, resolution, cases, ws.root)
            capability_evidence = registry["bindings"]
            resolution = {**resolution, "bindings": capability_evidence}
            atomic_json(dest / "resolution.json", resolution)
        else:
            plan = compile_plan(src, resolution)
            from ..graph.capabilities import validate_bindings

            capability_evidence = validate_bindings(ws.root, resolution["bindings"])
            skill_index = {row["id"]: row for row in src["skills"]["skills"]}
            for node in plan["steps"]:
                used = list(node["skills"])
                for name in used:
                    for child in skill_index[name].get("uses", []):
                        if child not in used:
                            used.append(child)
                node["skill_text"] = {
                    skill: files["skills/" + skill + "/SKILL.md"].decode("utf-8-sig")
                    for skill in used
                }
            ir, registry = studio_ir(src, plan)
        atomic_json(dest / "workflow.ir.json", ir)
        atomic_json(dest / "tool-signatures.json", registry)
        atomic_json(dest / "cases.json", cases)
        path = pack(dest, src, plan, raw, resolution, ir, registry)
        require(isinstance(cases, list) and 1 <= len(cases) <= 20, "需要 1–20 組驗收案例")
        require(len(encoded(cases)) <= 2 * 1024**2, "案例描述太大")
        from .trial import stage_fixtures

        with tempfile.TemporaryDirectory(prefix="rk-trial-") as temp:
            trial = Path(temp) / "workspace"
            trial.mkdir()
            trial_catalog = Path(temp) / "host" / "catalog.json"
            if any(item["provider"] == "desktop_host" for item in capability_evidence.values()):
                # The trial can inspect already observed tool metadata; its receipts are disposable.
                atomic_json(trial_catalog, read_json(Path(os.environ["ROUTINEKEEL_HOST_CATALOG"])))
            else:
                atomic_json(
                    trial_catalog,
                    {"version": 1, "expires_at": "2099-01-01T00:00:00+00:00", "tools": {}},
                )
            stage_fixtures(ws.root, trial, cases)
            _, members, _modes, _ = _read_archive(path)
            task = trial / src["manifest"]["id"]
            task.mkdir()
            for n, body in members.items():
                (task / n).write_bytes(body)
            validation = check_with_studio(ir, registry, task)
            require(validation["status"] == "PASS", validation.get("reason", "必要驗證未通過"))
            atomic_json(
                trial / "share_states/blueprint_bindings" / (digest(plan) + ".json"),
                resolution["bindings"],
            )
            atomic_json(trial / "cases.json", cases)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    "utf8",
                    "-m",
                    "flow_base.blueprints.trial",
                    str(trial),
                    task.name,
                ],
                cwd=trial,
                env=worker_environment() | {"ROUTINEKEEL_HOST_CATALOG": str(trial_catalog)},
                capture_output=True,
                timeout=300, check=False,
            )
            require((trial / "result.json").is_file(), "隔離試做未回報結果")
            evidence = read_json(trial / "result.json")
            evidence["studio_ir_and_record"] = "passed"
            if not evidence.get("passed"):
                atomic_json(dest/"trial-failure.json",evidence)
            require(
                proc.returncode == 0 and evidence.get("passed"),
                "候選包驗證失敗：" + evidence.get("error", ""),
            )
            shutil.copytree(trial / "evidence", dest / "trials")
        record = {
            "status": "verified",
            "validation": validation,
            "workflow_id": task.name,
            "draft_id": key,
            "build_id": build,
            "archive_sha256": sha256(path),
            "archive": str(path),
            "plan_hash": digest(plan),
            "bindings_hash": digest(resolution["bindings"]),
            "capability_evidence": capability_evidence,
            "evidence": evidence,
        }
        record["builder_receipt"] = admission(ir, record["archive_sha256"], validation, evidence)
        record["install_hash"] = digest(record)
        atomic_json(dest / "verification.json", record)
        return record
    except Exception as exc:
        atomic_json(
            dest / "verification.json",
            {
                "status": "blocked",
                "validation": validation,
                "build_id": build,
                "reason": str(exc)[:1500],
            },
        )
        if isinstance(exc, FlowError) and exc.code == "GRAPH_UNSUPPORTED":
            raise
        raise FlowError(
            "INVALID_CONFIGURATION",
            "草稿已保存；請修訂後再試做",
            draft_id=key,
            build_id=build,
            reason=str(exc)[:1500],
        ) from exc


def checked_candidate(ws, dest, expected):
    record = read_json(dest / "verification.json")
    require(
        record.get("status") == "verified"
        and record.get("validation") == {"status": "PASS", "policy_version": POLICY_VERSION}
        and expected
        and record.get("install_hash") == expected
        and digest({k: v for k, v in record.items() if k != "install_hash"}) == expected,
        "需要同一建置的驗證收據",
    )
    manifest, files, modes, h = _read_archive(dest / "workflow.zip")
    require(
        record["archive_sha256"] == h and record["workflow_id"] == manifest.id,
        "候選包已變更",
    )
    with tempfile.TemporaryDirectory(prefix="rk-install-check-") as temp:
        package = Path(temp)
        for name, body in files.items():
            (package / name).write_bytes(body)
        with zipfile.ZipFile(io.BytesIO(files[manifest.package])) as wheel:
            registry = json.loads(wheel.read(manifest.python_module + "/tool-signatures.json"))
        validation = check_with_studio(json.loads(files["workflow.ir.json"]), registry, package)
        require(
            validation == record["validation"], validation.get("reason", "安裝前必要驗證未通過")
        )
        require(
            admission(json.loads(files["workflow.ir.json"]), h, validation, record["evidence"])
            == record.get("builder_receipt"),
            "需要同一來源、試做與套件的 Builder 收據",
        )
    bindings = read_json(dest / "resolution.json")["bindings"]
    if "blueprint_schema_version" in json.loads(files["workflow.ir.json"]):
        from ..graph.capabilities import resolve_graph

        live = resolve_graph(json.loads(files["workflow.ir.json"]), ws.root)
        require(
            live["status"] == "ready"
            and live["bindings"] == bindings
            and digest(bindings) == record["bindings_hash"],
            "Graph 能力綁定已變更",
        )
    else:
        from ..graph.capabilities import validate_bindings

        require(
            validate_bindings(ws.root, bindings) == record.get("capability_evidence", {}),
            "能力回執或權限已變更，請重新試做",
        )
        require(
            digest(bindings) == record["bindings_hash"]
            and not any(b["kind"] == "fixture" for b in bindings.values()),
            "正式安裝需要目標宿主的實際能力證據",
        )
        bindings = {
            name: (
                {
                    **binding,
                    "authority_hash": record["capability_evidence"][name]["contract_hash"],
                }
                if binding["kind"] == "host_probe"
                else binding
            )
            for name, binding in bindings.items()
        }
    return record, manifest, files, modes, h, bindings


def install(ws, key, build, expected):
    require(isinstance(build, str) and re.fullmatch("[a-f0-9]{32}", build), "建置識別碼無效")
    dest = draft_dir(ws, key) / "builds" / build
    reject_links(dest)
    with package_lock(ws):
        ensure_no_pending(ws)
        record, manifest, files, modes, h, bindings = checked_candidate(ws,dest,expected)
        tasks = ws.discover(read_only=True)
        current = tasks.get(manifest.id)
        binding_path = ws.runtime.home / "blueprint_bindings" / (record["plan_hash"] + ".json")
        reject_links(binding_path)
        if current and current.manifest != manifest:
            if not binding_path.exists():
                atomic_json(binding_path, bindings)
            return {
                "status": "update_required",
                "workflow_id": manifest.id,
                "archive": str(dest / "workflow.zip"),
                "from_version": current.manifest.version,
                "to_version": manifest.version,
            }
        require(
            not any(
                t.manifest.python_module == manifest.python_module and t.manifest.id != manifest.id
                for t in tasks.values()
            ),
            "流程模組名稱衝突",
        )
        _idle(ws)
        target = (
            current.folder
            if current
            else ws.workflows_root / ("FLOW_" + manifest.id if ws.nested_layout else manifest.id)
        )
        reject_links(target)
        require(current or not target.exists(), "目標資料夾已存在")
        if current:
            for name, body in files.items():
                file = target / name
                reject_links(file)
                require(
                    file.is_file() and sha256(file) == hashlib.sha256(body).hexdigest(),
                    "既有套件內容不同",
                )
        atomic_json(binding_path, bindings)
        if not current:
            with tempfile.TemporaryDirectory(prefix=".install-", dir=ws.root) as tmp:
                stage = Path(tmp) / "package"
                stage.mkdir()
                for name, body in files.items():
                    file = stage / name
                    atomic_bytes(file, body)
                    file.chmod(modes[name])
                    require(
                        sha256(file) == hashlib.sha256(body).hexdigest(),
                        "安裝前檔案驗證失敗",
                    )
                stage.rename(target)
        receipt = {
            "status": "already_installed" if current else "installed",
            "workflow_id": manifest.id,
            "version": manifest.version,
            "folder": str(target),
            "build_id": build,
            "archive_sha256": h,
        }
        atomic_json(dest / "installation.json", receipt)
        return receipt
