"""Bridge to the operator's Studio installation, independent of the data workspace."""

import json
import os
import subprocess
import sys
from pathlib import Path

from .execution.process import worker_environment
from .runtime import FlowError


def studio_python():
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    explicit = os.environ.get("ROUTINEKEEL_STUDIO_PYTHON")
    if explicit:
        candidate = Path(explicit)
        if candidate.is_absolute() and candidate.is_file():
            return candidate
        raise FlowError("DEPENDENCY_UNAVAILABLE", "設定的 Studio Python 路徑不存在。")
    root = Path(sys.prefix).resolve().parent
    candidates = [root / "workflow_studio/.studio-venv", root / ".studio-venv"]
    source = Path(__file__).resolve()
    if source.parent.parent.name == "src" and source.parents[2].name == "flow_base":
        candidates.append(source.parents[3] / "workflow_studio/.venv")
    for env in candidates:
        if (env / executable).is_file():
            return env / executable
    raise FlowError(
        "DEPENDENCY_UNAVAILABLE",
        "請先由發行包的 Studio 啟動器準備環境，或指定 ROUTINEKEEL_STUDIO_PYTHON。",
    )


def studio_request(workspace, operation, params):
    if operation == "extract_pattern":
        from .graph.installed import installed_graph

        if "source" in params or not isinstance(params.get("workflow_ref"), str):
            raise FlowError("INVALID_CONFIGURATION", "擷取請指定已安裝的 workflow_id@version。")
        params = {**params, "source": installed_graph(workspace, params["workflow_ref"])}
    args = [
        str(studio_python()),
        "-I",
        "-X",
        "utf8",
        "-m",
        "workflow_studio",
        "--workspace",
        str(workspace),
        "--runtime-python",
        sys.executable,
        "interview",
        operation,
        "--payload",
        "-",
    ]
    try:
        proc = subprocess.run(
            args,
            input=json.dumps(params),
            encoding="utf-8",
            capture_output=True,
            timeout=350 if operation == "qualify_pattern" else 45,
            env=worker_environment(), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FlowError(
            "STUDIO_TIMEOUT", "Studio 回覆逾時；請先查詢目前版本，再重送相同請求。"
        ) from exc
    try:
        response = json.loads(proc.stdout)
        if not response["ok"]:
            raise FlowError(response["error"]["code"], response["error"]["message"])
        if proc.returncode == 0:
            result = response["result"]
            if operation == "prepare_blueprint":
                from .graph.builder import preflight_graph

                readiness = preflight_graph(result["graph"], workspace)
                result["build_preflight"] = {
                    "status": readiness["build_status"],
                    "capability_gaps": readiness["gaps"],
                    "build_gaps": readiness["build_gaps"],
                }
            return result
    except (ValueError, KeyError, TypeError):
        pass
    raise FlowError("STUDIO_UNAVAILABLE", "Studio 未能回報結果，請檢查本機安裝。")


def studio_terminal(workspace, params, *, operation="stream"):
    args = [
        str(studio_python()),
        "-I",
        "-X",
        "utf8",
        "-m",
        "workflow_studio",
        "--workspace",
        str(workspace),
        "interview",
        operation,
        "--payload",
        json.dumps(params),
    ]
    return subprocess.run(args, env=worker_environment(), check=False).returncode
