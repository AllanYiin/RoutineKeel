"""Freeze an approved design, check it with the installed importer, then deliver inert bytes."""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from filelock import FileLock
from routinekeel_contracts.graph_validator import validate_blueprint
from routinekeel_contracts.paths import safe_relative
from routinekeel_contracts.skill_files import check_skill_files

from ..contracts import StudioError
from ..ingestion.security import reject_links
from ..project.models import canonical_json
from .interviews import DOMAINS, digest


def fail(message):
    raise StudioError("BLUEPRINT_BLOCKED", message)


def check_portable(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key.casefold()
                in {
                    "api_key",
                    "access_token",
                    "private_key",
                    "password",
                    "client_secret",
                    "authorization",
                }
                and child
            ) and (not isinstance(child, str) or not child.startswith("secret://")):
                fail("設計含有私人認證欄位，請改為具名需求。")
            check_portable(child)
    elif isinstance(value, list):
        for child in value:
            check_portable(child)
    elif isinstance(value, str) and re.search(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer [A-Za-z0-9._~+/=-]{12,}|\bsk-[A-Za-z0-9_-]{20,}|(?:(?<![A-Za-z0-9_-])[A-Za-z]:[\\/]|/(?:Users|home)/)[^\s]+",
        value,
        re.IGNORECASE,
    ):
        fail("設計含有認證資料或本機絕對路徑，請先修訂。")


def skill_contents(studio, pid, selections, *, current=False):
    attachments, versions = {}, {}
    for selection in selections:
        if set(selection) != {"name", "source_id", "version"}:
            fail("技能來源須指定名稱、來源與版本。")
        name = safe_relative(selection["name"])
        if "/" in name or name in versions:
            fail("技能名稱須唯一且為單一目錄。")
        source = studio.sources.get(pid, selection["source_id"], version=selection["version"])
        if current and (
            source.status != "active"
            or studio.sources.get(pid, source.source_id).version != source.version
        ):
            fail("技能來源已更新或封存，請重新檢視。")
        versions[name] = {
            "source_id": source.source_id,
            "version": source.version,
            "sha256": source.content_hash,
        }
        for item in source.manifest["files"]:
            rel = "skills/" + name + "/" + safe_relative(item["path"])
            attachments[rel] = studio.sources.content(
                pid, source.source_id, item["path"], version=source.version
            )
            check_portable(attachments[rel].decode("utf-8-sig"))
    return attachments, versions


def archive_bytes(graph, files):
    check_portable(graph)
    check_skill_files(graph, files)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in sorted(
            {"blueprint.json": canonical_json(graph).encode(), **files}.items()
        ):
            info = zipfile.ZipInfo("design/" + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, body, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def prepare(
    studio, project_id, expected_revision, request_id, graph, design=None, skills=None, lineage=None
):
    design = {} if design is None else design
    skills = [] if skills is None else skills
    revision_source = {}
    if lineage is not None:
        if (
            not isinstance(lineage, dict)
            or set(lineage) != {"base_source_hash", "improvement_id"}
            or not all(
                isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v) for v in lineage.values()
            )
        ):
            fail("修訂來源須包含提案與原藍圖指紋。")
        revision_source = {"lineage": lineage}
    if not isinstance(design, dict) or set(design) - DOMAINS.keys():
        fail("設計摘要含未知領域。")
    model, order = validate_blueprint(graph)
    canonical = model.model_dump(mode="json", exclude_none=True)
    if canonical["provenance"]:
        fail("來源追蹤由 Studio 依專案版本產生。")

    def edit(db, state):
        if state["phase"] == "paused":
            fail("請先恢復訪談。")
        files, versions = skill_contents(studio, project_id, skills, current=True)
        controls = [n for n in canonical["nodes"] if n["kind"] not in {"task", "human_review"}]
        summary = {
            "goal": model.workflow.goal,
            "inputs": canonical["inputs"],
            "outputs": canonical["outputs"],
            "steps": canonical["nodes"],
            "controls": controls,
            "review": [
                {"step": n["id"], "prompt": n["prompt"]}
                for n in canonical["nodes"]
                if n["kind"] == "human_review"
            ],
            "capabilities": [
                {
                    "node": n["id"],
                    "capability": n["capability"],
                    "permissions": n["permissions"],
                    "side_effect": n["side_effect"],
                }
                for n in canonical["nodes"]
                if n["kind"] == "task"
            ],
            "quality": canonical["quality_contract"],
            "success": canonical["quality_contract"].get("success_criteria"),
            "cases": canonical["quality_contract"].get("golden_cases"),
            "evolution": canonical["evolution_policy"] or {"automatic_promotion": False},
            "actors": "本機使用者",
            "preferences": {},
            "recovery": "中斷後保留已完成成果",
            "budget": "未授權付費",
            "rules": None,
        }
        for key, item in state["coverage"].items():
            if item["status"] == "blocked" and key not in design:
                fail("仍待釐清：" + key)
            if key in design:
                if item["status"] == "confirmed" and item["value"] != design[key]:
                    fail("請先修訂已確認的決策：" + key)
                if (
                    key
                    in {
                        "goal",
                        "inputs",
                        "outputs",
                        "steps",
                        "controls",
                        "review",
                        "capabilities",
                        "quality",
                        "success",
                        "cases",
                    }
                    and design[key] != summary[key]
                ):
                    fail("摘要與圖形欄位不一致：" + key)
                summary[key] = design[key]
            if item["status"] == "confirmed":
                # Keep the human wording as well as the executable design representation.
                if key == "goal" and item["value"] != summary["goal"]:
                    fail("圖形目標與已確認訪談不符。")
                if key not in design and key not in {
                    "goal",
                    "inputs",
                    "outputs",
                    "steps",
                    "controls",
                    "review",
                    "capabilities",
                    "quality",
                    "success",
                    "cases",
                }:
                    summary[key] = item["value"]
            if summary.get(key) is None:
                fail("設計尚缺少：" + key)
        if not summary["cases"] or not summary["success"]:
            fail("請補上成功條件與驗收案例。")
        confirmed = {
            k: {"value": v["value"], "revision": v["revision"]}
            for k, v in state["coverage"].items()
            if v["status"] == "confirmed"
        }
        proposed = {
            **revision_source,
            "graph": canonical,
            "decisions": summary,
            "confirmed": confirmed,
            "skill_sources": versions,
            "skill_files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
            "selections": skills,
            "order": order,
            "project_id": project_id,
            "revision": expected_revision + 1,
        }
        check_skill_files({"provenance": {"skill_files": proposed["skill_files"]}}, files)
        reviews = "、".join(item["step"] for item in summary["review"]) or "無"
        proposed["display_text"] = (
            f"{model.workflow.goal}\n具名交付：{'、'.join(model.outputs)}\n"
            f"流程含 {len(order)} 個節點；指定人工校閱：{reviews}。\n"
            "請檢視步驟、業務規則與驗收案例，可直接在對話確認整份設計或指出要修改的地方。"
        )
        proposed["proposal_id"] = digest(proposed)
        state["blueprint_proposal"] = proposed
        state["phase"] = "needs_confirmation"

    state = studio.interviews._change(
        project_id,
        expected_revision,
        request_id,
        {
            "action": "blueprint_preview",
            "graph": canonical,
            "design": design,
            "skills": skills,
            **revision_source,
        },
        edit,
    )
    return state["blueprint_proposal"]


def check_import(raw, expected):
    exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    adjacent = Path(sys.prefix).resolve().parent / ".venv" / exe
    python = (
        adjacent
        if Path(sys.prefix).name == ".studio-venv" and adjacent.is_file()
        else Path(sys.executable)
    )
    with tempfile.TemporaryDirectory(prefix="rk-export-check-") as folder:
        path = Path(folder) / "source.zip"
        path.write_bytes(raw)
        code = (
            "import json,sys;from pathlib import Path;from flow_base.blueprints.source import inspect_blueprint;"
            "source,_=inspect_blueprint(Path(sys.argv[1]).read_bytes());print(json.dumps(source['graph']))"
        )
        result = subprocess.run(
            [str(python), "-I", "-X", "utf8", "-c", code, str(path)],
            capture_output=True,
            encoding="utf-8",
            cwd=folder,
            timeout=40,
        )
        if result.returncode or json.loads(result.stdout) != expected:
            fail("底座匯入結果與已確認設計不一致。")


def materialize(workspace, workflow, raw):
    key = hashlib.sha256(raw).hexdigest()
    root = Path(workspace).resolve()
    folder = root / "blueprints" / safe_relative(workflow)
    for parent in (root / "blueprints", folder):
        parent.mkdir(exist_ok=True)
        reject_links(parent)
    target = folder / (key + ".zip")
    lock = folder / (key + ".lock")
    if lock.exists() or lock.is_symlink():
        reject_links(lock)
    with FileLock(str(lock), timeout=15):
        if target.exists() or target.is_symlink():
            reject_links(target)
            if target.read_bytes() != raw:
                fail("現有藍圖與版本內容不符，已保留原檔。")
        else:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
                staged = Path(temp.name)
                try:
                    temp.write(raw)
                    temp.flush()
                    os.fsync(temp.fileno())
                finally:
                    temp.close()
            try:
                os.replace(staged, target)
            finally:
                staged.unlink(missing_ok=True)
    return target


def export(
    studio, workspace, project_id, expected_revision, request_id, proposal_id, user_text, decision
):
    if not isinstance(user_text, str) or not user_text.strip():
        fail("請先在對話確認目前顯示的藍圖摘要。")

    def edit(db, state):
        proposal = state.get("blueprint_proposal", {})
        if (
            proposal.get("proposal_id") != proposal_id
            or proposal.get("revision") != expected_revision
        ):
            fail("摘要已過期，請重新預覽。")
        if decision == "decline":
            state.pop("blueprint_proposal")
            state["phase"] = "collecting"
            state["last_blueprint_decision"] = {
                "proposal_id": proposal_id,
                "decision": decision,
                "user_text": user_text,
            }
            return
        if decision != "approve":
            fail("尚未收到這一版的匯出決定。")
        files, versions = skill_contents(studio, project_id, proposal["selections"], current=True)
        if versions != proposal["skill_sources"]:
            fail("技能來源版本已變更。")
        graph = dict(proposal["graph"])
        graph["provenance"] = {
            **({"lineage": proposal["lineage"]} if "lineage" in proposal else {}),
            "project_id": project_id,
            "interview_revision": expected_revision,
            "proposal_id": proposal_id,
            "decisions_hash": digest(proposal["decisions"]),
            "design": {
                k: v
                for k, v in proposal["decisions"].items()
                if k
                not in {
                    "steps",
                    "inputs",
                    "outputs",
                    "quality",
                    "controls",
                    "review",
                    "capabilities",
                }
            },
            "skill_sources": versions,
            "skill_files": proposal["skill_files"],
        }
        raw = archive_bytes(graph, files)
        check_import(raw, graph)
        state["blueprint_revision"] = {
            "graph": graph,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "revision": expected_revision + 1,
            "selections": proposal["selections"],
            "confirmation_text": user_text,
        }
        for domain, value in proposal["decisions"].items():
            item = state["coverage"][domain]
            if item["status"] != "confirmed":
                item.update(
                    value=value,
                    status="confirmed",
                    origin="desktop_conversation",
                    evidence_refs=["design-summary/" + proposal_id],
                    revision=expected_revision + 1,
                )
        state["stale_cases"] = []
        state["phase"] = "exported"

    state = studio.interviews._change(
        project_id,
        expected_revision,
        request_id,
        {
            "action": "export",
            "proposal_id": proposal_id,
            "user_text": user_text,
            "decision": decision,
        },
        edit,
    )
    if decision == "decline":
        return {"status": "declined", "project_id": project_id, "revision": state["revision"]}
    return deliver(studio, workspace, state["blueprint_revision"], project_id)


def deliver(studio, workspace, revision, project_id):
    graph = revision["graph"]
    files, _ = skill_contents(studio, project_id, revision["selections"])
    raw = archive_bytes(graph, files)
    if hashlib.sha256(raw).hexdigest() != revision["sha256"]:
        fail("歷史來源與匯出版本指紋不一致。")
    path = materialize(workspace, graph["workflow"]["id"], raw)
    return {
        "status": "exported",
        "project_id": project_id,
        "revision": revision["revision"],
        "path": str(path),
        "sha256": revision["sha256"],
    }
