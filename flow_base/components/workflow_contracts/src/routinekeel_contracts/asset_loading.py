"""Bounded JSON/YAML and ZIP readers for inert domain assets."""

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent

from .domain_assets import ASSET_MODELS, canonical_json
from .errors import StudioError
from .paths import safe_relative

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 48
DOCUMENT_FORMATS = {".json", ".yaml", ".yml"}
ASSET_FOLDERS = {
    "capabilities": ("capability", "capability_refs"),
    "patterns": ("pattern", "pattern_refs"),
    "policies": ("policy", "policy_refs"),
    "profile_schemas": ("profile_schema", "profile_schema_refs"),
}
_EXECUTABLE_FIELDS = {
    "shell",
    "command",
    "shell_command",
    "exec",
    "executable",
    "entrypoint",
    "entry_point",
    "python_code",
    "python_module",
    "script_path",
    "imports",
}
_UNSUPPORTED_VALUE = "資產只能使用 JSON 相容的資料內容。"


def _reject(message):
    return StudioError("DOMAIN_ASSET_INVALID", message)


def _mapping(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise _reject("資產物件含重複或非文字欄位。")
        result[key] = value
    return result


class _DataLoader(yaml.SafeLoader):
    # Preserve identifiers such as "on". Only true/false become booleans.
    yaml_implicit_resolvers: ClassVar = {
        char: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
        for char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise _reject("資產的 YAML 物件格式錯誤。")
        return _mapping(
            (self.construct_object(key, deep=deep), self.construct_object(value, deep=deep))
            for key, value in node.value
        )


_DataLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.IGNORECASE), list("tTfF")
)


def _parse_yaml(text):
    depth = 0
    for event in yaml.parse(text, Loader=_DataLoader):
        if (
            isinstance(event, AliasEvent)
            or getattr(event, "anchor", None)
            or getattr(event, "tag", None)
        ):
            raise _reject("資產 YAML 不接受自訂標籤、別名或錨點。")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > MAX_DEPTH:
                raise _reject("資產資料巢狀過深。")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1
    return yaml.load(text, Loader=_DataLoader)


def _check_values(value, depth=0, *, declarations=False):
    if depth > MAX_DEPTH:
        raise _reject("資產資料巢狀過深。")
    if isinstance(value, dict):
        for key, item in value.items():
            if declarations and key.casefold().replace("-", "_") in _EXECUTABLE_FIELDS:
                raise _reject("可攜資產不接受可執行指令欄位。")
            _check_values(item, depth + 1, declarations=declarations)
    elif isinstance(value, list):
        for item in value:
            _check_values(item, depth + 1, declarations=declarations)
    elif value is not None and type(value) not in {str, bool, int, float}:
        raise _reject(_UNSUPPORTED_VALUE)


def _parse(raw, extension, *, declarations=False):
    if len(raw) > MAX_DOCUMENT_BYTES or extension not in DOCUMENT_FORMATS:
        raise _reject("資產文件大小或格式不符。")
    try:
        text = raw.decode("utf-8-sig")
        data = (
            json.loads(text, object_pairs_hook=_mapping)
            if extension == ".json"
            else _parse_yaml(text)
        )
        _check_values(data, declarations=declarations)
        canonical_json(data)
        return data
    except (ValueError, RecursionError, yaml.YAMLError) as exc:
        raise _reject(_UNSUPPORTED_VALUE) from exc


def load_data_document(raw, filename):
    return _parse(raw, PurePosixPath(filename).suffix.lower())


@dataclass(frozen=True)
class LoadedAsset:
    kind: str
    source_sha256: str
    sha256: str
    canonical: bytes

    @property
    def model(self):
        return ASSET_MODELS[self.kind].model_validate_json(self.canonical)


def load_asset_bytes(kind, raw, extension=".json"):
    if kind not in ASSET_MODELS:
        raise _reject("不支援的可攜資產類型。")
    payload = _parse(raw, extension.lower(), declarations=True)
    try:
        model = ASSET_MODELS[kind].model_validate(payload)
    except ValidationError as exc:
        raise _reject("可攜資產不符合指定的 schema 版本。") from exc
    canonical = canonical_json(model.model_dump(mode="json"))
    return LoadedAsset(
        kind, hashlib.sha256(raw).hexdigest(), hashlib.sha256(canonical).hexdigest(), canonical
    )


def _regular_source(path):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        metadata = part.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise _reject("可攜資產來源不可經過連結或重解析點。")
        if part == path and not stat.S_ISREG(metadata.st_mode):
            raise _reject("可攜資產來源須為一般檔案。")
    return path


def _read_source(path, limit):
    try:
        with _regular_source(path).open("rb") as source:
            data = source.read(limit + 1)
    except OSError as exc:
        raise _reject("無法讀取可攜資產來源。") from exc
    if len(data) > limit:
        raise _reject("可攜資產來源超過大小限制。")
    return data


def load_asset(kind, path):
    return load_asset_bytes(kind, _read_source(path, MAX_DOCUMENT_BYTES), Path(path).suffix)


@dataclass(frozen=True)
class LoadedPack:
    manifest: LoadedAsset
    source_sha256: str
    definitions: tuple[LoadedAsset, ...]
    archive: bytes


def _zip_inventory(archive):
    entries = {}
    paths = set()
    size = 0
    for entry in archive.infolist():
        name = entry.filename
        safe_relative(name)
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (
            name != entry.orig_filename
            or name.casefold() in paths
            or mode not in {0, stat.S_IFREG}
            or entry.is_dir()
            or entry.flag_bits & 1
            or entry.file_size > MAX_DOCUMENT_BYTES
        ):
            raise _reject("Pack 內有重複、不安全或不支援的檔案。")
        paths.add(name.casefold())
        entries[name] = entry
        size += entry.file_size
    if len(entries) > 512 or size > MAX_PACK_BYTES:
        raise _reject("Pack 資產數量或解壓後大小超過限制。")
    return entries


def load_pack(path):
    raw = _read_source(path, MAX_PACK_BYTES)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = _zip_inventory(archive)
            manifests = set(entries) & {"pack.json", "pack.yaml", "pack.yml"}
            if len(manifests) != 1:
                raise _reject("Pack 必須有唯一的根層 manifest。")
            manifest_name = manifests.pop()
            manifest = load_asset_bytes(
                "pack",
                archive.read(entries.pop(manifest_name)),
                PurePosixPath(manifest_name).suffix,
            )
            model = manifest.model
            if entries.keys() != model.assets.keys():
                raise _reject("Pack 的檔案與 manifest 清單不符。")
            definitions = []
            refs = {field: [] for _, field in ASSET_FOLDERS.values()}
            fixtures = set()
            for name, entry in sorted(entries.items()):
                parts = PurePosixPath(name).parts
                extension = PurePosixPath(name).suffix.lower()
                data = archive.read(entry)
                if hashlib.sha256(data).hexdigest() != model.assets[name]:
                    raise _reject("Pack 資產指紋不符。")
                if len(parts) >= 2 and parts[0] in ASSET_FOLDERS:
                    kind, field = ASSET_FOLDERS[parts[0]]
                    definition = load_asset_bytes(kind, data, extension)
                    asset = definition.model
                    refs[field].append(asset.pattern.ref if kind == "pattern" else asset.ref)
                    definitions.append(definition)
                elif len(parts) >= 2 and parts[0] == "cases" and extension in DOCUMENT_FORMATS:
                    _parse(data, extension)
                    fixtures.add(name)
                elif len(parts) >= 2 and parts[0] == "cases" and extension in {".csv", ".txt"}:
                    data.decode("utf-8-sig")
                else:
                    raise _reject("Pack 包含非宣告式資產或不受支援的測例格式。")
            if any(sorted(values) != sorted(getattr(model, key)) for key, values in refs.items()):
                raise _reject("Pack 的資產版本參照與實際定義不符。")
            if any(
                definition.model.tests.golden_cases_manifest not in fixtures
                for definition in definitions
                if definition.kind == "pattern"
            ):
                raise _reject("Pack 缺少 Pattern 宣告的測例清單。")
    except (zipfile.BadZipFile, UnicodeError, NotImplementedError, RuntimeError) as exc:
        if isinstance(exc, StudioError):
            raise
        raise _reject("無法安全讀取 Pack ZIP。") from exc
    return LoadedPack(manifest, hashlib.sha256(raw).hexdigest(), tuple(definitions), raw)
