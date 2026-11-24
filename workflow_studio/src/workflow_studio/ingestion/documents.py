"""Documa previews and graph search for versioned Studio source snapshots."""

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from routinekeel_contracts.documa import build_document_index, documa_cli, search_document_index

from .models import Preview
from .security import reject_links


def materialize_source(root, name, payload):
    folder = root / "documa_sources"
    folder.mkdir(exist_ok=True)
    reject_links(folder)
    fingerprint = hashlib.sha256(payload).hexdigest()
    path = folder / (fingerprint + Path(name).suffix.lower())
    try:
        reject_links(path)
    except FileNotFoundError:
        pass
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint:
        with NamedTemporaryFile(dir=folder, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def _preview(navigation, limit):
    text = "\n\n".join(block["body"] for block in navigation["blocks"] if block.get("body"))
    return Preview("document", text[:limit], truncated=len(text) > limit)


def preview_document(workspace, path, limit):
    executable = documa_cli(workspace)
    with TemporaryDirectory(prefix="documa-preview-") as temporary:
        _, _, context = build_document_index(executable, path, Path(temporary))
        return _preview(json.loads(context.read_bytes()), limit)


def index_source(root, name, payload, limit):
    executable = documa_cli(root)
    source = materialize_source(root, name, payload)
    with TemporaryDirectory(prefix="documa-", dir=root) as temporary:
        metadata, ir, context = build_document_index(executable, source, Path(temporary))
        ir_bytes, context_bytes = ir.read_bytes(), context.read_bytes()
    preview = _preview(json.loads(context_bytes), limit)
    blobs = {}
    for key, content in (("ir", ir_bytes), ("index", context_bytes)):
        fingerprint = hashlib.sha256(content).hexdigest()
        metadata[key + "_sha256"] = fingerprint
        blobs[fingerprint] = content
    return preview, metadata, blobs


def search_source(root, name, payload, context, query, *, route="auto"):
    executable = documa_cli(root)
    source = materialize_source(root, name, payload)
    with TemporaryDirectory(prefix="documa-query-", dir=root) as temporary:
        folder = Path(temporary)
        index = folder / "context.json"
        if json.loads(context).get("metadata", {}).get("sourceName") == str(source):
            index.write_bytes(context)
        else:
            _, _, index = build_document_index(executable, source, folder)
        return search_document_index(executable, index, query, directory=folder, route=route)
