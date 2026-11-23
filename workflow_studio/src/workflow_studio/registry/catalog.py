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

    @property
    def packs(self):
        from .packs import PackRegistry

        return PackRegistry(self)

    @staticmethod
    def _reviewer(actor, reason, *, required=False):
        if not required and actor is None and reason is None:
            return
        if (
            not isinstance(actor, str)
            or not 1 <= len(actor.strip()) <= 200
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 4000
        ):
            raise StudioError("INPUT_INVALID", "請填寫 1–200 字決策者及 1–4,000 字具體理由。")

    def _event(self, tool_id, version, action, actor=None, reason=None):
        item = self.get(tool_id, version)
        self.repository.connection.execute(
            "INSERT INTO studio_tool_events(tool_id,version,action,contract_hash,record_version,payload_json,actor,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                tool_id,
                version,
                action,
                item["contract_hash"],
                item["record_version"],
                canonical_json({"status": item["status"], "trust_status": item["trust_status"]}),
                actor.strip() if actor else None,
                reason.strip() if reason else None,
                now(),
            ),
        )

    def history(self, tool_id, version):
        self.get(tool_id, version)
        return {
            "events": [
                dict(row)
                for row in self.repository.connection.execute(
                    "SELECT * FROM studio_tool_events WHERE tool_id=? AND version=? ORDER BY sequence",
                    (tool_id, version),
                )
            ],
            "approvals": [
                dict(row)
                for row in self.repository.connection.execute(
                    "SELECT * FROM studio_tool_approvals WHERE tool_id=? AND version=? ORDER BY id",
                    (tool_id, version),
                )
            ],
        }

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
                self._event(tool.tool_id, tool.version, "seed")

    def register(self, tool: ToolDescription, *, actor=None, reason=None):
        self._reviewer(actor, reason)
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
            self._event(tool.tool_id, tool.version, "register", actor, reason)
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

    def set_status(self, tool_id, version, expected_revision, status, *, actor=None, reason=None):
        self._reviewer(actor, reason)
        if status not in {"active", "disabled", "archived"}:
            raise StudioError("INPUT_INVALID", "工具狀態無效。")
        with self.repository.transaction() as db:
            updated = db.execute(
                "UPDATE studio_tools SET status=?,record_version=record_version+1 WHERE tool_id=? AND version=? AND record_version=?",
                (status, tool_id, version, expected_revision),
            )
            if updated.rowcount != 1:
                raise StudioError("REVISION_CONFLICT", "工具紀錄已更新，請重新讀取。")
            self._event(tool_id, version, status, actor, reason)
        return self.get(tool_id, version)

    def approve(
        self, tool_id, version, expected_hash, *, actor: str, reason: str, expected_revision=None
    ):
        self._reviewer(actor, reason, required=True)
        with self.repository.transaction() as db:
            item = self.get(tool_id, version)
            from .handlers import HandlerLibrary

            HandlerLibrary(self.repository).get(tool_id, version)
            if (
                item["contract_hash"] != expected_hash
                or item["status"] != "active"
                or (expected_revision is not None and item["record_version"] != expected_revision)
            ):
                raise StudioError("REVISION_CONFLICT", "工具內容或狀態已變動，核准未套用。")
            if not item["tool"].test_evidence:
                raise StudioError("TEST_FAILED", "未提供工具測試證據。")
            db.execute(
                "INSERT INTO studio_tool_approvals(tool_id,version,contract_hash,actor,reason,created_at) VALUES (?,?,?,?,?,?)",
                (tool_id, version, expected_hash, actor, reason, now()),
            )
            db.execute(
                "UPDATE studio_tools SET trust_status=?,record_version=record_version+1 WHERE tool_id=? AND version=?",
                ("builtin" if item["trust_status"] == "builtin" else "approved", tool_id, version),
            )
            self._event(tool_id, version, "approve", actor, reason)
        return self.get(tool_id, version)

    def revoke(self, tool_id, version, expected_revision, *, actor, reason):
        self._reviewer(actor, reason, required=True)
        with self.repository.transaction() as db:
            item = self.get(tool_id, version)
            if item["record_version"] != expected_revision:
                raise StudioError("REVISION_CONFLICT", "工具紀錄已改變，撤回未套用。")
            if item["trust_status"] != "approved":
                raise StudioError(
                    "INPUT_INVALID", "此版本沒有可撤回的人工核准；內建工具請使用停用。"
                )
            db.execute(
                "UPDATE studio_tools SET trust_status='untrusted',record_version=record_version+1 WHERE tool_id=? AND version=?",
                (tool_id, version),
            )
            self._event(tool_id, version, "revoke", actor, reason)
        return self.get(tool_id, version)

    def support(self, tool):
        from ..compiler.compatibility import tool_support
        from .handlers import HandlerLibrary

        supported, reason = tool_support(tool)
        if supported:
            try:
                HandlerLibrary(self.repository).get(tool.tool_id, tool.version)
            except StudioError as exc:
                return False, str(exc)
        return supported, reason

    def available(self, policy):
        return {key: tool for key, tool in self.allowed(policy).items() if self.support(tool)[0]}

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
