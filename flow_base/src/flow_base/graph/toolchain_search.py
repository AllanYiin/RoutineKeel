"""Content-addressed checkpoints for the search owned by a completion work item."""

import re

from ..outputs import reject_links
from ..runtime import atomic_json, digest, read_json
from .toolchain_plan import search_toolchains

SEARCH_POLICY = "toolchain-search/1"
SEARCH_SLICE = 512


class PlanningSearch:
    def __init__(self, runtime, previous, *, advance=False, selections=None, suggestions=None):
        self.root = runtime.home / "toolchain_searches"
        self.previous = previous
        self.advance = advance
        self.selections = selections or {}
        self.suggestions = suggestions or {}
        self.references = {}

    def __call__(self, node, task, rule, operations, observation, *, offline):
        scope = digest(
            [
                SEARCH_POLICY,
                SEARCH_SLICE,
                node,
                task.model_dump(mode="json"),
                rule.model_dump(mode="json"),
                observation["id"],
                offline,
                sorted(
                    (item.adapter.ref, item.proof, item.binding["fingerprint"])
                    for item in operations
                ),
            ]
        )
        previous_id = self.references.get(node) or self.previous.get(node)
        previous = self.read(previous_id, scope)
        if previous and (not self.advance or previous["checkpoint"] is None):
            self.references[node] = previous_id
            return self.result(node, previous["result"], previous_id)
        result = search_toolchains(
            task,
            rule,
            operations,
            offline=offline,
            maximum_states=SEARCH_SLICE,
            checkpoint=previous["checkpoint"] if previous else None,
            capture_checkpoint=True,
        )
        checkpoint = result.pop("checkpoint", None)
        body = {"scope": scope, "result": result, "checkpoint": checkpoint}
        reference = digest(body)
        path = self.root / (reference + ".json")
        reject_links(path)
        if not path.is_file():
            atomic_json(path, body)
        self.references[node] = reference
        return self.result(node, result, reference)

    def result(self, node, result, reference):
        plans = result["plans"]
        selected = self.selections.get(node, {}).get("toolchain_plan")
        for suggested in [*self.suggestions.get(node, []), *([selected] if selected else [])]:
            if all(plan["plan_id"] != suggested["plan_id"] for plan in plans):
                plans = [*plans, suggested]
        return {**result, "plans": plans, "search_id": reference}

    def read(self, reference, scope):
        if not isinstance(reference, str) or not re.fullmatch("[0-9a-f]{64}", reference):
            return None
        path = self.root / (reference + ".json")
        reject_links(path)
        if not path.is_file():
            return None
        try:
            body = read_json(path)
            if digest(body) == reference and body["scope"] == scope:
                return body
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None
