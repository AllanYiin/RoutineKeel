"""Trusted generated-package template. Data is loaded as JSON, never interpolated as code."""

import hashlib
import json
import os
import time
from pathlib import Path

from flow_base.contracts import Workflow
from flow_base.engine import Adapter as BaseAdapter
from flow_base.runtime import FlowError, atomic_json

MAX_FILE_BYTES = 10 * 1024 * 1024
PLAN = json.loads(Path(__file__).with_name("compiled.json").read_text(encoding="utf-8"))


def read_bounded(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise FlowError("INPUT_INVALID", "輸入缺少、為連結或超過此 Handler 的 10 MiB 限制")
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise FlowError("INPUT_INVALID", "輸入檔案超過限制")
    return content


def save_output(target, relative, content):
    if len(content) > MAX_FILE_BYTES:
        raise FlowError("OUTPUT_INVALID", "產物超過此 Handler 的大小限制")
    destination = target.parent / relative
    if not destination.resolve().is_relative_to(target.parent.resolve()):
        raise FlowError("OUTPUT_INVALID", "產物超出配置目錄")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    atomic_json(
        target,
        {
            "result": {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        },
    )
    return target


def load_artifact(primary):
    record = json.loads(primary.read_text(encoding="utf-8"))["result"]
    path = primary.parent / record["path"]
    if not path.resolve().is_relative_to(primary.parent.resolve()):
        raise FlowError("ARTIFACT_INVALID", "依賴產物位置不合法")
    content = read_bounded(path)
    if hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise FlowError("ARTIFACT_INVALID", "依賴產物指紋不符")
    return content


def input_handler(ctx, step, dependencies, target):
    return save_output(target, "input.bin", read_bounded(ctx.source))


def transform_handler(ctx, step, dependencies, target):
    started = time.monotonic()
    node = PLAN["operations"][step.id]
    own_output = None

    def resolve(reference):
        scheme, name = reference.split("://", 1)
        if scheme == "input":
            return load_artifact(dependencies[PLAN["input_step"]])
        owner, _ = name.split("/", 1)
        return own_output if owner == step.id else load_artifact(dependencies[owner])

    def check(condition):
        value = resolve(condition["reference"])
        operator = condition["operator"]
        ok = value is not None
        if operator == "nonempty":
            ok = bool(value)
        elif operator == "equals":
            ok = value.decode("utf-8") == condition["value"]
        elif operator == "sha256":
            ok = hashlib.sha256(value).hexdigest() == condition["value"]
        if not ok:
            raise FlowError("CONTRACT_MISMATCH", "節點前置或完成條件未通過")

    for condition in node["preconditions"]:
        check(condition)
    content = resolve(node["inputs"]["source"]["source"])
    implementation, parameters = node["implementation"], node["parameters"]
    if implementation == "builtin.branch":
        branch = node["branch"]
        condition = branch["condition"]
        operator, value = condition["operator"], condition["value"]
        if operator == "file_size_gt":
            selected = len(content) > value
        elif operator == "file_size_gte":
            selected = len(content) >= value
        elif operator == "text_contains":
            selected = value in content.decode("utf-8")
        else:
            selected = content.decode("utf-8") == value
        choice = "when_true" if selected else "when_false"
        arm = branch[choice]
        implementation, parameters = arm["implementation"], arm["parameters"]
        atomic_json(
            target.parent / "branch.json",
            {
                "step_id": step.id,
                "selected": choice,
                "condition": condition,
                "input_sha256": hashlib.sha256(content).hexdigest(),
                "tool": arm["tool"],
                "version": arm["version"],
            },
        )
    if implementation == "builtin.copy":
        own_output = content
    elif implementation == "builtin.normalize":
        text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if parameters.get("strip", False):
            text = "\n".join(line.strip() for line in text.splitlines())
        if parameters.get("final_newline", True):
            text = text.rstrip("\n") + "\n"
        own_output = text.encode("utf-8")
    elif implementation == "builtin.json":
        own_output = (
            json.dumps(
                json.loads(content.decode("utf-8-sig")),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode()
    elif implementation == "builtin.report":
        own_output = (
            json.dumps(
                {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()},
                sort_keys=True,
            )
            + "\n"
        ).encode()
    elif implementation == "builtin.llm":
        from .llm_runtime import run_llm

        own_output = run_llm(ctx, step, node, content, PLAN["ir_hash"])
    else:
        raise FlowError("IR_UNSUPPORTED_FEATURE", "未支援的 Handler")
    for condition in node["postconditions"]:
        check(condition)
    if time.monotonic() - started > node["timeout_seconds"]:
        raise FlowError("STEP_TIMEOUT", "受控轉換超過時間限制，未發布產物")
    return save_output(target, node["outputs"]["result"]["path"], own_output)


class Adapter(BaseAdapter):
    handlers = {"studio.input": input_handler, "studio.transform": transform_handler}

    def configuration(self, overrides):
        if overrides:
            raise FlowError("CONTRACT_MISMATCH", "請在 Studio 建立新版本調整流程參數")
        return Workflow.model_validate(PLAN["runtime_workflow"])

    def plan(self, runtime, workflow, source=None, output=None):
        result = super().plan(runtime, workflow, source, output)
        if (
            any(n["kind"] == "llm" for n in PLAN["operations"].values())
            and os.environ.get("WORKFLOW_STUDIO_LLM_AUTHORIZATION") != PLAN["ir_hash"]
        ):
            result["blocked"].append(
                {"code": "APPROVAL_REQUIRED", "message": "模型節點需要此 IR 版本的執行授權"}
            )
        if source is not None and source.is_file() and source.stat().st_size > MAX_FILE_BYTES:
            result["blocked"].append({"code": "INPUT_TOO_LARGE"})
        result["ready_to_prepare"] = not result["blocked"]
        return result

    def minimum_space(self, workflow):
        return (
            max((n["resources"]["disk_mb"] for n in PLAN["operations"].values()), default=0)
            * 1024
            * 1024
        )
