import hashlib
import json

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.registry.builtin import TRANSFORMS, builtin_tools
from workflow_studio.registry.catalog import Catalog
from workflow_studio.storage.sqlite import SQLiteRepository


def test_builtin_transforms_are_deterministic():
    assert TRANSFORMS["builtin.copy"](b"hello", {}) == b"hello"
    assert TRANSFORMS["builtin.normalize"](b" a \r\n b ", {"strip": True}) == b"a\nb\n"
    assert json.loads(TRANSFORMS["builtin.json"](b'{"z":1,"a":2}', {})) == {"a": 2, "z": 1}
    report = json.loads(TRANSFORMS["builtin.report"](b"hello", {}))
    assert report == {"bytes": 5, "sha256": hashlib.sha256(b"hello").hexdigest()}
    for transform in TRANSFORMS.values():
        with pytest.raises(StudioError):
            transform(b"hello", {"shell": "anything"})


def test_catalog_crud_versions_and_reopen(tmp_path):
    repo = SQLiteRepository(tmp_path)
    catalog = Catalog(repo)
    catalog.seed()
    initial = len(builtin_tools())
    assert len(catalog.list()) == initial
    tool = builtin_tools()[0].model_copy(
        update={"tool_id": "custom.copy", "implementation": "untrusted.proposal"}
    )
    item = catalog.register(tool)
    assert item["trust_status"] == "untrusted"
    with pytest.raises(StudioError, match="已存在"):
        catalog.register(tool)
    new = tool.model_copy(update={"version": "1.1.0", "title": "更新版本"})
    catalog.register(new)
    old = catalog.set_status(tool.tool_id, tool.version, 1, "archived")
    assert old["status"] == "archived"
    assert len(catalog.list()) == initial + 1
    assert len(catalog.list(include_archived=True)) == initial + 2
    with pytest.raises(StudioError):
        catalog.set_status(tool.tool_id, tool.version, 1, "active")
    repo.close()
    repo = SQLiteRepository(tmp_path)
    assert Catalog(repo).get(new.tool_id, new.version)["tool"].title == "更新版本"
    repo.close()


def test_seed_does_not_reenable_disabled_tool(tmp_path):
    repo = SQLiteRepository(tmp_path)
    catalog = Catalog(repo)
    catalog.seed()
    catalog.set_status("file.copy", "1.0.0", 1, "disabled")
    catalog.seed()
    assert catalog.get("file.copy", "1.0.0")["status"] == "disabled"
    repo.close()
