"""Read-only revision comparisons and explicit copy-forward edits."""

import copy

from ..contracts import StudioError
from ..llm.streaming import EventStore


def differences(before, after, *, limit=500):
    changes = []
    truncated = False

    def walk(left, right, path):
        nonlocal truncated
        if not isinstance(left, (dict, list)) and type(left) is type(right) and left == right:
            return
        if len(changes) >= limit:
            truncated = True
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                child = path + [key]
                if key not in left or key not in right:
                    changes.append(
                        {
                            "path": child,
                            "operation": "added" if key not in left else "removed",
                            "before": left.get(key),
                            "after": right.get(key),
                        }
                    )
                    if len(changes) >= limit:
                        truncated = True
                        return
                else:
                    walk(left[key], right[key], child)
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                if index >= len(left) or index >= len(right):
                    if len(changes) >= limit:
                        truncated = True
                        return
                    changes.append(
                        {
                            "path": path + [str(index)],
                            "operation": "added" if index >= len(left) else "removed",
                            "before": left[index] if index < len(left) else None,
                            "after": right[index] if index < len(right) else None,
                        }
                    )
                else:
                    walk(left[index], right[index], path + [str(index)])
        else:
            changes.append({"path": path, "operation": "changed", "before": left, "after": right})

    walk(before, after, [])
    return {"changes": changes, "truncated": truncated}


class RevisionHistory:
    def __init__(self, studio):
        self.studio = studio

    def compare(self, project_id, before_id, after_id):
        before = self.studio.projects.get_revision(project_id, before_id)
        after = self.studio.projects.get_revision(project_id, after_id)
        return {
            "before_id": before_id,
            "after_id": after_id,
            "before_hash": before.ir_hash,
            "after_hash": after.ir_hash,
            **differences(before.ir, after.ir),
        }

    def reuse(self, project_id, revision_id, expected_version, policy, *, restore_goal=False):
        with self.studio.repository.transaction() as db:
            project = self.studio.projects._editable(db, project_id, expected_version)
            old = self.studio.projects.get_revision(project_id, revision_id)
            payload = copy.deepcopy(old.ir)
            if payload.get("ir_version") == "1.0" and old.status != "draft":
                if payload["goal"] != project.goal:
                    if not restore_goal:
                        raise StudioError(
                            "REVISION_CONFLICT", "舊版工作目標不同，請明確確認同時還原目標。"
                        )
                    project = self.studio.projects.update(
                        project_id, project.version, goal=payload["goal"]
                    )
                ordinals = [
                    r.ir["revision"]
                    for r in self.studio.projects.revisions(project_id, include_discarded=True)
                    if r.status != "draft" and r.ir.get("ir_version") == "1.0"
                ]
                payload["revision"] = max(ordinals, default=0) + 1
                new = self.studio.save_ir(project_id, project.version, payload, policy)
            else:
                new = self.studio.projects.add_revision(project_id, project.version, payload)
            EventStore(self.studio.repository).append(
                project_id,
                None,
                "revision.reused",
                {"source_revision_id": revision_id},
                new.revision_id,
            )
        return new

    def discard(self, project_id, revision_id, expected_version):
        with self.studio.repository.transaction():
            result = self.studio.projects.discard_revision(
                project_id, revision_id, expected_version
            )
            EventStore(self.studio.repository).append(
                project_id, None, "revision.discarded", {}, revision_id
            )
        return result
