"""Reconcile observed publication state without deleting artifacts or installing new code."""

import hashlib
import json
import re
from pathlib import Path

from filelock import FileLock, Timeout

from ..compiler.package import digest
from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import now
from .layout import workflow_folder
from .release import atomic_bytes, safe_target


def file_hash(path):
    hasher, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise StudioError("PUBLISH_FAILED", "核對檔案超過 128 MiB 上限，未改動狀態。")
            hasher.update(chunk)
    return hasher.hexdigest()


class ReleaseRecovery:
    def __init__(self, studio):
        self.studio = studio

    def row(self, project_id, release_id):
        row = self.studio.repository.connection.execute(
            "SELECT * FROM studio_releases WHERE project_id=? AND release_id=?",
            (project_id, release_id),
        ).fetchone()
        if row is None:
            raise StudioError("PUBLISH_FAILED", "找不到此專案的發布紀錄。")
        return dict(row)

    def _owned(self, payload, root, workflow_id, project_id):
        if not isinstance(payload, dict) or not isinstance(payload.get("release_id"), str):
            raise StudioError("PUBLISH_FAILED", "發布狀態檔不合法。")
        row = self.row(project_id, payload["release_id"])
        if (
            json.loads(row["payload_json"]) != payload
            or Path(row["target_root"]).resolve() != root
            or payload.get("workflow_id") != workflow_id
            or payload.get("project_id") != project_id
        ):
            raise StudioError("PUBLISH_FAILED", "發布狀態檔與此工作區的紀錄不符。")
        return row

    @staticmethod
    def _json(path):
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise StudioError("PUBLISH_FAILED", "發布狀態檔大小或類型不合法。")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise StudioError("PUBLISH_FAILED", "發布狀態檔無法解析，未改動檔案。") from exc

    def _manifest(self, root, workflow_id, payload):
        name = "flow.json" if payload["state"] == "published" else payload["disabled_manifest"]
        path = safe_target(root, workflow_folder(root, workflow_id) + "/" + name)
        active = safe_target(root, workflow_folder(root, workflow_id) + "/flow.json")
        if payload["state"] == "disabled" and active.exists():
            return False
        if not path.is_file() or file_hash(path) != payload["manifest_hash"]:
            return False
        manifest = self._json(path)
        if not isinstance(manifest, dict) or manifest.get("id") != workflow_id:
            return False
        wheel = safe_target(root, workflow_folder(root, workflow_id) + "/" + manifest["package"])
        return wheel.is_file() and file_hash(wheel) == manifest["sha256"]

    def _observe(self, project_id, release_id):
        from .reviews import ReviewStore

        row = self.row(project_id, release_id)
        payload = json.loads(row["payload_json"])
        root, workflow_id = Path(row["target_root"]).resolve(), payload["workflow_id"]
        self._owned(payload, root, workflow_id, project_id)
        records = safe_target(root, ".studio_releases/" + workflow_id)
        pointer = self._json(safe_target(root, ".studio_releases/" + workflow_id + "/current.json"))
        journal = self._json(safe_target(root, ".studio_releases/" + workflow_id + "/journal.json"))
        if pointer:
            self._owned(pointer, root, workflow_id, project_id)
        if journal:
            self._owned(journal, root, workflow_id, project_id)
        previous = None
        if payload.get("previous_release_id"):
            previous_row = self.row(project_id, payload["previous_release_id"])
            previous = json.loads(previous_row["payload_json"])
            self._owned(previous, root, workflow_id, project_id)
        matches_selected = self._manifest(root, workflow_id, payload)
        matches_pointer = bool(pointer and self._manifest(root, workflow_id, pointer))
        matches_previous = bool(previous and self._manifest(root, workflow_id, previous))
        current_matches_previous = (pointer or {}).get("release_id") == payload.get(
            "previous_release_id"
        )
        active = safe_target(root, workflow_folder(root, workflow_id) + "/flow.json")
        same_content = bool(
            previous
            and previous["state"] == payload["state"]
            and previous["manifest_hash"] == payload["manifest_hash"]
        )
        outcome, explanation = "unknown", "檔案與已知版本不一致，不能推定是否生效。"
        if row["status"] != "prepared":
            outcome, explanation = "resolved", "此紀錄已完成或已結束準備。"
        elif pointer and pointer["release_id"] == release_id and matches_selected:
            outcome, explanation = "applied", "正式指標及完整套件已生效，可補齊資料庫紀錄。"
        elif journal and journal["release_id"] == release_id:
            if (
                matches_selected
                and not same_content
                and (current_matches_previous or pointer is None)
            ):
                outcome, explanation = "applied", "完整套件已生效，但正式指標或資料庫紀錄尚未完成。"
            elif (current_matches_previous and matches_pointer) or (
                pointer is None and matches_previous
            ):
                outcome, explanation = (
                    "not_applied",
                    "目前仍是先前版本，本次準備未提交新的正式指標。",
                )
            elif previous is None and pointer is None and not active.exists():
                outcome, explanation = (
                    "not_applied",
                    "首次發布尚未有正式 manifest，本次準備未生效。",
                )
        elif (current_matches_previous and matches_pointer) or (
            previous is None and pointer is None and journal is None and not active.exists()
        ):
            outcome, explanation = "not_applied", "本次準備尚未取得 journal，現有版本保持不變。"
        if row["status"] == "prepared" and outcome == "unknown":
            if pointer and pointer["release_id"] != release_id and matches_pointer:
                outcome, explanation = (
                    "not_applied",
                    "目前由另一個完整且已提交的版本管理，本準備可結束。",
                )
            elif previous is None and pointer is None and not active.exists():
                outcome, explanation = (
                    "not_applied",
                    "目前沒有正式 manifest；結束此準備不會改動其他準備或檔案。",
                )
        approval = self.studio.repository.connection.execute(
            "SELECT revoked_at FROM studio_approvals WHERE project_id=? AND approval_id=?",
            (project_id, row["approval_id"]),
        ).fetchone()
        review = ReviewStore(self.studio.repository).state(project_id, row["build_id"])
        result = {
            "schema_version": 1,
            "project_id": project_id,
            "release_id": release_id,
            "target_root": str(root),
            "workflow_id": workflow_id,
            "record_status": row["status"],
            "approval_unavailable": approval is None or approval["revoked_at"] is not None,
            "review_blocked": review["archived"] or review["returned"],
            "payload_hash": digest(row["payload_json"].encode()),
            "pointer": pointer,
            "journal": journal,
            "previous": previous,
            "matches_selected": matches_selected,
            "matches_pointer": matches_pointer,
            "matches_previous": matches_previous,
            "active_exists": active.exists(),
            "active_hash": file_hash(active) if active.is_file() else None,
            "outcome": outcome,
            "explanation": explanation,
        }
        result["observation_hash"] = digest(canonical_json(result).encode())
        return result, row, payload, records

    def _lock(self, project_id, release_id):
        row = self.row(project_id, release_id)
        payload = json.loads(row["payload_json"])
        root = Path(row["target_root"]).resolve()
        records = safe_target(root, ".studio_releases/" + payload["workflow_id"])
        if not records.is_dir():
            raise StudioError(
                "PUBLISH_FAILED", "找不到原發布工作區；未推定準備未生效，請先核對位置。"
            )
        return FileLock(
            str(safe_target(root, ".studio_releases/" + payload["workflow_id"] + "/publish.lock")),
            timeout=0,
        )

    def inspect(self, project_id, release_id):
        try:
            with self._lock(project_id, release_id):
                return self._observe(project_id, release_id)[0]
        except Timeout as exc:
            raise StudioError(
                "PUBLISH_BUSY", "發布工作區正被使用；未改動紀錄，請待操作結束後再核對。"
            ) from exc

    def reconcile(self, project_id, release_id, expected_observation, *, actor, reason):
        from ..registry.catalog import Catalog

        Catalog._reviewer(actor, reason, required=True)
        try:
            with self._lock(project_id, release_id), self.studio.repository.transaction() as db:
                observed, row, payload, records = self._observe(project_id, release_id)
                if observed["observation_hash"] != expected_observation:
                    raise StudioError("REVISION_CONFLICT", "核對後狀態已變動，請重新檢查。")
                outcome = observed["outcome"]
                if outcome not in {"applied", "not_applied"}:
                    raise StudioError("PUBLISH_FAILED", observed["explanation"])
                if outcome == "applied":
                    atomic_bytes(records / "current.json", canonical_json(payload).encode())
                    status = payload["state"]
                    if status == "published":
                        db.execute(
                            "UPDATE studio_revisions SET status='published' WHERE revision_id=(SELECT revision_id FROM studio_builds WHERE build_id=?) AND status<>'published'",
                            (row["build_id"],),
                        )
                else:
                    status = "abandoned"
                    if observed["pointer"] is None and observed["matches_previous"]:
                        atomic_bytes(
                            records / "current.json", canonical_json(observed["previous"]).encode()
                        )
                db.execute(
                    "UPDATE studio_releases SET status=? WHERE project_id=? AND release_id=?",
                    (status, project_id, release_id),
                )
                db.execute(
                    "INSERT INTO studio_release_reconciliations(project_id,release_id,observation_hash,observation_json,outcome,actor,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        project_id,
                        release_id,
                        expected_observation,
                        canonical_json(observed),
                        status,
                        actor.strip(),
                        reason.strip(),
                        now(),
                    ),
                )
                return {"release_id": release_id, "status": status, "files_preserved": True}
        except Timeout as exc:
            raise StudioError("PUBLISH_BUSY", "發布工作區正被使用，核對未套用。") from exc


def only_abandoned_files(studio, project_id, root, workflow_id):
    """Allow a new first publication beside proven leftovers; never remove them."""
    known, prefixes = {}, {}
    for row in studio.repository.connection.execute(
        "SELECT release_id,payload_json FROM studio_releases WHERE project_id=? AND target_root=? AND status='abandoned'",
        (project_id, str(root)),
    ):
        payload = json.loads(row["payload_json"])
        if payload.get("workflow_id") != workflow_id or payload.get("state") != "published":
            continue
        snapshot = safe_target(root, ".studio_releases/" + workflow_id + "/" + row["release_id"])
        manifest_path = safe_target(root, snapshot.relative_to(root).as_posix() + "/flow.json")
        if not manifest_path.is_file() or file_hash(manifest_path) != payload["manifest_hash"]:
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        wheel = safe_target(root, snapshot.relative_to(root).as_posix() + "/" + manifest["package"])
        if wheel.is_file() and file_hash(wheel) == manifest["sha256"]:
            known[manifest["package"]] = manifest["sha256"]
            prefixes.setdefault(manifest["package"], []).append(wheel)
            prefixes.setdefault("flow.json", []).append(manifest_path)
    active = safe_target(root, workflow_folder(root, workflow_id))
    if not known:
        return False
    for item in active.iterdir():
        safe_target(root, workflow_folder(root, workflow_id) + "/" + item.name)
        if not item.is_file():
            return False
        if item.name in known and file_hash(item) == known[item.name]:
            continue
        match = re.fullmatch(r"(.+)\.[0-9a-f]{32}\.tmp", item.name)
        if not match or match[1] not in prefixes:
            return False
        owned = False
        for expected in prefixes[match[1]]:
            if item.stat().st_size > expected.stat().st_size:
                continue
            with item.open("rb") as actual, expected.open("rb") as reference:
                while chunk := actual.read(1024 * 1024):
                    if chunk != reference.read(len(chunk)):
                        break
                else:
                    owned = True
            if owned:
                break
        if not owned:
            return False
    return True
