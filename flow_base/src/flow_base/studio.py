"""Bridge to the operator's Studio installation, independent of the data workspace."""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread

from routinekeel_contracts.domain_assets import content_hash

from .execution.process import worker_environment
from .runtime import FlowError

_studio_bridge = ContextVar("studio_bridge", default=None)


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


def _studio_command(workspace):
    return [
        str(studio_python()), "-I", "-X", "utf8", "-m", "workflow_studio",
        "--workspace", str(workspace), "--runtime-python", sys.executable,
    ]


def _exchange_requests(process, requests):
    ready = False
    with process.stdin, process.stdout:
        while (request := requests.get()) is not None:
            payload, reply = request
            try:
                if not ready:
                    greeting = process.stdout.readline()
                    # Older Studio CLIs reject the bridge command before reading a request.
                    if not greeting and process.wait() == 2:
                        reply.put(None)
                        return
                    if json.loads(greeting) != {"protocol": 1}:
                        raise ValueError("Unsupported Studio bridge protocol")
                    ready = True
                process.stdin.write(payload)
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                    raise ValueError("Invalid Studio bridge response")
                reply.put(response)
            except (OSError, ValueError) as exc:
                reply.put(exc)
                return


class StudioBridge:
    """Own one lazily started Studio subprocess for an MCP connection."""

    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self._lock = Lock()
        self._process = None
        self._legacy = False

    @contextmanager
    def activate(self):
        token = _studio_bridge.set(self)
        try:
            yield
        finally:
            _studio_bridge.reset(token)

    def _start(self):
        self._requests = Queue()
        self._process = subprocess.Popen(
            [*_studio_command(self.workspace), "bridge"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", env=worker_environment(),
        )
        self._thread = Thread(
            target=_exchange_requests, args=(self._process, self._requests), daemon=True
        )
        self._thread.start()

    def _stop(self, *, force=False):
        process = self._process
        if process is None:
            return
        self._process = None
        self._requests.put(None)
        if force and process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        self._thread.join(timeout=2)

    def close(self):
        with self._lock:
            self._stop()

    def response(self, operation, payload, timeout):
        with self._lock:
            if self._legacy:
                return None
            if self._process is not None and self._process.poll() is not None:
                self._stop()
            if self._process is None:
                try:
                    self._start()
                except OSError as exc:
                    raise FlowError("STUDIO_UNAVAILABLE", "無法啟動本機 Studio。") from exc
            reply = Queue(maxsize=1)
            self._requests.put((json.dumps([operation, payload]) + "\n", reply))
            try:
                response = reply.get(timeout=timeout)
            except Empty as exc:
                self._stop(force=True)
                raise subprocess.TimeoutExpired(operation, timeout) from exc
            if isinstance(response, Exception):
                self._stop(force=True)
                raise FlowError("STUDIO_UNAVAILABLE", "Studio 未能回報結果，請檢查本機安裝。") from response
            if response is None:
                self._stop()
                self._legacy = True
            return response


def design_preflight(proposal, workspace):
    from .graph.builder import preflight_graph

    graph = proposal["graph"]
    if "design_review" in proposal:
        graph = {
            **graph,
            "provenance": {
                **graph["provenance"],
                "design_review": {
                    "details": proposal["design_review"],
                    "sha256": proposal["review_hash"],
                },
                "skill_files": proposal["skill_files"],
                "skill_sources": proposal["skill_sources"],
            },
        }
    readiness = preflight_graph(graph, workspace)
    result = {
        "status": readiness["build_status"],
        "blocks_blueprint_export": False,
        "capability_gaps": readiness["gaps"],
        "build_gaps": readiness["build_gaps"],
    }
    lines = ["\n\n### 建置階段預檢（目前環境）"]
    if result["status"] == "ready":
        lines.append("預檢通過。候選套件建置後，仍須載入臨時工作區實跑驗收案例。")
    else:
        lines.append(
            "目前環境的建置預檢有待處理項目。請照常校閱和匯出已確認的藍圖，"
            "再於目標工作區解析工具或補齊建置案例，通過後才能試做候選包。"
        )
        for gap in (*result["capability_gaps"], *result["build_gaps"]):
            context = gap.get("node") or (f"案例 {gap['case']}" if "case" in gap else "建置")
            lines.append(f"- {context}｜{gap['reason']}")
    proposal["build_preflight"] = result
    proposal["build_preflight_guidance"] = {
        "scope": "candidate_trial",
        "completion_tool": "blueprint_completion",
        "message": "依目前定稿與確認狀態繼續設計。藍圖匯出後由底座接手補強與試做。",
    }
    proposal["display_text"] += "\n".join(lines)


def studio_request(workspace, operation, params):
    evidence_environment = {}
    if operation == "extract_pattern":
        from .graph.installed import installed_graph

        if "source" in params or not isinstance(params.get("workflow_ref"), str):
            raise FlowError("INVALID_CONFIGURATION", "擷取請指定已安裝的 workflow_id@version。")
        params = {**params, "source": installed_graph(workspace, params["workflow_ref"])}
    if operation in {"propose_pattern_improvement", "compare_pattern_improvement"}:
        from .evolution import patterns

        if "evidence" in params or "report" in params:
            raise FlowError("INVALID_CONFIGURATION", "Pattern 改善證據由本機 Runtime 取得。")
        params = dict(params)
        if operation == "propose_pattern_improvement":
            params["evidence"] = patterns.observe(
                workspace,
                params["source_ref"],
                params.pop("feedback_ids"),
                planned=bool(params.get("planned_reason")),
            )
        else:
            state = studio_request(
                workspace, "get_pattern_improvement", {"proposal_id": params["proposal_id"]},
            )
            if params["expected_revision"] != state["revision"]:
                raise FlowError("REVISION_CONFLICT", "改善提案版本已改變。")
            params["report"] = patterns.compare(
                workspace, state, params.pop("candidate_drafts"), params.pop("negative_cases"),
                params.pop("limits", {}),
            )
        evidence_environment["ROUTINEKEEL_STUDIO_EVIDENCE_SHA256"] = content_hash(params)
    payload = json.dumps(params)
    timeout = 350 if operation == "qualify_pattern" else 45
    bridge = _studio_bridge.get()
    try:
        # Runtime evidence is passed in a per-request environment for these operations.
        response = None
        if bridge is not None and bridge.workspace == Path(workspace) and not evidence_environment:
            response = bridge.response(operation, payload, timeout)
        returncode = 0
        if response is None:
            proc = subprocess.run(
                [*_studio_command(workspace), "interview", operation, "--payload", "-"],
                input=payload, encoding="utf-8", capture_output=True, timeout=timeout,
                env=worker_environment() | evidence_environment, check=False,
            )
            returncode = proc.returncode
            response = json.loads(proc.stdout)
        if not response["ok"]:
            raise FlowError(response["error"]["code"], response["error"]["message"])
        if returncode == 0:
            result = response["result"]
            if operation in {"prepare_blueprint", "finalize_workflow_design"}:
                design_preflight(result, workspace)
            if operation in {"get", "status"} and result.get("design_progress", {}).get(
                "stage"
            ) in {"draft_review_required", "decisions_required", "final_review_required"}:
                design_preflight(result["blueprint_proposal"], workspace)
            return result
    except subprocess.TimeoutExpired as exc:
        raise FlowError(
            "STUDIO_TIMEOUT", "Studio 回覆逾時；請先查詢目前版本，再重送相同請求。"
        ) from exc
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
