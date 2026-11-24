"""Use the installed Documa CLI for document parsing and ContextIR navigation."""

from pathlib import Path
from tempfile import TemporaryDirectory

from routinekeel_contracts.base_steps import VERSION
from routinekeel_contracts.documa import (
    DOCUMENT_EXTENSIONS,
    build_document_index,
    call_documa,
    documa_cli,
    search_document_index,
)
from routinekeel_contracts.errors import StudioError

from ..blueprints.sessions import refs
from ..runtime import FlowError, sha256
from .base_steps import cached_step


def _build(executable, assets, source, version):
    with TemporaryDirectory(prefix="rk-documa-", dir=assets.root) as folder:
        document, ir_path, index_path = build_document_index(
            executable, source, Path(folder)
        )
        return {
            "version": VERSION,
            "provider_version": version,
            **document,
            "ir": assets.store(ir_path),
            "index": assets.store(index_path),
        }


def index_documents(runtime, assets, inputs):
    documents = sorted(
        {
            token
            for token in refs(inputs)
            if Path(token[6:]).suffix in DOCUMENT_EXTENSIONS
        }
    )
    if not documents:
        return {}

    def intact(record):
        try:
            assets.resolve(record["ir"])
            assets.resolve(record["index"])
        except (FlowError, OSError, KeyError, TypeError):
            return False
        return True

    indexes = {}
    try:
        executable = documa_cli(runtime.workspace_root)
        version = call_documa(executable, "--version", directory=assets.root)[
            "documa_version"
        ]
        implementation = sha256(executable)
        for token in documents:
            source = assets.resolve(token)
            record, _ = cached_step(
                runtime,
                "base.document.index",
                [token, version, implementation],
                lambda source=source: _build(executable, assets, source, version),
                valid=intact,
            )
            indexes[token] = record
    except StudioError as exc:
        raise FlowError(exc.code, str(exc)) from exc
    return indexes


def search_indexes(runtime, assets, indexes, query, *, route="auto"):
    if not 1 <= len(query.strip()) <= 1000 or len(indexes) > 100:
        raise FlowError(
            "DOCUMENT_QUERY", "查詢須有 1–1000 個字元，每次至多查詢 100 份文件。"
        )
    if not indexes:
        return []
    executable = documa_cli(runtime.workspace_root)
    matches = []
    for token, record in sorted(indexes.items()):
        assets.resolve(token)
        context = assets.resolve(record["index"])
        found = search_document_index(
            executable, context, query, directory=assets.root, route=route
        )
        matches.append(
            {
                "source": token,
                "index": record["index"],
                "document_id": record["document_id"],
                **found,
            }
        )
    return matches
