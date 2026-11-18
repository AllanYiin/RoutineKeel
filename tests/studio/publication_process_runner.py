"""Abruptly exit this test-owned publisher; no finally/rollback code may run."""

import os
import sys
from pathlib import Path

from workflow_studio.governance.release import Releases
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService


def main():
    import workflow_studio.governance.release as module

    workspace, project_id, approval_id, root, previous_id, build_id, point = sys.argv[1:]
    assert point in {"before_manifest", "after_manifest", "after_release_update"}
    root = Path(root)
    studio = StudioService.open_workspace(workspace)
    write = module.atomic_bytes

    def crash():
        write(root / "crash-marker.txt", point.encode())
        os._exit(97)

    if point == "after_release_update":
        studio.repository.connection.create_function("test_publisher_crash", 0, crash)
        studio.repository.connection.execute(
            "CREATE TRIGGER crash_publication AFTER UPDATE ON studio_releases WHEN NEW.build_id='"
            + build_id
            + "' AND NEW.status='published' BEGIN SELECT test_publisher_crash(); END"
        )

    def interrupted_write(path, content):
        manifest = path == root / "copy-example/flow.json"
        if manifest and point == "before_manifest":
            crash()
        write(path, content)
        if manifest and point == "after_manifest":
            crash()

    module.atomic_bytes = interrupted_write
    Releases(studio).publish(
        project_id,
        approval_id,
        root,
        ToolPolicy("windows" if os.name == "nt" else "linux"),
        expected_current=previous_id,
    )
    raise AssertionError("The controlled process stop was not reached")


if __name__ == "__main__":
    main()
