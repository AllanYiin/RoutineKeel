"""Build versioned declarative and isolated program Adapters from trusted recipes."""

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from routinekeel_contracts.capability_requirements import (
    AssetID,
    AssetVersion,
    Fingerprint,
)
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.models import Contract, Identifier
from routinekeel_contracts.paths import safe_relative

from ..execution.process import worker_environment
from ..outputs import reject_links
from ..runtime import FlowError, atomic_json, canonical, digest, read_json, sha256
from .base_steps import _select

WASM_LIMIT = 2 * 1024**2
WASM_FLAGS = (
    "--max-old-space-size=48",
    "--wasm-max-mem-pages=128",
    "--wasm-num-compilation-tasks=1",
)
WASM_RUNNER = r"""
const request = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let limited = false;
try { new WebAssembly.Memory({initial:129}); } catch (error) { limited = error instanceof RangeError; }
if (!limited) throw Error('Wasm memory limit');
const module_ = new WebAssembly.Module(Buffer.from(request.program, 'base64'));
if (WebAssembly.Module.imports(module_).length) throw Error('Wasm imports denied');
const engine = new WebAssembly.Instance(module_).exports;
if (!(engine.memory instanceof WebAssembly.Memory) || typeof engine.allocate !== 'function'
    || typeof engine.transform !== 'function') throw Error('Invalid Adapter ABI');
if (request.verify) {
    process.stdout.write(JSON.stringify({imports:0, memory_pages:128, abi:1}));
} else {
    try {
        const write = (body) => {
            const payload = Buffer.from(JSON.stringify(body), 'utf8');
            const start = engine.allocate(payload.length);
            if (!Number.isInteger(start) || start < 0 || start + payload.length > engine.memory.buffer.byteLength)
                throw Error('Invalid input range');
            new Uint8Array(engine.memory.buffer, start, payload.length).set(payload);
            return [start, payload.length];
        };
        if (Object.keys(request.parameters).length) {
            if (typeof engine.configure !== 'function') throw Error('Parameters unsupported');
            engine.configure(...write(request.parameters));
        }
        const result = engine.transform(...write(request.inputs));
        if (typeof result !== 'bigint') throw Error('Invalid result range');
        const start = Number(BigInt.asUintN(64, result) >> 32n);
        const size = Number(result & 0xffffffffn);
        if (!size || size > 1048576 || start + size > engine.memory.buffer.byteLength)
            throw Error('Output exceeded boundary');
        const output = new TextDecoder('utf-8', {fatal: true}).decode(new Uint8Array(engine.memory.buffer, start, size));
        process.stdout.write(JSON.stringify({outputs:JSON.parse(output)}));
    } catch (error) {
        process.stdout.write(JSON.stringify({code:'ADAPTER_INPUT_REJECTED'}));
    }
}
"""


class OutputMapping(Contract):
    source: Identifier
    pointer: str = Field(default="", max_length=1000)
    fields: dict[Identifier, str] | None = None

    @model_validator(mode="after")
    def paths(self):
        for path in (self.pointer, *(self.fields or {}).values()):
            if (
                len(path) > 1000
                or path
                and (not path.startswith("/") or re.search(r"~(?![01])", path))
            ):
                raise ValueError("資料映射需要合法的 JSON Pointer。")
        if self.fields == {}:
            raise ValueError("逐列映射至少需要一個欄位。")
        return self


class Construction(Contract):
    recipe_schema_version: Literal[1]
    id: AssetID
    version: AssetVersion
    tool: str = Field(min_length=1, max_length=200)
    platforms: list[Literal["win32", "linux", "darwin"]] = Field(min_length=1)


class MappingConstruction(Construction):
    action: Literal["construct_mapping"]
    outputs: dict[Identifier, OutputMapping] = Field(min_length=1)


class WasmConstruction(Construction):
    action: Literal["construct_wasm"]
    artifact: str = Field(min_length=1, max_length=240)
    sha256: Fingerprint

    @field_validator("artifact")
    @classmethod
    def artifact_path(cls, value):
        try:
            if not safe_relative(value).endswith(".wasm"):
                raise ValueError("程式配方需要具名 WebAssembly 來源。")
        except StudioError as exc:
            raise ValueError("WebAssembly 來源不在受信目錄內。") from exc
        return value


def load_construction(raw):
    if raw.get("action") == "construct_mapping":
        return MappingConstruction.model_validate(raw)
    return WasmConstruction.model_validate(raw)


def _fail(message):
    raise FlowError("TOOLCHAIN_CONSTRUCTION_BLOCKED", message)


def check_descriptor(recipe, adapter):
    if (
        recipe.tool != adapter.tool
        or recipe.version != adapter.provider_version
        or adapter.locus != "local_runtime"
        or adapter.distribution is not None
        or adapter.permissions
        or adapter.network
        or adapter.side_effect != "none"
        or adapter.max_cost_micro_usd
    ):
        _fail("建置配方只接受同版本、無外部權限的本機 Adapter。")
    if isinstance(recipe, MappingConstruction) and (
        recipe.outputs.keys() != adapter.outputs.keys()
        or any(
            row.source not in adapter.inputs
            or row.fields is not None
            and adapter.outputs[name].type != "array"
            for name, row in recipe.outputs.items()
        )
    ):
        _fail("映射的來源或輸出埠不符合 Adapter 契約。")


def _engine():
    command = shutil.which("node")
    if not command:
        _fail("缺少可驗證限制的 Node WebAssembly 執行器。")
    return command, digest([sha256(Path(command)), WASM_FLAGS, WASM_RUNNER])


def _wasm(command, artifact, **parameters):
    if artifact.stat().st_size > WASM_LIMIT:
        _fail("WebAssembly 程式超過建置大小上限。")
    body = canonical(
        {"program": base64.b64encode(artifact.read_bytes()).decode("ascii"), **parameters}
    )
    try:
        result = subprocess.run(
            [command, *WASM_FLAGS, "-e", WASM_RUNNER],
            input=body.encode("utf-8"),
            env=worker_environment(),
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode:
            _fail("WebAssembly 程式未通過隔離檢查或執行失敗。")
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise FlowError("ADAPTER_TIMEOUT", "Adapter 超過隔離執行時間上限。") from exc
    except (OSError, ValueError) as exc:
        raise FlowError(
            "TOOLCHAIN_CONSTRUCTION_BLOCKED", "無法驗證 WebAssembly 執行器或回應。"
        ) from exc


def read_construction(target, recipe):
    reject_links(target)
    receipt_path = target / "receipt.json"
    reject_links(receipt_path)
    if not receipt_path.is_file():
        return None
    receipt = read_json(receipt_path)
    program = target / "program.json"
    reject_links(program)
    if (
        not isinstance(receipt, dict)
        or not program.is_file()
        or receipt.get("recipe_hash") != digest(recipe.model_dump(mode="json"))
        or read_json(program) != recipe.model_dump(mode="json")
        or receipt.get("operation_id") != target.name
        or receipt.get("program_hash") != sha256(program)
    ):
        _fail("已登錄的 Adapter 產物或建置收據已變更。")
    if isinstance(recipe, WasmConstruction):
        artifact = target / "program.wasm"
        reject_links(artifact)
        if (
            not artifact.is_file()
            or sha256(artifact) != recipe.sha256
            or receipt.get("engine_fingerprint") != _engine()[1]
            or receipt.get("isolation") != {"imports": 0, "memory_pages": 128, "abi": 1}
        ):
            _fail("WebAssembly 程式或隔離執行器已變更。")
    return receipt


def build_construction(runtime, target, recipe, artifact_root):
    if sys.platform not in recipe.platforms:
        _fail("Adapter 建置配方不支援目前平台。")
    with runtime.lock("toolchain-construction:" + target.name, timeout=20):
        prior = read_construction(target, recipe)
        if prior is not None:
            return prior
        if target.exists():
            _fail("建置位置已有未驗證的資料。")
        target.parent.mkdir(exist_ok=True, parents=True)
        with tempfile.TemporaryDirectory(prefix="adapter-staging-", dir=target.parent) as temporary:
            ready = Path(temporary) / "ready"
            ready.mkdir()
            raw = recipe.model_dump(mode="json")
            program = ready / "program.json"
            atomic_json(program, raw)
            receipt = {
                "operation_id": target.name,
                "recipe_hash": digest(raw),
                "program_hash": sha256(program),
                "action": recipe.action,
            }
            if isinstance(recipe, WasmConstruction):
                root = Path(artifact_root).resolve()
                source = root / recipe.artifact
                reject_links(source)
                if (
                    not source.is_file()
                    or not source.resolve().is_relative_to(root)
                    or source.stat().st_size > WASM_LIMIT
                ):
                    _fail("受信來源目錄內沒有可用的 WebAssembly 程式。")
                artifact = ready / "program.wasm"
                shutil.copyfile(source, artifact)
                if sha256(artifact) != recipe.sha256:
                    _fail("WebAssembly 來源的校驗值不同。")
                command, engine = _engine()
                receipt.update(
                    engine_fingerprint=engine, isolation=_wasm(command, artifact, verify=True)
                )
            atomic_json(ready / "receipt.json", receipt)
            ready.rename(target)
    return receipt


def execute_construction(target, recipe, binding, inputs, parameters):
    receipt = read_construction(target, recipe)
    if receipt is None or digest(receipt) != binding["implementation_hash"]:
        _fail("Adapter 執行來源與登錄收據不同。")
    if isinstance(recipe, WasmConstruction):
        result = _wasm(_engine()[0], target / "program.wasm", inputs=inputs, parameters=parameters)
        if result.get("code") == "ADAPTER_INPUT_REJECTED":
            raise FlowError("ADAPTER_INPUT_REJECTED", "隔離程式拒絕輸入或參數。")
        return result["outputs"]
    if parameters:
        raise FlowError("ADAPTER_INPUT_REJECTED", "資料映射配方未宣告可用參數。")
    result = {}
    try:
        for name, row in recipe.outputs.items():
            value = _select(inputs[row.source], row.pointer)
            if row.fields is not None:
                if not isinstance(value, list):
                    raise ValueError("projection requires a list")
                value = [
                    {output: _select(item, pointer) for output, pointer in row.fields.items()}
                    for item in value
                ]
            result[name] = value
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise FlowError("ADAPTER_INPUT_REJECTED", "映射所需的欄位或列資料不存在。") from exc
    return result
