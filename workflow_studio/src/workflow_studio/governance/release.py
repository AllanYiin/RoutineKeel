import json
import os
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from ..compiler.generator import Compiler
from ..compiler.package import digest
from ..contracts import StudioError
from ..ingestion.security import safe_relative
from ..project.models import canonical_json
from ..project.service import now
from ..verifier.static import verify_static
from .approvals import Approvals
from .layout import workflow_folder
from .reviews import ReviewStore


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_target(root, relative):
    safe_relative(relative)
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise StudioError("PUBLISH_FAILED", "發布位置超出選取的工作區。")
    for path in (target, *target.parents):
        if path == root:
            break
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise StudioError("PUBLISH_FAILED", "發布位置不能經過符號連結。")
    return target


class Releases:
    def __init__(self, studio):
        self.studio = studio

    def current(self, target_workspace, workflow_id):
        root = Path(target_workspace).expanduser().resolve()
        records = safe_target(root, ".studio_releases/" + workflow_id)
        if not records.is_dir():
            return None
        try:
            with FileLock(
                str(safe_target(root, ".studio_releases/" + workflow_id + "/publish.lock")),
                timeout=0,
            ):
                return self._current(root, workflow_id)
        except Timeout as exc:
            raise StudioError("PUBLISH_BUSY", "發布工作區正被使用，未核對或變更目前版本。") from exc

    def _current(self, target_workspace, workflow_id):
        """Read/recover the pointer while the caller holds this workflow's publish lock."""
        root = Path(target_workspace).expanduser().resolve()
        journal = safe_target(root, ".studio_releases/" + workflow_id + "/journal.json")
        pointer = safe_target(root, ".studio_releases/" + workflow_id + "/current.json")
        prior_pointer = (
            json.loads(pointer.read_text(encoding="utf-8")) if pointer.is_file() else None
        )

        def owned_state(value):
            if not isinstance(value, dict):
                raise StudioError("PUBLISH_FAILED", "發布狀態檔不合法。")
            row = self.studio.repository.connection.execute(
                "SELECT * FROM studio_releases WHERE release_id=?", (value.get("release_id"),)
            ).fetchone()
            if (
                row is None
                or json.loads(row["payload_json"]) != value
                or Path(row["target_root"]).resolve() != root
                or value.get("workflow_id") != workflow_id
                or value.get("project_id") != row["project_id"]
            ):
                raise StudioError("PUBLISH_FAILED", "發布狀態檔不屬於此 Studio 或工作區。")
            return row

        if prior_pointer:
            if owned_state(prior_pointer)["status"] == "abandoned":
                raise StudioError("PUBLISH_FAILED", "正式指標指向已結束的準備，請核對完整備份。")
        if journal.is_file():
            pending = json.loads(journal.read_text(encoding="utf-8"))
            owned = owned_state(pending)
            if owned["status"] == "abandoned":
                return prior_pointer
            comparison = prior_pointer
            if comparison is None and pending.get("previous_release_id"):
                previous = self.studio.repository.connection.execute(
                    "SELECT payload_json FROM studio_releases WHERE release_id=?",
                    (pending["previous_release_id"],),
                ).fetchone()
                if previous:
                    comparison = json.loads(previous[0])
                    owned_state(comparison)
            active = safe_target(root, workflow_folder(root, workflow_id) + "/flow.json")
            applied = active.is_file() and digest(active.read_bytes()) == pending["manifest_hash"]
            if pending.get("state") == "disabled":
                saved = safe_target(
                    root, workflow_folder(root, workflow_id) + "/" + pending["disabled_manifest"]
                )
                applied = (
                    not active.exists()
                    and saved.is_file()
                    and digest(saved.read_bytes()) == pending["manifest_hash"]
                )
            if applied:
                from .recovery import file_hash

                checked_manifest = saved if pending.get("state") == "disabled" else active
                manifest = json.loads(checked_manifest.read_text(encoding="utf-8"))
                wheel = safe_target(
                    root, workflow_folder(root, workflow_id) + "/" + manifest["package"]
                )
                applied = wheel.is_file() and file_hash(wheel) == manifest["sha256"]
            if (
                applied
                and comparison
                and comparison["release_id"] != pending["release_id"]
                and comparison.get("state") == pending.get("state")
                and comparison["manifest_hash"] == pending["manifest_hash"]
            ):
                # Identical bytes do not prove a new approval was committed.
                applied = False
            if applied:
                atomic_bytes(pointer, canonical_json(pending).encode())
                with self.studio.repository.transaction() as db:
                    db.execute(
                        "UPDATE studio_releases SET status=? WHERE release_id=?",
                        (pending.get("state", "published"), pending["release_id"]),
                    )
                    if pending.get("state", "published") == "published":
                        db.execute(
                            "UPDATE studio_revisions SET status='published' WHERE revision_id=(SELECT revision_id FROM studio_builds WHERE build_id=?) AND status<>'published'",
                            (pending["build_id"],),
                        )
        return json.loads(pointer.read_text(encoding="utf-8")) if pointer.is_file() else None

    def publish(
        self,
        project_id,
        approval_id,
        target_workspace,
        policy,
        *,
        expected_current=None,
        historical=False,
    ):
        approval = Approvals(self.studio).get(project_id, approval_id)
        if approval["revoked_at"]:
            raise StudioError("APPROVAL_REQUIRED", "核准已撤回。")
        build = Compiler(self.studio).get(project_id, approval["build_id"])
        ReviewStore(self.studio.repository).assert_open(
            project_id, build["build_id"], for_approval=True
        )
        if (
            historical
            and not self.studio.repository.connection.execute(
                "SELECT 1 FROM studio_releases WHERE project_id=? AND build_id=? AND approval_id=? AND status='published'",
                (project_id, build["build_id"], approval_id),
            ).fetchone()
        ):
            raise StudioError("APPROVAL_REQUIRED", "歷史回復僅適用於先前成功發布的同一核准版本。")
        if build["status"] != "verified" or approval["package_hash"] != build["package_hash"]:
            raise StudioError("TEST_FAILED", "核准版本已失去驗證資格。")
        manifest, files = verify_static(self.studio, build, policy)
        workflow_id = manifest["id"]
        if workflow_id in {"flow_base", "share_states", "share_logs"}:
            raise StudioError("PUBLISH_FAILED", "工作流識別碼與保留目錄衝突。")
        project = self.studio.projects.get(project_id)
        revision = self.studio.projects.get_revision(project_id, build["revision_id"])
        if not historical:
            if project.status != "active":
                raise StudioError("PROJECT_INACTIVE", "請先恢復專案後再發布新版本。")
            from ..verifier.report import Verifier

            Approvals(self.studio).validate_cases(
                project_id, Verifier(self.studio).get(project_id, approval["report_id"])
            )
            if (
                project.current_revision_id != revision.revision_id
                or project.goal != revision.ir["goal"]
            ):
                raise StudioError("REVISION_CONFLICT", "專案已修改，請重新驗證並核准目前版本。")
            for evidence in revision.ir["evidence"]:
                source = self.studio.sources.get(project_id, evidence["source_id"])
                if source.version != evidence["source_version"] or source.status != "active":
                    raise StudioError("REVISION_CONFLICT", "來源已修改或移除，請重新分析與核准。")
        root = Path(target_workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        records = safe_target(root, ".studio_releases/" + workflow_id)
        records.mkdir(parents=True, exist_ok=True)
        with FileLock(
            str(safe_target(root, ".studio_releases/" + workflow_id + "/publish.lock")), timeout=10
        ):
            current = self._current(root, workflow_id)
            if current and current["project_id"] != project_id:
                raise StudioError("PUBLISH_FAILED", "此工作流識別碼已由另一個專案管理，未覆寫。")
            if (
                current
                and current["build_id"] == build["build_id"]
                and current["approval_id"] == approval_id
                and current.get("state") != "disabled"
            ):
                installed = safe_target(root, workflow_folder(root, workflow_id) + "/flow.json")
                installed_wheel = safe_target(
                    root, workflow_folder(root, workflow_id) + "/" + manifest["package"]
                )
                if (
                    installed.read_bytes() != files["flow.json"]
                    or not installed_wheel.is_file()
                    or digest(installed_wheel.read_bytes()) != manifest["sha256"]
                ):
                    raise StudioError("PUBLISH_FAILED", "已安裝版本被修改，未宣稱重複發布成功。")
                return current
            if (current["release_id"] if current else None) != expected_current:
                raise StudioError("REVISION_CONFLICT", "已安裝版本有更新，請重新讀取後發布。")
            active = safe_target(root, workflow_folder(root, workflow_id))
            manifest_path = safe_target(root, workflow_folder(root, workflow_id) + "/flow.json")
            if current:
                if current.get("state") == "disabled":
                    stored = safe_target(
                        root,
                        workflow_folder(root, workflow_id) + "/" + current["disabled_manifest"],
                    )
                    if (
                        manifest_path.exists()
                        or not stored.is_file()
                        or digest(stored.read_bytes()) != current["manifest_hash"]
                    ):
                        raise StudioError("PUBLISH_FAILED", "停用版本的快照不符，未覆寫。")
                elif (
                    not manifest_path.is_file()
                    or digest(manifest_path.read_bytes()) != current["manifest_hash"]
                ):
                    raise StudioError("PUBLISH_FAILED", "已安裝 manifest 被修改，未覆寫。")
            elif active.exists() and any(active.iterdir()):
                journal = records / "journal.json"
                pending = (
                    json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else {}
                )
                recoverable = pending.get("build_id") == build["build_id"] and all(
                    item.is_file()
                    and item.name == manifest["package"]
                    and digest(item.read_bytes()) == manifest["sha256"]
                    for item in active.iterdir()
                )
                if not recoverable:
                    from .recovery import only_abandoned_files

                    if not only_abandoned_files(self.studio, project_id, root, workflow_id):
                        raise StudioError("PUBLISH_FAILED", "目標目錄已有其他資料，未覆寫。")
            release_id = "release_" + uuid4().hex
            snapshot = records / release_id
            snapshot.mkdir()
            for name, content in files.items():
                atomic_bytes(snapshot / name, content)
            result = {
                "release_id": release_id,
                "project_id": project_id,
                "build_id": build["build_id"],
                "approval_id": approval_id,
                "workflow_id": workflow_id,
                "manifest_hash": digest(files["flow.json"]),
                "package_hash": build["package_hash"],
                "previous_release_id": expected_current,
                "created_at": now(),
                "state": "published",
            }
            with self.studio.repository.transaction() as db:
                db.execute(
                    "INSERT INTO studio_releases VALUES (?,?,?,?,?,?,?,?)",
                    (
                        release_id,
                        project_id,
                        build["build_id"],
                        approval_id,
                        str(root),
                        "prepared",
                        canonical_json(result),
                        now(),
                    ),
                )
            atomic_bytes(records / "journal.json", canonical_json(result).encode())
            active.mkdir(exist_ok=True)
            wheel_path = safe_target(
                root, workflow_folder(root, workflow_id) + "/" + manifest["package"]
            )
            if wheel_path.exists() and digest(wheel_path.read_bytes()) != manifest["sha256"]:
                raise StudioError("PUBLISH_FAILED", "同名 wheel 內容不同，未覆寫。")
            atomic_bytes(wheel_path, files[manifest["package"]])
            # Outputs and previous wheels remain intact. Switch executable metadata last.
            with self.studio.repository.transaction():
                ReviewStore(self.studio.repository).assert_open(
                    project_id, build["build_id"], for_approval=True
                )
                if Approvals(self.studio).get(project_id, approval_id)["revoked_at"]:
                    raise StudioError("APPROVAL_REQUIRED", "發布前核准已撤回。")
                verify_static(self.studio, build, policy)
                if not historical:
                    latest = self.studio.projects.get(project_id)
                    if latest.status != "active":
                        raise StudioError(
                            "PROJECT_INACTIVE", "發布準備期間專案已封存，未切換正式版本。"
                        )
                    if (
                        latest.current_revision_id != revision.revision_id
                        or latest.goal != revision.ir["goal"]
                    ):
                        raise StudioError(
                            "REVISION_CONFLICT", "發布準備期間專案已改變，未切換正式版本。"
                        )
                    Approvals(self.studio).validate_cases(
                        project_id, Verifier(self.studio).get(project_id, approval["report_id"])
                    )
                    for evidence in revision.ir["evidence"]:
                        source = self.studio.sources.get(project_id, evidence["source_id"])
                        if (
                            source.version != evidence["source_version"]
                            or source.status != "active"
                        ):
                            raise StudioError("REVISION_CONFLICT", "發布準備期間來源已改變。")
                atomic_bytes(manifest_path, files["flow.json"])
                atomic_bytes(records / "current.json", canonical_json(result).encode())
                applied = self._current(root, workflow_id)
            if applied is None or applied["release_id"] != release_id:
                raise StudioError("PUBLISH_FAILED", "版本指標尚未確認。")
            return applied

    def history(self, project_id):
        return [
            dict(row)
            for row in self.studio.repository.connection.execute(
                "SELECT * FROM studio_releases WHERE project_id=? ORDER BY created_at",
                (project_id,),
            )
        ]

    def disable(
        self,
        project_id,
        target_workspace,
        workflow_id,
        expected_current,
        *,
        actor=None,
        reason=None,
    ):
        from ..registry.catalog import Catalog

        Catalog._reviewer(actor, reason)
        root = Path(target_workspace).resolve()
        records = safe_target(root, ".studio_releases/" + workflow_id)
        if not records.is_dir():
            raise StudioError("PUBLISH_FAILED", "沒有此 Studio 管理的發布版本。")
        with FileLock(
            str(safe_target(root, ".studio_releases/" + workflow_id + "/publish.lock")), timeout=10
        ):
            current = self._current(root, workflow_id)
            if (
                not current
                or current["project_id"] != project_id
                or current["release_id"] != expected_current
            ):
                raise StudioError("REVISION_CONFLICT", "安裝版本已變動。")
            if current.get("state") == "disabled":
                return current
            manifest = safe_target(root, workflow_folder(root, workflow_id) + "/flow.json")
            if digest(manifest.read_bytes()) != current["manifest_hash"]:
                raise StudioError("PUBLISH_FAILED", "安裝的 manifest 不符，未停用。")
            identity = "release_" + uuid4().hex
            result = {
                **current,
                "release_id": identity,
                "previous_release_id": expected_current,
                "state": "disabled",
                "disabled_manifest": f"flow.disabled.{identity}.json",
                "created_at": now(),
                "actor": actor.strip() if actor else None,
                "reason": reason.strip() if reason else None,
            }
            with self.studio.repository.transaction() as db:
                db.execute(
                    "INSERT INTO studio_releases VALUES (?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        project_id,
                        current["build_id"],
                        current["approval_id"],
                        str(root),
                        "prepared",
                        canonical_json(result),
                        now(),
                    ),
                )
            atomic_bytes(records / "journal.json", canonical_json(result).encode())
            os.replace(
                manifest,
                safe_target(
                    root, workflow_folder(root, workflow_id) + "/" + result["disabled_manifest"]
                ),
            )
            return self._current(root, workflow_id)

    def rollback(self, project_id, release_id, policy):
        row = self.studio.repository.connection.execute(
            "SELECT * FROM studio_releases WHERE project_id=? AND release_id=? AND status='published'",
            (project_id, release_id),
        ).fetchone()
        if row is None:
            raise StudioError("PUBLISH_FAILED", "找不到可回復的成功發布版本。")
        payload = json.loads(row["payload_json"])
        current = self.current(row["target_root"], payload["workflow_id"])
        return self.publish(
            project_id,
            row["approval_id"],
            row["target_root"],
            policy,
            expected_current=current["release_id"] if current else None,
            historical=True,
        )
