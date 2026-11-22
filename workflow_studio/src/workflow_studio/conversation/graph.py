"""In-memory graph edits for host conversations."""

import copy

from ..contracts import StudioError
from ..ir.references import parse_reference


class GraphDraft:
    def __init__(self, ir):
        self.original = copy.deepcopy(ir)
        self.ir = copy.deepcopy(ir)

    def node(self, identity):
        found = next((n for n in self.ir["nodes"] if n["id"] == identity), None)
        if found is None:
            raise StudioError("INPUT_INVALID", "找不到選取的步驟。")
        return found

    def next_id(self):
        used = {n["id"] for n in self.ir["nodes"] + self.original["nodes"]}
        return next(f"step_{i}" for i in range(1, 1000) if f"step_{i}" not in used)

    def references(self, exclude=None):
        return [f"input://{k}" for k in self.ir["inputs"]] + [
            f"artifact://{node['id']}/{name}"
            for node in self.ir["nodes"]
            if node["id"] != exclude
            for name in node["outputs"]
        ]

    def reconnect(self, identity, source, dependencies, path):
        node = self.node(identity)
        old = node["inputs"]["source"]["source"]
        node["inputs"]["source"]["source"] = source
        for condition in node["preconditions"] + node["postconditions"]:
            if condition["reference"] == old:
                condition["reference"] = source
        ref = parse_reference(source)
        node["depends_on"] = sorted(
            set(dependencies) | ({ref.owner} if ref.scheme == "artifact" else set())
        )
        node["outputs"]["result"]["path"] = path

    def duplicate(self, identity):
        node = copy.deepcopy(self.node(identity))
        new_id = self.next_id()
        node["id"] = new_id
        for condition in node["preconditions"] + node["postconditions"]:
            prefix = f"artifact://{identity}/"
            if condition["reference"].startswith(prefix):
                condition["reference"] = (
                    f"artifact://{new_id}/" + condition["reference"][len(prefix) :]
                )
        self.ir["nodes"].append(node)
        return node

    def changes(self):
        return {
            "upsert_nodes": copy.deepcopy(self.ir["nodes"]),
            "delete_nodes": sorted(
                {n["id"] for n in self.original["nodes"]} - {n["id"] for n in self.ir["nodes"]}
            ),
            "inputs": copy.deepcopy(self.ir["inputs"]),
            "outputs": copy.deepcopy(self.ir["outputs"]),
            "secrets": copy.deepcopy(self.ir["secrets"]),
        }
