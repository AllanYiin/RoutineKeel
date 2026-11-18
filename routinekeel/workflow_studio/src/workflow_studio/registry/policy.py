from dataclasses import dataclass

from ..contracts import StudioError
from .models import ToolDescription


@dataclass(frozen=True)
class ToolPolicy:
    platform: str
    permissions: frozenset[str] = frozenset({"workspace.write"})
    allow_network: bool = False
    allow_external_effects: bool = False
    max_cost_micro_usd: int = 0

    def check(self, tool: ToolDescription, *, trust: str, status: str):
        if status != "active" or trust not in {"builtin", "approved"}:
            raise StudioError("TOOL_NOT_ALLOWED", "工具未核准、已停用或已封存。")
        if self.platform not in tool.platforms:
            raise StudioError("PLATFORM_UNSUPPORTED", "工具不支援指定平台。")
        if not set(tool.permissions) <= self.permissions:
            raise StudioError("TOOL_NOT_ALLOWED", "工具要求尚未授權的能力。")
        if tool.resources.network and not self.allow_network:
            raise StudioError("TOOL_NOT_ALLOWED", "工具需要網路授權。")
        if tool.side_effects == "external" and not self.allow_external_effects:
            raise StudioError("TOOL_NOT_ALLOWED", "工具需要外部副作用授權。")
        if tool.cost_micro_usd > self.max_cost_micro_usd:
            raise StudioError("BUDGET_EXCEEDED", "工具宣告成本超過允許範圍。")
