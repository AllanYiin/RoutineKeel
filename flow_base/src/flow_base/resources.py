"""Shared, content-verified assets. No model downloads on import."""
from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import urllib.error
import time
from pathlib import Path

from .contracts import Resource
from .runtime import FlowError, Runtime, atomic_json, digest, read_json, require_space, sha256, event


def resource_key(resource: Resource):
    return digest(resource.model_dump())


def resource_path(runtime: Runtime, resource: Resource):
    return runtime.home / "resources" / resource_key(resource)


def check_resource(runtime, resource):
    root = resource_path(runtime, resource)
    missing = []
    for item in resource.files:
        path = root / item.path
        if not path.is_file() or path.stat().st_size != item.size or sha256(path) != item.sha256:
            missing.append(item.path)
    return {"id": resource.id, "ready": not missing, "missing": missing,
            "download_bytes": sum(f.size for f in resource.files if f.path in missing), "path": str(root)}


def ensure_resource(runtime: Runtime, resource: Resource, *, offline=False, reserve_bytes=0):
    from .core.run_context import current
    run_context = current(runtime)
    sink = run_context.event_sink if run_context else None
    if sink:
        sink.record('resource.verify_started', attributes={'resource_id': resource.id})
    runtime.initialize()
    root = resource_path(runtime, resource)
    with runtime.lock("resource:" + resource_key(resource), timeout=600):
        status = check_resource(runtime, resource)
        if status["ready"]:
            if sink:
                sink.record('resource.verified', attributes={'resource_id': resource.id})
            return root
        if sink:
            sink.record('resource.invalid', severity='WARNING', attributes={'resource_id': resource.id, 'reason': 'missing_or_hash_mismatch'})
        if offline:
            raise FlowError("OFFLINE_RESOURCE_MISSING", "Required model is not cached", resource=resource.id, missing=status["missing"])
        require_space(root, status["download_bytes"] * 2 + reserve_bytes)
        root.mkdir(parents=True, exist_ok=True)
        for item in resource.files:
            if item.path not in status["missing"]:
                continue
            path = root / item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".part")
            # Restart a partial file rather than trusting a server's Range behavior.
            try:
                event('download_start', resource=resource.id, file=item.path, total_bytes=item.size)
                req = urllib.request.Request(item.url, headers={"User-Agent": "flow_base/0.2"})
                with urllib.request.urlopen(req, timeout=60) as response, temp.open("wb") as out:
                    if not response.geturl().startswith("https://"):
                        raise FlowError("RESOURCE_REDIRECT", "Resource redirected to a non-HTTPS URL")
                    h, total = hashlib.sha256(), 0
                    last_progress = time.monotonic()
                    while block := response.read(1024 * 1024):
                        total += len(block)
                        if total > item.size:
                            raise FlowError("RESOURCE_SIZE", "Download exceeds declared size", resource=resource.id)
                        h.update(block)
                        out.write(block)
                        if time.monotonic() - last_progress >= 5:
                            event('download_progress', resource=resource.id, file=item.path, received_bytes=total,
                                  total_bytes=item.size, percent=round(total / item.size * 100, 1))
                            last_progress = time.monotonic()
                if total != item.size or h.hexdigest() != item.sha256:
                    raise FlowError("RESOURCE_HASH", "Downloaded file failed integrity check", file=item.path)
                temp.replace(path)
                event('download_verified', resource=resource.id, file=item.path, received_bytes=total, sha256=item.sha256)
            except (OSError, urllib.error.URLError) as e:
                raise FlowError("DOWNLOAD_FAILED", "Download failed; retry prepare to recover", file=item.path,
                                reason=type(e).__name__) from e
        atomic_json(root / "resource.json", resource.model_dump())
        if sink:
            sink.record('resource.verified', attributes={'resource_id': resource.id})
        return root


def export_resources(runtime, destination: Path, ids: list[str], catalogue):
    """Portable model-only archive: environments and credentials never exported."""
    import zipfile
    if destination.exists():
        raise FlowError("OUTPUT_EXISTS", "Cache archive already exists")
    selected = [catalogue[name] for name in ids]
    runtime.initialize()
    for r in selected:
        if not check_resource(runtime, r)["ready"]:
            raise FlowError("RESOURCE_MISSING", "Prepare model before exporting", resource=r.id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", json.dumps([r.model_dump() for r in selected]))
        for r in selected:
            with runtime.lock("resource:" + resource_key(r), timeout=60):
                for f in r.files:
                    z.write(resource_path(runtime, r) / f.path, resource_key(r) + "/" + f.path)
    temp.replace(destination)
    return {"archive": str(destination), "resources": ids}


def import_resources(runtime, source: Path, catalogue):
    import zipfile
    runtime.initialize()
    allowed = {resource_key(r): r for r in catalogue.values()}
    with zipfile.ZipFile(source) as z:
        info = z.getinfo("manifest.json")
        if info.file_size > 1024 * 1024:
            raise FlowError("ARCHIVE_INVALID", "Oversized archive manifest")
        resources = [Resource.model_validate(r) for r in json.loads(z.read(info))]
        names = z.namelist()
        if len(names) != len(set(names)):
            raise FlowError("ARCHIVE_INVALID", "Duplicate archive entries")
        for r in resources:
            key = resource_key(r)
            if key not in allowed:
                raise FlowError("ARCHIVE_UNTRUSTED", "Archive resource is not in this release's catalogue")
            require_space(runtime.home, sum(f.size for f in r.files))
            with runtime.lock("resource:" + key, timeout=60):
                root = resource_path(runtime, r)
                for f in r.files:
                    member = z.getinfo(key + "/" + f.path)
                    if member.file_size != f.size:
                        raise FlowError("ARCHIVE_INVALID", "Unexpected entry size")
                    target = root / f.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_name(target.name + ".part")
                    with z.open(member) as src, temp.open("wb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                    if sha256(temp) != f.sha256:
                        temp.unlink()
                        raise FlowError("RESOURCE_HASH", "Archive file failed integrity check")
                    temp.replace(target)
                atomic_json(root / "resource.json", r.model_dump())
    return {"imported": [r.id for r in resources]}
