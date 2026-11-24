"""Small deterministic allowlist. Functions transform bytes; never run a shell."""

import hashlib
import json

from ..contracts import StudioError
from ..ir.models import Port, Resources
from .models import ToolDescription


def copy_bytes(data: bytes, parameters: dict) -> bytes:
    if parameters:
        raise StudioError("CONTRACT_MISMATCH", "file.copy 不接受額外參數。")
    return data


def normalize_text(data: bytes, parameters: dict) -> bytes:
    if set(parameters) - {"strip", "final_newline"} or not all(
        type(v) is bool for v in parameters.values()
    ):
        raise StudioError("CONTRACT_MISMATCH", "文字整理只接受 strip／final_newline 布林參數。")
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if parameters.get("strip", False):
        text = "\n".join(line.strip() for line in text.splitlines())
    if parameters.get("final_newline", True):
        text = text.rstrip("\n") + "\n"
    return text.encode("utf-8")


def format_json(data: bytes, parameters: dict) -> bytes:
    if parameters:
        raise StudioError("CONTRACT_MISMATCH", "json.format 不接受額外參數。")
    value = json.loads(data.decode("utf-8-sig"))
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def file_report(data: bytes, parameters: dict) -> bytes:
    if parameters:
        raise StudioError("CONTRACT_MISMATCH", "file.report 不接受額外參數。")
    return (
        json.dumps({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True)
        + "\n"
    ).encode()


TRANSFORMS = {
    "builtin.copy": copy_bytes,
    "builtin.normalize": normalize_text,
    "builtin.json": format_json,
    "builtin.report": file_report,
}


def builtin_tools() -> list[ToolDescription]:
    definitions = [
        ("file.copy", "複製檔案", "file.copy", "builtin.copy", None, None),
        (
            "text.normalize",
            "整理文字換行與空白",
            "text.normalize",
            "builtin.normalize",
            "txt",
            "txt",
        ),
        ("json.format", "整理 JSON", "json.format", "builtin.json", "json", "json"),
        ("file.report", "產生檔案指紋報告", "file.report", "builtin.report", None, "json"),
    ]
    result = [
        ToolDescription(
            tool_id=identity,
            version="1.0.0",
            title=title,
            capabilities=[capability],
            kind="deterministic",
            inputs={"source": Port(type="file", format=input_format)},
            outputs={"result": Port(type="file", format=output_format)},
            platforms=["windows", "macos", "linux"],
            side_effects="local_write",
            resources=Resources(memory_mb=64, disk_mb=50, network=False),
            permissions=["workspace.write"],
            implementation=implementation,
            test_evidence=["tests/studio/test_registry.py"],
            cost_micro_usd=0,
        )
        for identity, title, capability, implementation, input_format, output_format in definitions
    ]
    result.append(
        result[0].model_copy(
            update={
                "tool_id": "control.branch",
                "title": "受控條件分支",
                "capabilities": ["control.branch"],
                "kind": "composite_branch",
                "implementation": "builtin.branch",
                "test_evidence": ["tests/studio/test_branches.py"],
            }
        )
    )
    result.append(
        result[0].model_copy(
            update={
                "tool_id": "llm.structured",
                "title": "受控模型結構化處理",
                "capabilities": ["llm.structured"],
                "kind": "llm",
                "implementation": "builtin.llm",
                "side_effects": "external",
                "outputs": {"result": Port(type="file", format="json")},
                "resources": Resources(memory_mb=64, disk_mb=10, network=True),
                "permissions": ["workspace.write", "model.call", "secret.read"],
                "cost_micro_usd": 1,
                "test_evidence": ["tests/studio/test_llm_runtime.py"],
            }
        )
    )
    for suffix, title, output_format in (
        ("text", "自訂文字處理", "txt"),
        ("csv", "自訂 CSV 處理", "csv"),
        ("json", "自訂 JSON 處理", "json"),
    ):
        result.append(
            result[0].model_copy(
                update={
                    "tool_id": "data.transform_" + suffix,
                    "title": title,
                    "capabilities": ["data.transform", "handler.generate"],
                    "implementation": "generated.program",
                    "outputs": {"result": Port(type="file", format=output_format)},
                    "resources": Resources(memory_mb=128, disk_mb=50, network=False),
                    "test_evidence": [
                        "tests/studio/test_generated_handlers.py",
                        "tests/studio/test_program_safety.py",
                    ],
                }
            )
        )
    return result
