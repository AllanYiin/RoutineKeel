"""Prepare trusted, hash-locked Python wheels in an operation-scoped directory."""

import json
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib.metadata import distributions
from pathlib import Path
from typing import Literal

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from pydantic import Field, ValidationError, field_validator, model_validator
from routinekeel_contracts.capability_requirements import (
    AssetID,
    AssetVersion,
    Fingerprint,
)
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.models import Contract
from routinekeel_contracts.paths import safe_relative

from ..execution.process import worker_environment
from ..outputs import reject_links
from ..runtime import FlowError, atomic_json, canonical, digest, read_json, sha256
from ..workspace import Workspace


class LockedWheel(Contract):
    artifact: str = Field(min_length=1, max_length=240)
    sha256: Fingerprint
    distribution: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=80)

    @field_validator("artifact")
    @classmethod
    def wheel_path(cls, value):
        try:
            safe_relative(value)
            parse_wheel_filename(Path(value).name)
        except (StudioError, ValueError) as exc:
            raise ValueError("配方只接受受信目錄內的 wheel 相對路徑。") from exc
        return value


class WheelPreparation(Contract):
    recipe_schema_version: Literal[1]
    id: AssetID
    version: AssetVersion
    action: Literal["install_locked_wheels"]
    tool: str = Field(min_length=1, max_length=200)
    distribution: str = Field(min_length=1, max_length=128)
    platforms: list[Literal["win32", "linux", "darwin"]] = Field(min_length=1)
    wheels: list[LockedWheel] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def lock(self):
        names = {canonicalize_name(item.distribution) for item in self.wheels}
        if len(names) != len(self.wheels) or canonicalize_name(self.distribution) not in names:
            raise ValueError("配方須鎖定目標套件與各相依套件的唯一版本。")
        return self


def _fail(message):
    raise FlowError("TOOLCHAIN_PREPARATION_BLOCKED", message)


def preparation_id(recipe):
    return digest(
        [
            recipe.model_dump(mode="json"),
            sysconfig.get_platform(),
            sys.implementation.cache_tag,
        ]
    )


def load_recipe(declaration):
    if isinstance(declaration, dict) and declaration.get("action") in ("construct_mapping", "construct_wasm"):
        from .construction import load_construction

        return load_construction(declaration)
    return WheelPreparation.model_validate(declaration)


def adapter_recipe(record, adapter):
    declaration = record.get("preparation")
    if declaration is None:
        return None
    try:
        recipe = load_recipe(declaration)
    except ValidationError as exc:
        raise FlowError("TOOLCHAIN_PREPARATION_BLOCKED", "宿主宣告的安裝配方格式不符。") from exc
    if not isinstance(recipe, WheelPreparation):
        from .construction import check_descriptor

        check_descriptor(recipe, adapter)
        return recipe
    package = canonicalize_name(recipe.distribution)
    if (
        recipe.tool != adapter.tool
        or package != canonicalize_name(adapter.distribution or "")
        or not any(
            canonicalize_name(item.distribution) == package
            and item.version == adapter.provider_version
            for item in recipe.wheels
        )
    ):
        _fail("配方的工具、套件或版本與 Adapter 不符。")
    return recipe


def _target(runtime, identifier):
    root = runtime.home / "toolchain_preparations"
    path = root / identifier
    reject_links(path)
    return path


def _site_hash(site):
    entries = []
    for path in sorted(site.rglob("*")):
        reject_links(path)
        if path.is_file():
            entries.append((path.relative_to(site).as_posix(), sha256(path)))
    return digest(entries)


def _entry(site, recipe):
    packages = list(distributions(path=[str(site)]))
    if any(not package.name or not package.version for package in packages):
        _fail("隔離環境的套件缺少名稱或版本證據。")
    locked = {canonicalize_name(item.distribution): item.version for item in recipe.wheels}
    installed = {canonicalize_name(package.name): package.version for package in packages}
    if len(packages) != len(installed) or installed != locked:
        _fail("隔離環境安裝的套件與鎖定清單不同。")
    marker = default_environment() | {"extra": ""}
    for package in packages:
        for required in package.requires or ():
            dependency = Requirement(required)
            if dependency.marker and not dependency.marker.evaluate(marker):
                continue
            version = installed.get(canonicalize_name(dependency.name))
            if version is None or dependency.url or not dependency.specifier.contains(version):
                _fail("配方缺少已鎖定的傳遞相依套件。")
    entries = [
        entry
        for package in packages
        if canonicalize_name(package.name) == canonicalize_name(recipe.distribution)
        for entry in package.entry_points
        if entry.group == "routinekeel.portable_adapters" and entry.name == recipe.tool
    ]
    if len(entries) != 1:
        _fail("隔離環境缺少唯一的 Adapter 入口。")
    return entries[0]


def read_preparation(runtime, recipe):
    try:
        return _read_preparation(runtime, recipe)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FlowError("TOOLCHAIN_PREPARATION_BLOCKED", "無法核對已保存的配方、產物或收據。") from exc


def _read_preparation(runtime, recipe):
    identifier = preparation_id(recipe)
    target = _target(runtime, identifier)
    if not isinstance(recipe, WheelPreparation):
        from .construction import read_construction

        return read_construction(target, recipe)
    receipt = target / "receipt.json"
    reject_links(receipt)
    if not receipt.is_file():
        return None
    body = read_json(receipt)
    site = target / "site"
    reject_links(site)
    if (
        not isinstance(body, dict)
        or not site.is_dir()
        or body.get("operation_id") != identifier
        or body.get("recipe_hash") != digest(recipe.model_dump(mode="json"))
        or body.get("site_hash") != _site_hash(site)
        or body.get("entry_point") != _entry(site, recipe).value
    ):
        _fail("已準備的工具、入口或收據已變更。")
    return body


def prepare_wheels(runtime, recipe, artifact_root):
    """Install only named, verified wheels from the authorized local artifact root."""
    if sys.platform not in recipe.platforms:
        _fail("配方不支援目前平台。")
    uv = shutil.which("uv")
    if not uv:
        raise FlowError("UV_MISSING", "請以底座啟動器準備 uv，再重驗配方。")
    root = Path(artifact_root).resolve()
    if not root.is_dir():
        _fail("配方的受信 wheel 目錄不存在。")
    op_id = preparation_id(recipe)
    target = _target(runtime, op_id)
    with runtime.lock("toolchain-preparation:" + op_id, timeout=120):
        previous = read_preparation(runtime, recipe)
        if previous is not None:
            return previous
        if target.exists():
            _fail("操作位置已有未驗證內容，請先查核原準備紀錄。")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wheel-staging-", dir=target.parent) as directory:
            staging = Path(directory)
            ready = staging / "ready"
            wheelhouse = staging / "wheels"
            ready.mkdir()
            wheelhouse.mkdir()
            retained = []
            for item in recipe.wheels:
                source = root / item.artifact
                reject_links(source)
                if not source.is_file() or not source.resolve().is_relative_to(root):
                    _fail("受信目錄缺少配方的 wheel。")
                name, version, _build, _tags = parse_wheel_filename(source.name)
                if name != canonicalize_name(item.distribution) or str(version) != item.version:
                    _fail("wheel 名稱或版本與配方不同。")
                wheel = wheelhouse / source.name
                shutil.copyfile(source, wheel)
                if sha256(wheel) != item.sha256:
                    _fail("wheel 校驗值與已授權的配方不同。")
                retained.append(str(wheel))
            site = ready / "site"
            argv = [
                uv,
                "--no-config",
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--no-index",
                "--no-build",
                "--target",
                str(site),
                "--python",
                sys.executable,
                *retained,
            ]
            env = worker_environment() | {
                "UV_CACHE_DIR": str(staging / "cache"),
                "UV_LINK_MODE": "copy",
            }
            try:
                result = subprocess.run(
                    argv, cwd=staging, env=env, capture_output=True, timeout=90, check=False
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FlowError(
                    "TOOLCHAIN_PREPARATION_BLOCKED", "安裝程序無法完成，請確認本機環境後重驗。"
                ) from exc
            if result.returncode:
                _fail("uv 無法離線安裝配方鎖定的 wheel。")
            entry = _entry(site, recipe)
            receipt = {
                "operation_id": op_id,
                "recipe_hash": digest(recipe.model_dump(mode="json")),
                "site_hash": _site_hash(site),
                "entry_point": entry.value,
                "action": recipe.action,
            }
            atomic_json(ready / "receipt.json", receipt)
            ready.rename(target)
    return receipt


def prepare_recipe(runtime, recipe, artifact_root):
    if isinstance(recipe, WheelPreparation):
        return prepare_wheels(runtime, recipe, artifact_root)
    from .construction import build_construction

    return build_construction(runtime, _target(runtime, preparation_id(recipe)), recipe, artifact_root)


def copy_preparations(source, target, bindings):
    leaves = [
        leaf
        for binding in bindings.values()
        for leaf in (
            binding["chain_bindings"].values() if binding["provider"] == "toolchain" else [binding]
        )
    ]
    for binding in leaves:
        if "preparation" not in binding:
            continue
        recipe = load_recipe(binding["preparation"])
        if read_preparation(source, recipe) is None:
            _fail("候選試做缺少已準備的工作環境。")
        identifier = preparation_id(recipe)
        destination = _target(target, identifier)
        if not destination.exists():
            shutil.copytree(_target(source, identifier), destination)
        if read_preparation(target, recipe) is None:
            _fail("候選試做的工作環境與原收據不同。")


def execute_prepared(workspace, binding, inputs, parameters):
    if workspace is None:
        _fail("已準備的 Adapter 缺少指定工作環境。")
    runtime = Workspace(workspace).runtime
    recipe = load_recipe(binding["preparation"])
    if not isinstance(recipe, WheelPreparation):
        from .construction import execute_construction

        return execute_construction(_target(runtime, preparation_id(recipe)), recipe, binding, inputs, parameters)
    payload = canonical({"binding": binding, "inputs": inputs, "parameters": parameters})
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "flow_base.graph.prepared_runner",
                str(runtime.workspace_root),
            ],
            input=payload.encode("utf-8"),
            env=worker_environment(),
            capture_output=True,
            timeout=20,
            check=False,
        )
        if child.returncode:
            _fail("已準備的 Adapter 無法在獨立程序執行。")
        observation = json.loads(child.stdout)
        if observation.get("code") == "ADAPTER_INPUT_REJECTED":
            raise FlowError("ADAPTER_INPUT_REJECTED", "已準備的 Adapter 拒絕此案例輸入。")
        return observation["outputs"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, AttributeError) as exc:
        raise FlowError(
            "TOOLCHAIN_PREPARATION_BLOCKED", "已準備的 Adapter 執行或回應無效。"
        ) from exc
