"""Portable design attachments are explicitly named data, never loadable packages."""

import hashlib
from pathlib import PurePosixPath

from .errors import StudioError
from .paths import safe_relative


def check_skill_files(graph, files):
    declared = graph.get("provenance", {}).get("skill_files", {})
    if not isinstance(declared, dict) or set(files) != set(declared):
        raise StudioError("GRAPH_INVALID", "附帶技能清單與實際內容不同。")
    skills = set()
    for name, data in files.items():
        parts = PurePosixPath(safe_relative(name)).parts
        if (
            len(parts) < 3
            or parts[0] != "skills"
            or any(
                p.startswith(".")
                or p.casefold()
                in {
                    "inputs",
                    "outputs",
                    "share_states",
                    "share_logs",
                    "settings.json",
                    "router.json",
                    "__pycache__",
                }
                for p in parts
            )
            or PurePosixPath(name).suffix.lower()
            not in {".md", ".txt", ".json", ".py", ".sh", ".ps1", ".toml", ".yaml", ".yml"}
            or hashlib.sha256(data).hexdigest() != declared[name]
        ):
            raise StudioError("GRAPH_INVALID", "技能來源路徑或內容指紋無效。")
        skills.add(parts[1])
    if any(f"skills/{name}/SKILL.md" not in files for name in skills):
        raise StudioError("GRAPH_INVALID", "技能缺少 SKILL.md。")
