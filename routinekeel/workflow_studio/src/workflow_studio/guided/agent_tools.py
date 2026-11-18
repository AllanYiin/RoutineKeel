"""What the host agent may call. Nothing here can answer a card, consent, approve or publish."""

import os
import sys
from pathlib import Path

from ..contracts import StudioError
from ..registry.policy import ToolPolicy
from ..service import StudioService

# Claude Code caps MCP output near 25,000 tokens; stay well below it for CJK text.
MAX_READ_CHARS = 12_000


def host_policy():
    platform = {"nt": "windows", "posix": "linux"}[os.name]
    if os.name == "posix" and os.uname().sysname == "Darwin":
        platform = "macos"
    return ToolPolicy(platform)


def find_runtime(workspace):
    """Trials run in the Runtime's own interpreter so a package cannot lean on Studio."""
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    for base in (".venv", "flow_base/.venv", "routinekeel/flow_base/.venv"):
        candidate = Path(workspace) / base / executable
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


class AgentTools:
    """Each call opens its own storage connection so the local page thread never shares one."""

    def __init__(self, workspace, page_url=None):
        self.workspace, self.page_url = Path(workspace), page_url

    def _studio(self):
        studio = StudioService.open_workspace(self.workspace)
        studio.tools.seed()
        return studio

    def open(self, name=None, project_id=None):
        """Start or resume a project and get the page where the person confirms cards."""
        with self._studio() as studio:
            if project_id is None:
                if not isinstance(name, str) or not name.strip():
                    raise StudioError("INPUT_INVALID", "新專案需要一個名稱；可由你依對話內容提議。")
                project_id = studio.projects.create(name.strip()).project_id
            state = studio.guided(host_policy()).state(project_id)
        url = self.page_url(project_id) if self.page_url else None
        return {
            "project_id": project_id,
            "page_url": url,
            "stage": state["stage"],
            "next": state["next"],
        }

    def state(self, project_id):
        with self._studio() as studio:
            return studio.guided(host_policy()).state(project_id)

    def tools(self):
        """Closed list of steps a draft may use, with the ports each one accepts."""
        with self._studio() as studio:
            return [
                {
                    "tool": tool.tool_id,
                    "version": tool.version,
                    "title": tool.title,
                    "kind": tool.kind,
                    "capabilities": tool.capabilities,
                    "inputs": {k: v.model_dump(exclude_none=True) for k, v in tool.inputs.items()},
                    "outputs": {
                        k: v.model_dump(exclude_none=True) for k, v in tool.outputs.items()
                    },
                    "compilable": studio.tools.support(tool)[0],
                }
                for tool in studio.tools.allowed(host_policy()).values()
            ]

    def sources(self, project_id):
        """Names and sizes only; contents need the person's consent on the page."""
        with self._studio() as studio:
            consented = set(studio.guided(host_policy()).state(project_id).get("consented", []))
            return [
                {
                    "source_id": source.source_id,
                    "version": source.version,
                    "name": source.name,
                    "readable": source.source_id in consented,
                    "files": [
                        {"path": record["path"], "size": record["size"]}
                        for record in source.manifest["files"]
                    ],
                }
                for source in studio.sources.list(project_id)
            ]

    def source_read(self, project_id, source_id, path, offset=0):
        with self._studio() as studio:
            state = studio.guided(host_policy()).state(project_id)
            if source_id not in state.get("consented", []):
                raise StudioError(
                    "APPROVAL_REQUIRED", "作者尚未同意把這份來源交給模型閱讀；請作者在頁面上勾選。"
                )
            content = studio.sources.content(project_id, source_id, path)
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise StudioError("INPUT_INVALID", "此檔案不是 UTF-8 文字，無法分段閱讀。") from exc
        if type(offset) is not int or offset < 0:
            raise StudioError("INPUT_INVALID", "offset 必須是非負整數。")
        chunk = text[offset : offset + MAX_READ_CHARS]
        end = offset + len(chunk)
        return {
            "text": chunk,
            "first_line": text.count("\n", 0, offset) + 1,
            "next_offset": end if end < len(text) else None,
            "total_chars": len(text),
        }

    def submit_draft(self, project_id, expected_version, draft):
        with self._studio() as studio:
            return studio.guided(host_policy()).submit_draft(project_id, expected_version, draft)

    def respond_reflection(self, project_id, expected_version, responses):
        with self._studio() as studio:
            return studio.guided(host_policy()).respond(project_id, expected_version, responses)

    def trial(self, project_id, expected_version):
        """Build the committed version and compare it with the person's cases."""
        with self._studio() as studio:
            return studio.guided(host_policy()).trial(
                project_id, expected_version, find_runtime(self.workspace)
            )
