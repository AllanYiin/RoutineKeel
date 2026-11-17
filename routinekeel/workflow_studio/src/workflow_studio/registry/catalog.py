import json

from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import now
from .builtin import builtin_tools
from .models import ToolDescription
from .policy import ToolPolicy


class Catalog:
    def __init__(self, repository):
        self.repository = repository

    def seed(self):
        with self.repository.transaction() as db:
            for tool in builtin_tools():
                # Never overwrite a user decision to disable an existing built-in version.
                existing = db.execute(
                    "SELECT contract_hash FROM studio_tools WHERE tool_id=? AND version=?",
                    (tool.tool_id, tool.version),
                ).fetchone()
                if existing:
                    if existing[0] != tool.fingerprint:
                        raise StudioError("REVISION_CONFLICT", "內建工具版本內容衝突，請新增版本。")
                    continue
                db.execute(
                    "INSERT INTO studio_tools VALUES (?,?,?,?,'builtin','active',1,?)",
                    (
                        tool.tool_id,
                        tool.version,
                        canonical_json(tool.model_dump()),
                        tool.fingerprint,
                        now(),
                    ),
                )

    def register(self, tool: ToolDescription):
        tool = ToolDescription.model_validate(tool.model_dump())
        with self.repository.transaction() as db:
            if db.execute(
                "SELECT 1 FROM studio_tools WHERE tool_id=? AND version=?",
                (tool.tool_id, tool.version),
            ).fetchone():
                raise StudioError("REVISION_CONFLICT", "工具版本已存在，修改請註冊新版本。")
            db.execute(
                "INSERT INTO studio_tools VALUES (?,?,?,?,'untrusted','active',1,?)",
                (
                    tool.tool_id,
                    tool.version,
                    canonical_json(tool.model_dump()),
                    tool.fingerprint,
                    now(),
                ),
            )
        return self.get(tool.tool_id, tool.version)

    def get(self, tool_id: str, version: str) -> dict:
        row = self.repository.connection.execute(
            "SELECT * FROM studio_tools WHERE tool_id=? AND version=?", (tool_id, version)
        ).fetchone()
        if row is None:
            raise StudioError("TOOL_NOT_ALLOWED", "工具版本不存在。")
        result = dict(row)
        result["tool"] = ToolDescription.model_validate(json.loads(result.pop("contract_json")))
        if result["tool"].fingerprint != result["contract_hash"]:
            raise StudioError("TOOL_NOT_ALLOWED", "工具契約指紋不符。")
        return result

    def list(self, *, include_archived=False) -> list[dict]:
        return [
            self.get(row[0], row[1])
            for row in self.repository.connection.execute(
                "SELECT tool_id,version FROM studio_tools WHERE status<>'archived' OR ? ORDER BY tool_id,version",
                (include_archived,),
            )
        ]

    def set_status(self, tool_id, version, expected_revision, status):
        if status not in {"active", "disabled", "archived"}:
            raise StudioError("INPUT_INVALID", "工具狀態無效。")
        with self.repository.transaction() as db:
            updated = db.execute(
                "UPDATE studio_tools SET status=?,record_version=record_version+1 WHERE tool_id=? AND version=? AND record_version=?",
                (status, tool_id, version, expected_revision),
            )
            if updated.rowcount != 1:
                raise StudioError("REVISION_CONFLICT", "工具紀錄已更新，請重新讀取。")
        return self.get(tool_id, version)

    def approve(self, tool_id, version, expected_hash, *, actor: str, reason: str):
        if not actor.strip() or not reason.strip():
            raise StudioError("INPUT_INVALID", "核准須記錄決策者及理由。")
        with self.repository.transaction() as db:
            item = self.get(tool_id, version)
            if item["contract_hash"] != expected_hash or item["status"] != "active":
                raise StudioError("REVISION_CONFLICT", "工具內容或狀態已變動，核准未套用。")
            if not item["tool"].test_evidence:
                raise StudioError("TEST_FAILED", "未提供工具測試證據。")
            db.execute(
                "INSERT INTO studio_tool_approvals(tool_id,version,contract_hash,actor,reason,created_at) VALUES (?,?,?,?,?,?)",
                (tool_id, version, expected_hash, actor, reason, now()),
            )
            db.execute(
                "UPDATE studio_tools SET trust_status='approved',record_version=record_version+1 WHERE tool_id=? AND version=?",
                (tool_id, version),
            )
        return self.get(tool_id, version)

    def allowed(self, policy: ToolPolicy) -> dict[str, ToolDescription]:
        result = {}
        for item in self.list():
            try:
                policy.check(item["tool"], trust=item["trust_status"], status=item["status"])
            except StudioError:
                continue
            tool = item["tool"]
            result[f"{tool.tool_id}@{tool.version}"] = tool
        return result
