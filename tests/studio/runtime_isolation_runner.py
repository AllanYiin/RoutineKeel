"""Execute a published workflow 100 times using only the public flow_base API."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from flow_base.core.service import WorkflowService
from flow_base.core.types import PlanRequest, StartRequest
from flow_base.runtime import FlowError


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    root, output_file = Path(sys.argv[1]), Path(sys.argv[2])
    service = WorkflowService(root)
    task = "copy-example"
    source = root / "same-input-name.txt"
    source.write_bytes(b"prepare only")
    plan = service.plan_workflow(
        PlanRequest(task=task, source=source, output=root / "slot-0", offline=True)
    )
    assert plan.plan["ready_to_prepare"]
    assert service.prepare_workflow(task, offline=True)["ok"]
    assert service.verify_workflow(task)["ok"]
    entries, corpus, refused_reuse = [], hashlib.sha256(), 0
    start = time.perf_counter()
    for index in range(100):
        content = (f"case-{index:03d}|固定隔離資料 {index * 31}\n" * (index % 7 + 1)).encode()
        corpus.update(content)
        expected_hash = hashlib.sha256(content).hexdigest()
        source.write_bytes(content)
        output = root / f"slot-{index}"
        if index == 1:
            occupied = root / "slot-0"
            before = {
                p.relative_to(occupied).as_posix(): p.read_bytes()
                for p in occupied.rglob("*")
                if p.is_file()
            }
            try:
                service.run_workflow_sync(
                    StartRequest(task=task, source=source, output=occupied, offline=True)
                )
            except FlowError as exc:
                assert exc.code == "OUTPUT_EXISTS"
                refused_reuse += 1
            else:
                raise AssertionError("A different input must not overwrite an existing job")
            after = {
                p.relative_to(occupied).as_posix(): p.read_bytes()
                for p in occupied.rglob("*")
                if p.is_file()
            }
            assert after == before
        report = service.run_workflow_sync(
            StartRequest(task=task, source=source, output=output, offline=True)
        )
        assert report["status"] == "complete"
        assert report["token_usage"]["registered_requests"] == 0
        assert all(step["status"] == "executed" for step in report["steps"])
        primary = output / report["named_outputs"]["result"]["primary"]
        record = json.loads(primary.read_text(encoding="utf-8"))["result"]
        assert (primary.parent / record["path"]).read_bytes() == content
        relative = (primary.parent / record["path"]).relative_to(output).as_posix()
        artifacts = service.list_run_artifacts(report["run_id"])
        item = next(a for a in artifacts if a["path"] == relative)
        assert item["sha256"] == expected_hash
        events = service.list_run_events(report["run_id"])["events"]
        assert {"run.started", "step.started", "delivery.published", "run.finished"} <= {
            e["name"] for e in events
        }
        entries.append(
            {
                "case_index": index,
                "run_id": report["run_id"],
                "delivery_id": item["delivery_id"],
                "artifact_path": relative,
                "input_sha256": expected_hash,
                "actual_sha256": item["sha256"],
                "bytes": len(content),
                "output_slot": index,
            }
        )
        progress = output_file.with_suffix(".progress.json")
        temporary = progress.with_suffix(".tmp")
        temporary.write_text(json.dumps({"completed": index + 1, "target": 100}), encoding="utf-8")
        os.replace(temporary, progress)
    # Validate all previous delivered versions again after completing the whole sequence.
    for entry in entries:
        artifacts = service.list_run_artifacts(entry["run_id"])
        item = next(a for a in artifacts if a["path"] == entry["artifact_path"])
        assert (
            item["sha256"] == entry["input_sha256"] and item["delivery_id"] == entry["delivery_id"]
        )
    assert len({e["run_id"] for e in entries}) == 100
    assert len({e["delivery_id"] for e in entries}) == 100
    assert len({e["input_sha256"] for e in entries}) == 100
    output_file.write_text(
        json.dumps(
            {
                "runs": 100,
                "output_slots": 100,
                "occupied_output_reuse_refused": refused_reuse,
                "retained_versions_verified": 100,
                "corpus_sha256": corpus.hexdigest(),
                "registered_model_calls": 0,
                "elapsed_seconds": time.perf_counter() - start,
                "mixed_outputs": 0,
                "duplicate_run_ids": 0,
                "entries": entries,
                "passed": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
