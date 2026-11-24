"""Deliberately bounded flowchart grammar; unsupported syntax reports its line."""

import re

from ..contracts import StudioError

NODE = r'([A-Za-z][A-Za-z0-9_-]*)(?:\[(?:"([^"\n]+)"|([^\]\n]+))\]|\{([^}\n]+)\})?'
EDGE = re.compile(r"\s*(-->)\s*(?:\|([^|\n]+)\|\s*)?")
NODE_RE = re.compile(NODE)


def parse_mermaid(text: str) -> dict:
    lines = text.strip().splitlines()
    if lines and lines[0].strip() == "```mermaid" and lines[-1].strip() == "```":
        lines = lines[1:-1]
    if not lines or not re.fullmatch(
        r"(?:graph|flowchart)\s+(?:TD|TB|LR|RL|BT)\s*;?", lines[0].strip()
    ):
        raise StudioError("INPUT_INVALID", "Mermaid 第 1 行須為 flowchart／graph 與方向。")
    nodes, edges = {}, []
    for number, raw in enumerate(lines[1:], 2):
        line = raw.strip().rstrip(";")
        if not line or line.startswith("%%"):
            continue
        match = NODE_RE.match(line)
        if not match:
            raise StudioError("INPUT_INVALID", f"Mermaid 第 {number} 行語法不支援。")
        previous = match.group(1)
        nodes[previous] = next((x for x in match.groups()[1:] if x), nodes.get(previous, previous))
        position = match.end()
        while position < len(line):
            edge = EDGE.match(line, position)
            target = NODE_RE.match(line, edge.end()) if edge else None
            if target is None:
                raise StudioError(
                    "INPUT_INVALID", f"Mermaid 第 {number} 行包含不支援的連線或語法。"
                )
            identity = target.group(1)
            nodes[identity] = next(
                (x for x in target.groups()[1:] if x), nodes.get(identity, identity)
            )
            edges.append({"from": previous, "to": identity, "condition": edge.group(2)})
            previous, position = identity, target.end()
    if not nodes:
        raise StudioError("INPUT_INVALID", "流程圖沒有節點。")
    return {"nodes": nodes, "edges": edges}
