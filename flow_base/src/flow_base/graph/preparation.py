"""Prepare trusted, hash-locked Python wheels in an operation-scoped directory."""

import json
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib.metadata import distributions
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import build_opener

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

from ..contracts import AssetFile, Resource
from ..diagnostics import redact
from ..execution.process import worker_environment
from ..llm import NoRedirect
from ..outputs import reject_links
from ..resources import ensure_resource, resource_path
from ..runtime import FlowError, atomic_json, canonical, digest, read_json, sha256
from ..workspace import Workspace


class WheelDownload(Contract):
    url: str = Field(min_length=1, max_length=500)
    size: int = Field(gt=0)
    license: str = Field(min_length=1, max_length=300)

    @field_validator("url")
    @classmethod
    def official_index(cls, value):
        target = urlsplit(value)
        if (target.scheme != "https" or target.hostname != "files.pythonhosted.org"
                or target.port not in {None, 443} or target.username is not None or target.password is not None
                or target.query or target.fragment or not target.path.startswith("/packages/")):
            raise ValueError("wheel 下載須為無憑證的 PyPI 官方檔案網址。")
        return value


class LockedWheel(Contract):
    artifact: str = Field(min_length=1, max_length=240)
    sha256: Fingerprint
    distribution: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=80)
    source: WheelDownload | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("artifact")
    @classmethod
    def wheel_path(cls, value):
        try:
            safe_relative(value)
            parse_wheel_filename(Path(value).name)
        except (StudioError, ValueError) as exc:
            raise ValueError("配方只接受受信目錄內的 wheel 相對路徑。") from exc
        return value

    @model_validator(mode="after")
    def source_filename(self):
        if self.source:
            filename = Path(self.artifact).name
            name, version, _build, _tags = parse_wheel_filename(filename)
            if (Path(urlsplit(self.source.url).path).name != filename
                    or name != canonicalize_name(self.distribution) or str(version) != self.version):
                raise ValueError("PyPI 來源的檔名、套件及版次須與鎖定 wheel 一致。")
        return self


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
    if isinstance(declaration, dict) and declaration.get("action") in (
        "construct_mapping", "construct_wasm", "construct_cli", "construct_skill_script",
    ):
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


def _staging_failure(staging, error):
    return FlowError(
        "STAGING_PATH_UNUSABLE", "套件準備的暫存或快取位置無法使用。",
        stage="wheel_preparation", staging=str(staging), python=sys.executable,
        os_error=getattr(error, "winerror", None) or error.errno,
        stderr=redact(str(error)),
    )


def _open_wheel(request, *, timeout):
    return build_opener(NoRedirect).open(request, timeout=timeout)


def _wheel_sources(runtime, recipe, root, *, download_allowed, offline):
    files, acquired = {}, []
    for item in recipe.wheels:
        source = root / item.artifact
        reject_links(source)
        if not source.resolve().is_relative_to(root):
            _fail("wheel 超出配方的受信目錄。")
        if not source.is_file():
            if item.source is None:
                _fail("受信目錄缺少配方的 wheel。")
            metadata = item.source
            resource = Resource(id="wheel-" + item.sha256[:24], revision=item.version, license=metadata.license,
                                files=[AssetFile(path=source.name, url=metadata.url, size=metadata.size,
                                                 sha256=item.sha256)])
            reject_links(resource_path(runtime, resource) / source.name)
            reject_links(resource_path(runtime, resource) / (source.name + ".part"))
            try:
                cached = ensure_resource(runtime, resource, offline=offline or not download_allowed, opener=_open_wheel)
            except FlowError as exc:
                if exc.code != "OFFLINE_RESOURCE_MISSING":
                    raise
                code = "OFFLINE_WHEEL_MISSING" if offline else "PREPARATION_DOWNLOAD_APPROVAL_REQUIRED"
                message = "離線工作缺少配方的 wheel 快取。" if offline else "此配方尚未取得 wheel 下載授權。"
                raise FlowError(code, message, artifact=item.artifact, source=metadata.url) from exc
            source = cached / source.name
            acquired.append({"artifact": item.artifact, "version": item.version, "sha256": item.sha256,
                             **metadata.model_dump(mode="json")})
        files[item.artifact] = source
    return files, acquired


def _stage_wheels(uv, recipe, sources, staging, *, cache=None, acquired=()):
    ready = staging / "ready"
    cache = cache or staging / "uv-cache"
    wheelhouse = staging / "wheels"
    try:
        reject_links(staging)
        reject_links(cache)
        ready.mkdir()
        cache.mkdir(exist_ok=True)
        wheelhouse.mkdir()
        probe = cache / "write-test.tmp"
        atomic_json(probe, {"writable": True})
        probe.unlink()
    except OSError as exc:
        raise _staging_failure(staging, exc) from exc

    retained = []
    for item in recipe.wheels:
        source = sources[item.artifact]
        reject_links(source)
        name, version, _build, _tags = parse_wheel_filename(source.name)
        if name != canonicalize_name(item.distribution) or str(version) != item.version:
            _fail("wheel 名稱或版本與配方不同。")
        wheel = wheelhouse / source.name
        try:
            shutil.copyfile(source, wheel)
        except OSError as exc:
            raise _staging_failure(staging, exc) from exc
        if sha256(wheel) != item.sha256:
            _fail("wheel 校驗值與已授權的配方不同。")
        retained.append(wheel.as_uri())

    site = ready / "site"
    argv = [
        uv, "--no-config", "pip", "install", "--offline", "--no-deps", "--no-index", "--no-build",
        "--target", str(site), "--python", sys.executable, *retained,
    ]
    env = worker_environment() | {"UV_CACHE_DIR": str(cache), "UV_LINK_MODE": "copy"}
    try:
        result = subprocess.run(argv, cwd=staging, env=env, capture_output=True, timeout=90, check=False)
    except OSError as exc:
        raise _staging_failure(staging, exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise FlowError(
            "PREPARATION_IO_FAILED", "uv 逾時，請確認這次離線套件準備的執行環境。",
            stage="uv_install", staging=str(staging), python=sys.executable,
        ) from exc
    if result.returncode:
        stderr = result.stderr.decode("utf-8", "replace")
        normalized = stderr.lower()
        os_error = re.search(r"os error (\d+)", stderr, re.IGNORECASE)
        stage_error = any(
            marker in normalized for marker in (
                "failed to persist temporary file", "failed to create cache directory",
                "os error 206", "file name too long",
            )
        )
        if sys.platform == "win32" and not stderr.strip():
            stage_error = True
        if "#" in str(staging) and (
            "distribution not found at" in normalized or "building source distributions is disabled" in normalized
        ):
            stage_error = True
        code = "STAGING_PATH_UNUSABLE" if stage_error else "PREPARATION_IO_FAILED"
        raise FlowError(
            code, "uv 無法離線安裝配方鎖定的 wheel。",
            stage="uv_python_probe" if "failed to inspect python interpreter" in normalized else "uv_install",
            python=sys.executable, staging=str(staging), cache=str(cache),
            exit_code=result.returncode, os_error=int(os_error[1]) if os_error else None,
            stderr=redact(stderr[-3000:]),
        )
    entry = _entry(site, recipe)
    receipt = {
        "operation_id": preparation_id(recipe),
        "recipe_hash": digest(recipe.model_dump(mode="json")),
        "site_hash": _site_hash(site),
        "entry_point": entry.value,
        "action": recipe.action,
    }
    if acquired:
        receipt["wheel_sources"] = list(acquired)
    atomic_json(ready / "receipt.json", receipt)
    return ready, receipt


def _prepare_short(uv, recipe, sources, staging, workspace, first, *, acquired=()):
    """Prepare off-volume if needed, then verify a same-volume staging copy."""
    try:
        # Some uv releases reinterpret a literal # when resolving local wheel paths, even in a file URI.
        roots = sorted({Path(tempfile.gettempdir()), workspace}, key=lambda path: (len(str(path)), str(path)))
        parent = next((path for path in roots if path.is_dir() and "#" not in str(path)
                       and len(str(path)) + len("rk-wheel-xxxxxxxx") < len(str(staging))), None)
        if parent is None:
            raise FlowError(
                "STAGING_PATH_UNUSABLE", "找不到比原位置更短的可用本機暫存路徑。",
                staging=str(staging), candidates=[str(path) for path in roots], python=sys.executable,
            )
        cache_parent = next(path for path in roots if path.is_dir())
        reject_links(parent)
        reject_links(cache_parent)
        with (
            tempfile.TemporaryDirectory(prefix="rk-wheel-", dir=parent) as directory,
            tempfile.TemporaryDirectory(prefix="rk-uv-", dir=cache_parent) as cache,
        ):
            short = Path(directory).resolve()
            ready, receipt = _stage_wheels(uv, recipe, sources, short, cache=Path(cache), acquired=acquired)
            destination = staging / "verified"
            shutil.copytree(ready, destination)
            site = destination / "site"
            if _site_hash(site) != receipt["site_hash"] or _entry(site, recipe).value != receipt["entry_point"]:
                _fail("換用短路徑後的安裝結果與驗證指紋不同。")
            atomic_json(destination / "preparation-diagnostic.json", {
                "operation_id": receipt["operation_id"], "fallback": "short_staging",
                "initial_failure": first.as_dict()["error"],
            })
            return destination, receipt
    except (FlowError, OSError) as exc:
        last = exc if isinstance(exc, FlowError) else _staging_failure(staging, exc)
        raise FlowError(last.code, str(last), **last.details, initial_failure=first.as_dict()["error"]) from exc


def prepare_wheels(runtime, recipe, artifact_root, *, download_allowed=False, offline=False):
    """Install verified wheels offline, acquiring missing files only under a separate grant."""
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
        sources, acquired = _wheel_sources(runtime, recipe, root, download_allowed=download_allowed, offline=offline)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="wheel-staging-", dir=target.parent) as directory:
                staging = Path(directory)
                try:
                    ready, receipt = _stage_wheels(uv, recipe, sources, staging, acquired=acquired)
                except FlowError as exc:
                    if sys.platform != "win32" or exc.code != "STAGING_PATH_UNUSABLE":
                        raise
                    ready, receipt = _prepare_short(uv, recipe, sources, staging, runtime.workspace_root, exc, acquired=acquired)
                ready.rename(target)
        except OSError as exc:
            raise _staging_failure(target.parent, exc) from exc
    return receipt


def prepare_recipe(runtime, recipe, artifact_root, *, download_allowed=False, offline=False):
    if isinstance(recipe, WheelPreparation):
        return prepare_wheels(runtime, recipe, artifact_root, download_allowed=download_allowed, offline=offline)
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

        return execute_construction(
            _target(runtime, preparation_id(recipe)), recipe, binding, inputs, parameters, runtime=runtime,
        )
    payload = canonical({"binding": binding, "inputs": inputs, "parameters": parameters})
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-X",
                "utf8",
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
            lines = child.stderr.decode("utf-8", "replace").splitlines()
            fault = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*): (.*)$", lines[-1]) if lines else None
            details = {"stage": "prepared_runner", "exit_code": child.returncode}
            if fault:
                details["exception_type"] = fault[1]
                if fault[1] == "ImportError" and fault[2].startswith("DLL load failed while importing "):
                    details["loader_error"] = redact(fault[2][:350])
            raise FlowError(
                "TOOLCHAIN_PREPARATION_BLOCKED", "已準備的 Adapter 無法在獨立程序執行。",
                **details,
            )
        observation = json.loads(child.stdout)
        if observation.get("code") == "ADAPTER_INPUT_REJECTED":
            raise FlowError("ADAPTER_INPUT_REJECTED", "已準備的 Adapter 拒絕此案例輸入。")
        return observation["outputs"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, AttributeError) as exc:
        raise FlowError(
            "TOOLCHAIN_PREPARATION_BLOCKED", "已準備的 Adapter 執行或回應無效。"
        ) from exc
