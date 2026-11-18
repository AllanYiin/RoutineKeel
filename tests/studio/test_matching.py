from workflow_studio.ir.models import Port
from workflow_studio.registry.builtin import builtin_tools
from workflow_studio.registry.catalog import Catalog
from workflow_studio.registry.matcher import match_tools
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.storage.sqlite import SQLiteRepository


def test_matching_contract_platform_trust_and_disabled(tmp_path):
    repo = SQLiteRepository(tmp_path)
    catalog = Catalog(repo)
    catalog.seed()
    policy = ToolPolicy("linux")
    inputs, outputs = {"source": Port(type="file")}, {"result": Port(type="file")}
    assert [t.tool_id for t in match_tools(catalog, "file.copy", inputs, outputs, policy)] == [
        "file.copy"
    ]
    assert match_tools(catalog, "file.copy", inputs, {"result": Port(type="integer")}, policy) == []
    tool = builtin_tools()[0].model_copy(
        update={"tool_id": "custom.copy", "platforms": ["windows"]}
    )
    catalog.register(tool)
    assert len(match_tools(catalog, "file.copy", inputs, outputs, policy)) == 1
    catalog.approve(
        tool.tool_id,
        tool.version,
        tool.fingerprint,
        actor="test reviewer",
        reason="fixture validation",
    )
    assert len(match_tools(catalog, "file.copy", inputs, outputs, policy)) == 1
    matches = match_tools(catalog, "file.copy", inputs, outputs, ToolPolicy("windows"))
    assert [t.tool_id for t in matches] == ["file.copy", "custom.copy"]
    catalog.set_status("file.copy", "1.0.0", 1, "disabled")
    assert match_tools(catalog, "file.copy", inputs, outputs, policy) == []
    repo.close()
