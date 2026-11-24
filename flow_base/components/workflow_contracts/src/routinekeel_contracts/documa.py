"""Local Documa CLI boundary shared by Studio and Flow Base."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .errors import StudioError

DOCUMENT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".docm",
        ".xlsm",
        ".pptm",
        ".html",
        ".htm",
        ".xhtml",
        ".eml",
        ".msg",
        ".ipynb",
    }
)
DOCUMA_CONFIG = frozenset({"ROUTINEKEEL_DOCUMA", "ROUTINEKEEL_DOCUMA_SKILL_STORE"})
_PROCESS_ENV = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    }
)


def documa_cli(workspace):
    observed = os.environ.get("ROUTINEKEEL_DOCUMA") or shutil.which("documa")
    executable = Path(observed or "")
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or executable.resolve().is_relative_to(Path(workspace).resolve())
        or (os.name == "nt" and executable.suffix.lower() != ".exe")
    ):
        raise StudioError(
            "DOCUMA_UNAVAILABLE",
            "請安裝 Documa，或設定 ROUTINEKEEL_DOCUMA 的絕對執行檔路徑。",
        )
    return executable.resolve()


def call_documa(
    executable, *arguments, directory, timeout=120, allow_unsuccessful=False
):
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in _PROCESS_ENV
    }
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        result = subprocess.run(
            [str(executable), *map(str, arguments)],
            cwd=directory,
            capture_output=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
            errors="strict",
            env=environment,
        )
        payload = json.loads(result.stdout)
        if isinstance(payload, dict) and (
            (result.returncode == 0 and payload.get("status", "ok") == "ok")
            or (
                allow_unsuccessful
                and payload.get("status") in {"error", "needs_narrowing"}
            )
        ):
            return payload
        raise ValueError("Documa did not return a structured response")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise StudioError(
            "DOCUMA_FAILED",
            f"Documa {arguments[0]} 執行失敗，請檢查本機安裝與輸入格式。",
        ) from exc


def build_document_index(executable, source, folder):
    processed = call_documa(
        executable, "process", source, "--out", folder, directory=folder
    )
    ir_path = folder / "documa.ir.json"
    index_path = folder / "context.json"
    context = call_documa(
        executable,
        "context-build",
        "document",
        ir_path,
        "--out",
        index_path,
        "--store-dir",
        folder / "store",
        directory=folder,
    )
    return (
        {
            "document_id": processed["document_id"],
            "parser": processed.get("parser"),
            "block_count": context["block_count"],
            "relation_count": context["relation_count"],
        },
        ir_path,
        index_path,
    )


def search_document_index(executable, index_path, query, *, directory, route="auto"):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000:
        raise StudioError("DOCUMENT_QUERY", "文件查詢須有 1–1000 個字元。")
    if route not in {"auto", "lexical-first", "graph-first", "overview"}:
        raise StudioError("DOCUMENT_QUERY", "未知的 Documa 查詢路徑。")
    found = call_documa(
        executable,
        "context-search",
        "--route",
        route,
        "--max-evidence-blocks",
        "3",
        "--",
        index_path,
        query,
        directory=directory,
    )
    candidates = []
    for item in found["candidates"]:
        row = {
            key: item[key]
            for key in (
                "blockId",
                "title",
                "snippet",
                "graphPath",
                "sourceSpan",
                "reasons",
            )
            if key in item
        }
        if row.get("title") == item.get("sourceLocator"):
            row["title"] = "文件"
        candidates.append(row)
    return {
        "candidates": candidates,
        "graph_used": found["graphUsed"],
        "route": found["route"],
    }


def load_skills(workspace, task, *, skill_names=(), max_tokens=None):
    """Only Documa's operator-configured trusted catalog can supply instructions."""
    if not isinstance(task, str) or not 1 <= len(task.strip()) <= 16000:
        raise StudioError("INPUT_INVALID", "請提供 1–16000 個字元的載入任務。")
    if (
        not isinstance(skill_names, (tuple, list))
        or len(skill_names) > 20
        or any(
            not isinstance(name, str) or not 1 <= len(name.strip()) <= 200
            for name in skill_names
        )
        or (
            max_tokens is not None
            and (type(max_tokens) is not int or not 1 <= max_tokens <= 100000)
        )
    ):
        raise StudioError("INPUT_INVALID", "技能名稱、數量或 token 預算無效。")
    store = Path(os.environ.get("ROUTINEKEEL_DOCUMA_SKILL_STORE", ""))
    if not store.is_absolute() or not store.is_dir():
        return {
            "status": "needs_narrowing",
            "code": "SKILL_CONFIG_MISSING",
            "candidates": [],
        }
    return load_skill_bundle(
        documa_cli(workspace), store, task, skill_names, max_tokens=max_tokens
    )


def load_skill_bundle(executable, store, task, skill_names, *, max_tokens=None):
    options = ["--store-dir", str(store.resolve())]
    for name in skill_names:
        options.append("--skill=" + name)
    if max_tokens is not None:
        options.extend(["--max-tokens", str(max_tokens)])
    return call_documa(
        executable,
        "skills",
        "load",
        *options,
        "--",
        task,
        directory=store,
        allow_unsuccessful=True,
    )
