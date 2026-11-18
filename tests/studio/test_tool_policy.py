import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.ir.models import Resources
from workflow_studio.registry.builtin import builtin_tools
from workflow_studio.registry.catalog import Catalog
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.storage.sqlite import SQLiteRepository


@pytest.mark.parametrize(
    "change",
    [
        {"permissions": ["other_project.read"]},
        {"resources": Resources(memory_mb=64, disk_mb=1, network=True)},
        {"side_effects": "external"},
        {"cost_micro_usd": 1},
    ],
)
def test_approval_does_not_grant_runtime_permissions(tmp_path, change):
    repo = SQLiteRepository(tmp_path)
    catalog = Catalog(repo)
    tool = builtin_tools()[0].model_copy(update={"tool_id": "custom.copy", **change})
    catalog.register(tool)
    catalog.approve(
        tool.tool_id, tool.version, tool.fingerprint, actor="reviewer", reason="test fixture"
    )
    assert catalog.allowed(ToolPolicy("windows")) == {}
    repo.close()


def test_changed_hash_no_evidence_and_unknown_version_rejected(tmp_path):
    repo = SQLiteRepository(tmp_path)
    catalog = Catalog(repo)
    tool = builtin_tools()[0].model_copy(update={"tool_id": "custom.copy", "test_evidence": []})
    catalog.register(tool)
    with pytest.raises(StudioError) as caught:
        catalog.approve(tool.tool_id, tool.version, "wrong", actor="reviewer", reason="fixture")
    assert caught.value.code == "REVISION_CONFLICT"
    with pytest.raises(StudioError) as caught:
        catalog.approve(
            tool.tool_id, tool.version, tool.fingerprint, actor="reviewer", reason="fixture"
        )
    assert caught.value.code == "TEST_FAILED"
    with pytest.raises(StudioError):
        catalog.get(tool.tool_id, "9.0.0")
    assert catalog.get(tool.tool_id, tool.version)["trust_status"] == "untrusted"
    repo.close()
