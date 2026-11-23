"""Read verified Graph data from an installed wheel without importing its adapter."""

import hashlib
import json
import zipfile

from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.package import verify_package

from ..blueprints.adapter import ADAPTER_SOURCE
from ..runtime import FlowError, digest, sha256
from ..workspace import Workspace
from .source import inspect_graph


def installed_graph(workspace, workflow_ref):
    workflow_id, separator, version = workflow_ref.partition("@")
    if not separator or not workflow_id or not version:
        raise FlowError("INVALID_CONFIGURATION", "擷取來源需要明確的 workflow_id@version。")
    task = Workspace(workspace).task(workflow_id, read_only=True)
    manifest = task.manifest
    if manifest.id != workflow_id or manifest.version != version:
        raise FlowError("CONTRACT_MISMATCH", "目前安裝的工作流識別碼或版本與擷取來源不同。")
    module = manifest.python_module
    path = task.folder / manifest.package
    if module != f"rk_bp_{workflow_id}":
        raise FlowError("GRAPH_UNSUPPORTED", "此版本沒有可擷取的已安裝 Graph v2。")
    if not path.is_file() or path.is_symlink() or sha256(path) != manifest.sha256:
        raise FlowError("TASK_INTEGRITY", "已安裝流程包的完整性檢查未通過。")
    try:
        with zipfile.ZipFile(path) as wheel:
            entries = wheel.infolist()
            size = sum(entry.file_size for entry in entries)
            if len(entries) > 3000 or size > 32 * 1024**2:
                raise FlowError("TASK_INTEGRITY", "已安裝流程包超過圖形讀取限制。")
            verify_package(task.folder)
            if wheel.read(f"{module}/adapter.py") != ADAPTER_SOURCE:
                raise FlowError("TASK_INTEGRITY", "已安裝流程包沒有受控的 Graph Adapter。")
            plan = json.loads(wheel.read(f"{module}/plan.json"))
            source = wheel.read(f"{module}/source.blueprint")
            bindings = json.loads(wheel.read(f"{module}/tool-signatures.json"))["bindings"]
            if (
                plan["format"] != 2
                or plan["workflow"] != workflow_id
                or plan["version"] != version
                or plan["graph_hash"] != digest(plan["graph"])
                or plan["source_hash"] != hashlib.sha256(source).hexdigest()
                or plan["bindings_hash"] != digest(bindings)
            ):
                raise FlowError("CONTRACT_MISMATCH", "已安裝 Graph、來源或綁定指紋不一致。")
    except (StudioError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise FlowError("TASK_INTEGRITY", "無法安全讀取已安裝 Graph。") from exc
    graph, _ = inspect_graph(plan["graph"])
    if (graph["workflow"]["id"], graph["workflow"]["version"]) != (workflow_id, version):
        raise FlowError("CONTRACT_MISMATCH", "已安裝 Graph 的來源版本不符。")
    return {
        "graph": graph,
        "blueprint_sha256": plan["source_hash"],
        "wheel_sha256": manifest.sha256,
        "dependency_evidence": {
            node: {"provider": binding.get("provider"), "fingerprint": digest(binding)}
            for node, binding in bindings.items()
        },
    }
