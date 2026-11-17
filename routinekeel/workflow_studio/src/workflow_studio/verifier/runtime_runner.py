"""Run only the already-verified generated package, through the public Runtime API."""

import json
import sys
from pathlib import Path

from flow_base.core.service import WorkflowService
from flow_base.core.types import PlanRequest, StartRequest


def main():
    root = Path(sys.argv[1]).resolve()
    service = WorkflowService(root)
    request = PlanRequest(
        task="generated", source=root / "input.bin", output=root / "output", offline=True
    )
    planned = service.plan_workflow(request)
    if not planned.plan["ready_to_prepare"]:
        raise RuntimeError("Generated plan was blocked")
    service.prepare_workflow("generated", offline=True)
    if not service.verify_workflow("generated")["ok"]:
        raise RuntimeError("Runtime verification failed")
    result = service.run_workflow_sync(StartRequest.model_validate(request.model_dump()))
    result["lifecycle"] = [
        e["name"] for e in service.list_run_events(result["run_id"], limit=1000)["events"]
    ]
    (root / "runtime-result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
