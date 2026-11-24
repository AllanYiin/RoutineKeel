"""Run a recorded completion trial in an independently resumable local worker."""

import argparse
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime

from ..evolution.comparison import case_content
from ..execution.process import worker_environment
from ..execution.supervisor import process_started_at
from ..outputs import reject_links
from ..runtime import FlowError, digest, read_json
from . import installer
from .completion_store import CompletionStore

RUNNING = {"trial_queued", "trialing", "installing"}


def trial_key(service, state):
    return digest(
        [
            state["draft_id"],
            state["capability_fingerprint"],
            state["intent"],
            state["offline"],
            case_content(
                service.workspace.root,
                [state["case_coverage"], state.get("legacy_recipe")],
            ),
        ]
    )


def worker_alive(state):
    worker = state.get("trial", {})
    pid, started_at = worker.get("pid"), worker.get("started_at")
    return bool(
        type(pid) is int
        and started_at is not None
        and process_started_at(pid) == started_at
    )


def trial_progress(service, state):
    directory = (
        installer.draft_dir(service.workspace, state["draft_id"])
        / "builds"
        / state["trial"]["build_id"]
    )
    progress = directory / "trial-progress.json"
    reject_links(progress)
    try:
        snapshot = read_json(progress)
    except FileNotFoundError:
        snapshot = {}
    except PermissionError:
        snapshot = {"progress_available": False}
    return {**snapshot, "worker_alive": worker_alive(state)}


def queue_trial(service, state, record):
    if state["status"] not in {"ready_for_trial", *RUNNING}:
        return state
    pending = state.get("trial", {})
    key = trial_key(service, state)
    if pending.get("key") == key and not pending.get("outcome") and worker_alive(state):
        return {**state, "status": "trialing"}
    if (
        pending.get("key") == key
        and pending.get("outcome") == "verified"
        and state["intent"] == "build"
    ):
        return {**state, "status": "verified"}
    if pending.get("key") != key or pending.get("outcome") == "failed":
        pending = {"key": key, "build_id": uuid.uuid4().hex, "launch_attempts": 0}
        state = {
            key: value
            for key, value in state.items()
            if key not in {"candidate", "update_plan"}
        }
    if pending["launch_attempts"] >= 3:
        from .completion import _preflight

        return _preflight(
            service,
            {
                **state,
                "trial": {
                    **pending,
                    "outcome": "failed",
                    "reason": "本機試做程序多次中斷，請檢查環境再重試。",
                },
            },
        )
    pending = {**pending, "launch_attempts": pending["launch_attempts"] + 1}
    queued = record({**state, "status": "trial_queued", "trial": pending})
    runtime = service.workspace.runtime
    logs = runtime.logs / "blueprint-completion" / queued["work_id"]
    reject_links(logs)
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / (pending["build_id"] + ".log")
    reject_links(log)
    env = worker_environment()
    for name in ("ROUTINEKEEL_HOST_CATALOG", "ROUTINEKEEL_STUDIO_PYTHON"):
        if value := os.environ.get(name):
            env[name] = value
    kwargs = (
        {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        with log.open("ab") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    "utf8",
                    "-m",
                    "flow_base.blueprints.completion_delivery",
                    str(service.workspace.root),
                    queued["work_id"],
                    pending["build_id"],
                ],
                cwd=service.workspace.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **kwargs,
            )
    except OSError as exc:
        from .completion import _preflight

        return _preflight(
            service,
            {
                **queued,
                "status": "needs_input",
                "trial": {
                    **pending,
                    "outcome": "failed",
                    "reason": "無法啟動本機試做程序。",
                    "code": type(exc).__name__,
                },
            },
        )
    return {
        **queued,
        "status": "trialing",
        "trial": {
            **pending,
            "pid": process.pid,
            "started_at": process_started_at(process.pid),
        },
    }


def _record(store, current, **changes):
    revision = current["revision"] + 1
    state = {
        **current,
        **changes,
        "revision": revision,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return store.save(
        state, f"internal:{revision}", digest([current["trial"]["build_id"], revision])
    )


def _candidate(service, state):
    trial = state["trial"]
    key, build_id = state["draft_id"], trial["build_id"]
    if trial_key(service, state) != trial["key"]:
        raise FlowError("REVISION_CONFLICT", "試做素材已變更，請依目前版本重驗。")
    directory = installer.draft_dir(service.workspace, key) / "builds" / build_id
    read = directory / "verification.json"
    reject_links(read)
    if read.is_file():
        candidate = read_json(read)
        if candidate["status"] == "verified":
            installer.checked_candidate(
                service.workspace, directory, candidate["install_hash"]
            )
            return candidate
        raise FlowError(
            "INVALID_CONFIGURATION", candidate.get("reason", "候選試做未通過。")
        )
    legacy = state.get("legacy_recipe")
    resolution = (
        legacy["resolution"]
        if legacy
        else {"capability_fingerprint": state["capability_fingerprint"]}
    )
    return installer.verify(
        service.workspace,
        key,
        resolution,
        legacy["cases"] if legacy else None,
        build_id=build_id,
        coverage=None if legacy else state["case_coverage"],
        offline=state["offline"],
    )


def _explain_failure(service, state, exc):
    directory = (
        installer.draft_dir(service.workspace, state["draft_id"])
        / "builds"
        / state["trial"]["build_id"]
    )
    evidence_path = directory / "trial-failure.json"
    reject_links(evidence_path)
    evidence = read_json(evidence_path) if evidence_path.is_file() else {}
    return {
        "outcome": "failed",
        "code": evidence.get("code", getattr(exc, "code", type(exc).__name__)),
        "reason": evidence.get(
            "error", getattr(exc, "details", {}).get("reason", str(exc))
        )[:1000],
        **{key: evidence[key] for key in ("case", "phase") if key in evidence},
    }


def _deliver(service, store, state, candidate):
    trial = {**state["trial"], "outcome": "verified"}
    status = "verified" if state["intent"] == "build" else "installing"
    state = _record(store, state, status=status, candidate=candidate, trial=trial)
    if state["intent"] == "build":
        return
    try:
        result = service.install_workflow(
            draft_id=state["draft_id"],
            build_id=candidate["build_id"],
            install_hash=candidate["install_hash"],
        )
        if result["status"] == "update_required":
            from .completion_update import update_details

            plan = service.preview_workflow_update(
                candidate["archive"], task=state["workflow_id"]
            )
            _record(
                store,
                state,
                status="needs_update",
                update_plan=plan,
                update=update_details(service, state, plan),
            )
        else:
            _record(store, state, status="installed", installation=result)
    except (FlowError, OSError) as exc:
        _fail(service, store, state, exc, phase="install")


def _fail(service, store, state, exc, **details):
    from .completion import _preflight

    trial = {**state["trial"], **_explain_failure(service, state, exc), **details}
    derived = _preflight(service, {**state, "trial": trial})
    if derived["status"] == "ready_for_trial":
        changes = {
            key: value
            for key, value in derived.items()
            if key not in {"revision", "updated_at"}
        }
        retries = state.get("environment_retries", 0)
        if retries < 3:

            def save_checkpoint(snapshot):
                current = store.read(state["work_id"])
                delta = {
                    key: value
                    for key, value in snapshot.items()
                    if key not in {"revision", "updated_at"}
                }
                return _record(store, current, **delta)

            restarted = queue_trial(
                service,
                {**changes, "environment_retries": retries + 1},
                save_checkpoint,
            )
            save_checkpoint(restarted)
            return
        derived = _preflight(
            service,
            {
                **changes,
                "trial": {
                    **trial,
                    "key": trial_key(service, derived),
                    "reason": "建置期間目標環境反覆變動，請核對後重驗。",
                },
            },
        )
    state_changes = {
        key: value
        for key, value in derived.items()
        if key not in {"revision", "updated_at"}
    }
    _record(store, state, **state_changes)


def run_trial(service, work_id, build_id):
    runtime = service.workspace.runtime
    store = CompletionStore(runtime)
    lock = "blueprint-completion:" + work_id
    with runtime.lock("blueprint-trial:" + build_id, timeout=0):
        with runtime.lock(lock, timeout=30):
            state = store.read(work_id)
            if (
                state.get("trial", {}).get("build_id") != build_id
                or state["status"] not in RUNNING
            ):
                return
        try:
            candidate = _candidate(service, state)
        except (FlowError, OSError, ValueError) as exc:
            with runtime.lock(lock, timeout=30):
                current = store.read(work_id)
                if (
                    current["status"] in RUNNING
                    and current["trial"]["build_id"] == build_id
                ):
                    _fail(service, store, current, exc)
            return
        with runtime.lock(lock, timeout=30):
            current = store.read(work_id)
            if (
                current["status"] in RUNNING
                and current["trial"]["build_id"] == build_id
            ):
                _deliver(service, store, current, candidate)


def main():
    from ..core.service import WorkflowService

    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("work_id")
    parser.add_argument("build_id")
    args = parser.parse_args()
    if not re.fullmatch(r"completion_[0-9a-f]{32}", args.work_id) or not re.fullmatch(
        r"[0-9a-f]{32}", args.build_id
    ):
        parser.error("invalid completion work or build id")
    try:
        run_trial(WorkflowService(args.workspace), args.work_id, args.build_id)
    except FlowError as exc:
        if exc.code != "BUSY":
            raise


if __name__ == "__main__":
    main()
