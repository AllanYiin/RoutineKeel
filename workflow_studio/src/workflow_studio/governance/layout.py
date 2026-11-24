"""Resolve installed packages without changing workflow identities or release records."""

from pathlib import Path


def workflow_folder(root, workflow_id):
    root = Path(root)
    if (root / "workflows").is_dir():
        return "workflows/FLOW_" + workflow_id
    return workflow_id
