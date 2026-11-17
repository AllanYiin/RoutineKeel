from ..ir.models import Port
from ..ir.validator import compatible
from .catalog import Catalog
from .policy import ToolPolicy


def match_tools(
    catalog: Catalog,
    capability: str,
    inputs: dict[str, Port],
    outputs: dict[str, Port],
    policy: ToolPolicy,
) -> list:
    matches = []
    for key, tool in catalog.allowed(policy).items():
        if (
            capability not in tool.capabilities
            or inputs.keys() != tool.inputs.keys()
            or outputs.keys() != tool.outputs.keys()
        ):
            continue
        if not all(compatible(inputs[name], port) for name, port in tool.inputs.items()):
            continue
        if not all(compatible(tool.outputs[name], port) for name, port in outputs.items()):
            continue
        item = catalog.get(tool.tool_id, tool.version)
        rank = (
            item["trust_status"] != "builtin",
            tool.side_effects == "external",
            tool.cost_micro_usd,
            key,
        )
        matches.append((rank, tool))
    return [tool for _, tool in sorted(matches, key=lambda pair: pair[0])]
