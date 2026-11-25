"""Portable design attachments are explicitly named data, never loadable packages."""

import hashlib
import io
import re
import stat
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote

from .errors import StudioError
from .paths import safe_relative

SKILL_BUNDLE = "bundle.zip"
TEXT_SUFFIXES = {
    ".md", ".txt", ".json", ".py", ".sh", ".ps1", ".toml", ".yaml", ".yml",
    ".js", ".ts", ".csv", ".tsv", ".xml", ".html", ".css",
}
TEXT_NAMES = {"LICENSE", "COPYING", "NOTICE"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ttf", ".otf", ".woff", ".woff2"}
MARKDOWN_LINK = re.compile(r"\]\(\s*(?:<([^>]+)>|([^\s)]+))")
MAX_SKILL_BYTES = 24 * 1024**2
MAX_SKILL_FILE = 10 * 1024**2
MAX_SKILL_ITEMS = 1000


def is_skill_text(name):
    path = PurePosixPath(name)
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.upper() in TEXT_NAMES


def _check_path(name):
    parts = PurePosixPath(safe_relative(name)).parts
    if any(
        part.startswith(".") or part.casefold() in {
            "inputs", "outputs", "share_states", "share_logs", "settings.json",
            "router.json", "__pycache__", "credentials.json",
        }
        for part in parts
    ):
        raise StudioError("GRAPH_INVALID", "技能包含私人設定、快取或工作產物路徑。")
    return parts


def missing_skill_files(files):
    missing = set()
    for name, payload in files.items():
        if not name.endswith(".md"):
            continue
        markup = payload.decode("utf-8-sig")
        markup = re.sub(r"```[^\n]*\n.*?```", "", markup, flags=re.S)
        for angle, simple in MARKDOWN_LINK.findall(markup):
            target = unquote((angle or simple).split("#", 1)[0])
            if not target or "://" in target or target.startswith(("mailto:", "tel:")):
                continue
            path = PurePosixPath(target)
            if path.is_absolute():
                continue
            parts = list(PurePosixPath(name).parent.parts)
            for part in path.parts:
                if part == "..":
                    if not parts:
                        break
                    parts.pop()
                else:
                    parts.append(part)
            else:
                prefix = "/".join(parts)
                if prefix in files or any(item.startswith(prefix + "/") for item in files):
                    continue
            missing.add(target)
    return sorted(missing)


def check_skill_bundle(raw):
    if len(raw) > MAX_SKILL_BYTES:
        raise StudioError("GRAPH_INVALID", "技能 ZIP 超過大小上限。")
    files, seen = {}, set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= MAX_SKILL_ITEMS:
                raise ValueError("invalid skill size")
            for item in members:
                name = item.filename.rstrip("/") if item.is_dir() else item.filename
                _check_path(name)
                if item.orig_filename != item.filename or name.casefold() in seen:
                    raise ValueError("duplicate or invalid member")
                seen.add(name.casefold())
                mode = stat.S_IFMT(item.external_attr >> 16)
                if mode not in {0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG} or item.flag_bits & 1:
                    raise ValueError("unsupported zip entry")
                if item.is_dir():
                    continue
                total += item.file_size
                if item.file_size > MAX_SKILL_FILE or total > MAX_SKILL_BYTES:
                    raise ValueError("uncompressed size exceeded")
                files[name] = archive.read(item)
        file_names = {name.casefold() for name in files}
        if any(
            str(parent).casefold() in file_names
            for file in files
            for parent in PurePosixPath(file).parents
        ):
            raise ValueError("file and directory collision")
        _check_members(files)
    except (OSError, ValueError, NotImplementedError, RuntimeError, zipfile.BadZipFile) as exc:
        raise StudioError("GRAPH_INVALID", "無法讀取完整且安全的技能 ZIP。") from exc
    return files


def _check_members(files):
    if not files.get("SKILL.md") or len(files) > MAX_SKILL_ITEMS:
        raise StudioError("GRAPH_INVALID", "技能缺少入口或檔案項目過多。")
    for name, payload in files.items():
        _check_path(name)
        if len(payload) > MAX_SKILL_FILE:
            raise StudioError("GRAPH_INVALID", "技能檔案大小超過限制。")
        if is_skill_text(name):
            try:
                payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise StudioError("GRAPH_INVALID", "技能文字來源必須是 UTF-8。") from exc
        elif PurePosixPath(name).suffix.lower() not in BINARY_SUFFIXES:
            raise StudioError("GRAPH_INVALID", "技能包含未支援的檔案：" + name)


def check_skill_files(graph, files):
    declared = graph.get("provenance", {}).get("skill_files", {})
    if not isinstance(declared, dict) or set(files) != set(declared):
        raise StudioError("GRAPH_INVALID", "附帶技能清單與實際內容不同。")
    skills = set()
    for name, data in files.items():
        parts = _check_path(name)
        bundle = len(parts) == 3 and parts[2] == SKILL_BUNDLE
        if (
            len(parts) < 3
            or parts[0] != "skills"
            or not (bundle or is_skill_text(name))
            or hashlib.sha256(data).hexdigest() != declared[name]
        ):
            raise StudioError("GRAPH_INVALID", "技能來源路徑或內容指紋無效。")
        if bundle:
            entries = check_skill_bundle(data)
            prefix = f"skills/{parts[1]}/"
            if entries["SKILL.md"] != files.get(prefix + "SKILL.md"):
                raise StudioError("GRAPH_INVALID", "技能 ZIP 的 SKILL.md 與附件原文不同。")
        skills.add(parts[1])
    if any(f"skills/{name}/SKILL.md" not in files for name in skills):
        raise StudioError("GRAPH_INVALID", "技能缺少 SKILL.md。")


def inspect_skill_attachments(graph, files):
    """Keep manual skill additions as source evidence alongside the reviewed files."""
    declared = graph.get("provenance", {}).get("skill_files", {})
    if not isinstance(declared, dict):
        raise StudioError("GRAPH_INVALID", "技能附件宣告不是有效的清單。")
    check_skill_files(graph, {name: data for name, data in files.items() if name in declared})
    groups = {}
    for name, payload in files.items():
        parts = _check_path(name)
        if len(parts) < 3 or parts[0] != "skills" or f"skills/{parts[1]}/SKILL.md" not in declared:
            raise StudioError("GRAPH_INVALID", "補入的附件須屬於藍圖已指定的技能。")
        groups.setdefault(parts[1], {})["/".join(parts[2:])] = payload
    findings = []
    for skill, members in sorted(groups.items()):
        prefix = f"skills/{skill}/"
        supplemental = {name for name in members if prefix + name not in declared}
        archive = members.get(SKILL_BUNDLE)
        if archive is None:
            inventory = members
            _check_members(inventory)
        else:
            inventory = check_skill_bundle(archive)
            if any(data != inventory.get(name) for name, data in members.items() if name != SKILL_BUNDLE):
                raise StudioError("GRAPH_INVALID", "技能 ZIP 與原藍圖已有的技能檔案不一致。")
        if supplemental:
            findings.append({
                "kind": "skill", "code": "SKILL_SOURCE_SUPPLEMENTED", "skill": skill,
                "members": {
                    name: hashlib.sha256(data).hexdigest()
                    for name, data in sorted(inventory.items())
                },
            })
        missing = missing_skill_files(inventory)
        if missing:
            findings.append({
                "kind": "skill", "code": (
                    "SKILL_REFERENCE_MISSING" if archive is not None or supplemental
                    else "SKILL_BUNDLE_INCOMPLETE"
                ),
                "skill": skill, "missing": missing,
            })
    return findings
