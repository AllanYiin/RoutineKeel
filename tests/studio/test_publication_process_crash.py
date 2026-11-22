import os
import subprocess
import sys
from pathlib import Path

import pytest
from compiler_helpers import execute_package
from gate_evidence import write_evidence
from test_edge_cases import candidate_after_publication, tree_bytes, tree_hash
from workflow_studio.governance.recovery import ReleaseRecovery
from workflow_studio.governance.release import Releases


@pytest.mark.parametrize("point", ["before_manifest", "after_manifest", "after_release_update"])
def test_publisher_process_death_keeps_previous_release_recoverable(tmp_path, point):
    studio, p, root, first, build, approval, policy, _ = candidate_after_publication(tmp_path)
    try:
        snapshot = root / ".studio_releases/copy-example" / first["release_id"]
        old_snapshot = tree_bytes(snapshot)
        old_output = tree_bytes(root / "copy-example/outputs")
        old_manifest = (root / "copy-example/flow.json").read_bytes()
        runner = Path(__file__).with_name("publication_process_runner.py")
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "PATH",
                "PATHEXT",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
                "LANG",
                "LC_ALL",
            }
        }
        process = subprocess.run(
            [
                sys.executable,
                "-I",
                str(runner),
                str(tmp_path / "studio"),
                p.project_id,
                approval["approval_id"],
                str(root),
                first["release_id"],
                build["build_id"],
                point,
            ],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert process.returncode == 97, process.stderr.decode("utf-8", errors="replace")
        assert (root / "crash-marker.txt").read_text() == point
        studio.repository.connection.execute("DROP TRIGGER IF EXISTS crash_publication")
        assert tree_bytes(snapshot) == old_snapshot
        assert tree_bytes(root / "copy-example/outputs") == old_output
        pending = [
            r for r in Releases(studio).history(p.project_id) if r["build_id"] == build["build_id"]
        ]
        assert len(pending) == 1 and pending[0]["status"] == "prepared"
        recovery = ReleaseRecovery(studio)
        observed = recovery.inspect(p.project_id, pending[0]["release_id"])
        expected = "not_applied" if point == "before_manifest" else "applied"
        assert observed["outcome"] == expected
        recovery.reconcile(
            p.project_id,
            pending[0]["release_id"],
            observed["observation_hash"],
            actor="crash-test",
            reason="reconcile after process exit 97",
        )
        if expected == "applied":
            Releases(studio).rollback(p.project_id, first["release_id"], policy)
        assert (root / "copy-example/flow.json").read_bytes() == old_manifest
        assert tree_bytes(snapshot) == old_snapshot
        assert tree_bytes(root / "copy-example/outputs") == old_output
        result = execute_package(
            root / "copy-example", tmp_path / "runtime-probe", b"still valid after process death"
        )
        assert result["status"] == "complete"
        assert (
            tmp_path / "runtime-probe/output/artifacts/result/artifacts/result.txt"
        ).read_bytes() == b"still valid after process death"
        assert studio.repository.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        write_evidence(
            tmp_path,
            "publication-crash-" + point,
            {
                "fault_point": point,
                "child_exit_code": process.returncode,
                "normal_cleanup_ran": False,
                "recovery_outcome": observed["outcome"],
                "snapshot_before": tree_hash(old_snapshot),
                "snapshot_after": tree_hash(tree_bytes(snapshot)),
                "previous_version_replayed": True,
                "passed": True,
            },
        )
    finally:
        studio.close()
