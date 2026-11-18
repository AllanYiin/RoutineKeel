"""Verified means one persisted Runtime replay and its exact sample comparison."""

import copy
import io
import json
import re
import zipfile

from ..compiler.package import digest
from ..contracts import StudioError
from ..ingestion.service import SourceService
from ..project.models import canonical_json
from ..project.service import ProjectService
from ..verifier.comparison import compare
from ..verifier.replay import evidence_file, output_bytes


def execution_basis(payload):
    value = copy.deepcopy(payload)
    value.pop("revision", None)
    value.pop("evidence", None)
    for node in value.get("nodes", []):
        if isinstance(node, dict):
            node.pop("evidence_refs", None)
    return digest(canonical_json(value).encode())


class MachineEvidence:
    def __init__(self, repository):
        self.repository = repository

    def inspect(self, project_id, identity, payload):
        try:
            return self._inspect(project_id, identity, payload)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise StudioError(
                "EVIDENCE_INSUFFICIENT", "試做證據缺少檔案或內容不完整，請重新試做。"
            ) from exc

    def _inspect(self, project_id, identity, payload):
        match = re.fullmatch(r"replay:(verification_[a-f0-9]{32}):([1-9][0-9]{0,3})", identity)
        if not match:
            raise StudioError("EVIDENCE_INSUFFICIENT", "機器驗證引用格式無效。")
        report_id, ordinal = match[1], int(match[2])
        row = self.repository.connection.execute(
            "SELECT v.report_json,b.relative_path,b.package_hash,b.revision_id FROM studio_verifications v JOIN studio_builds b ON b.build_id=v.build_id AND b.project_id=v.project_id WHERE v.project_id=? AND v.report_id=?",
            (project_id, report_id),
        ).fetchone()
        if row is None:
            raise StudioError("EVIDENCE_INSUFFICIENT", "找不到此專案的試做報告。")
        report = json.loads(row["report_json"])
        original = ProjectService(self.repository).get_revision(project_id, row["revision_id"]).ir
        if execution_basis(original) != execution_basis(payload):
            raise StudioError(
                "EVIDENCE_INSUFFICIENT", "處理內容已改變，不能沿用先前試做的機器驗證狀態。"
            )
        if (
            report["status"] != "passed"
            or report["static"] != "passed"
            or report["package_hash"] != row["package_hash"]
        ):
            raise StudioError("EVIDENCE_INSUFFICIENT", "僅可引用完整通過且套件指紋相符的試做報告。")
        archives = list((self.repository.root / row["relative_path"]).glob("*.zip"))
        if len(archives) != 1:
            raise StudioError("EVIDENCE_INSUFFICIENT", "原試做套件已缺失或指紋不符。")
        archive_bytes = evidence_file(
            self.repository.root,
            row["relative_path"] + "/" + archives[0].name,
            limit=128 * 1024 * 1024,
        )
        if digest(archive_bytes) != row["package_hash"]:
            raise StudioError("EVIDENCE_INSUFFICIENT", "原試做套件已缺失或指紋不符。")
        result = report["cases"][ordinal - 1]
        if result["status"] != "passed" or not {
            "run.started",
            "delivery.published",
            "run.finished",
        } <= set(result["lifecycle"]):
            raise StudioError("EVIDENCE_INSUFFICIENT", "案例缺少通過結果或 Runtime 生命週期。")
        case_row = self.repository.connection.execute(
            "SELECT spec_json FROM studio_test_cases WHERE project_id=? AND case_id=? AND version=?",
            (project_id, result["case_id"], result["version"]),
        ).fetchone()
        if case_row is None:
            raise StudioError("EVIDENCE_INSUFFICIENT", "案例歷史版本已缺失。")
        specification = json.loads(case_row[0])
        if (
            digest(canonical_json(specification).encode()) != result["acceptance_hash"]
            or specification["comparison"] != result["comparison"]
        ):
            raise StudioError("EVIDENCE_INSUFFICIENT", "案例驗收依據與報告不符。")
        sources = SourceService(self.repository)
        contents = {}
        for side in ("input", "expected"):
            ref = specification[side]
            contents[side] = sources.content(
                project_id, ref["source_id"], ref["path"], version=ref["version"]
            )
            if (
                digest(contents[side]) != ref["sha256"]
                or digest(contents[side]) != result[f"{side}_sha256"]
            ):
                raise StudioError("EVIDENCE_INSUFFICIENT", "固定案例來源指紋與報告不符。")
        relative = row["relative_path"] + "/verification/" + report_id
        if json.loads(evidence_file(self.repository.root, relative + "/report.json")) != report:
            raise StudioError("EVIDENCE_INSUFFICIENT", "保存的報告檔案與資料庫不一致。")
        # Read the stored Runtime result; do not execute code or request a model.
        case_relative = relative + f"/case-{ordinal}"
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for entry in archive.infolist():
                if entry.file_size > 10 * 1024 * 1024:
                    raise StudioError("EVIDENCE_INSUFFICIENT", "原套件單檔超過核對上限。")
                name = entry.filename.split("/", 1)[1]
                if archive.read(entry) != evidence_file(
                    self.repository.root, case_relative + "/generated/" + name
                ):
                    raise StudioError(
                        "EVIDENCE_INSUFFICIENT", "Runtime 使用的套件副本與原建置不符。"
                    )
        runtime = json.loads(
            evidence_file(self.repository.root, case_relative + "/runtime-result.json")
        )
        if (
            runtime["status"] != "complete"
            or runtime["run_id"] != result["run_id"]
            or runtime["lifecycle"] != result["lifecycle"]
        ):
            raise StudioError("EVIDENCE_INSUFFICIENT", "Runtime 執行紀錄與報告不符。")
        if (
            digest(evidence_file(self.repository.root, case_relative + "/input.bin"))
            != result["input_sha256"]
        ):
            raise StudioError("EVIDENCE_INSUFFICIENT", "Runtime 輸入與固定案例不符。")
        actual = output_bytes(
            self.repository.root / case_relative, runtime, result["output"], original
        )
        if (
            digest(actual) != result["actual_sha256"]
            or not compare(result["comparison"], contents["expected"], actual)[0]
        ):
            raise StudioError(
                "EVIDENCE_INSUFFICIENT", "保存的實際產物已變動或無法重現報告的比對結果。"
            )
        ref = specification["expected"]
        rule = (
            "位元組完全相同"
            if result["comparison"] == "exact_bytes"
            else "JSON 內容相同（忽略排版）"
        )
        evidence = {
            "id": f"replay_{report_id.removeprefix('verification_')}_{ordinal}",
            "source_id": ref["source_id"],
            "source_version": ref["version"],
            "evidence_locator": ref["path"],
            "claim": f"固定案例「{specification['name']}」第 {result['version']} 版：執行 {result['run_id']} 的 {result['output']} 交付物與預期成果按「{rule}」比對通過。輸入 SHA-256 {result['input_sha256']}；實際成果 SHA-256 {result['actual_sha256']}；報告 {report_id}。僅證明這次執行及固定案例，不代表所有輸入或業務規則皆正確。",
            "confidence_label": "verified",
            "verified_by": identity,
        }
        return {
            "evidence": evidence,
            "report_id": report_id,
            "case": result,
            "specification": specification,
            "execution_basis": execution_basis(original),
        }

    def valid_ids(self, project_id, payload):
        valid = set()
        evidence = payload.get("evidence", [])
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, dict) or item.get("confidence_label") != "verified":
                continue
            observed = self.inspect(project_id, item.get("verified_by") or "", payload)
            if observed["evidence"] == item:
                valid.add(item["verified_by"])
        return valid

    @staticmethod
    def downgrade_changed(before, after):
        if execution_basis(before) == execution_basis(after):
            return
        for item in after.get("evidence", []):
            if item.get("confidence_label") == "verified":
                item["confidence_label"], item["verified_by"] = "inferred", None

    def attach(self, studio, project_id, expected_version, report_id, policy):
        from .evidence import EvidenceRules

        rules = EvidenceRules(studio, policy)
        with self.repository.transaction():
            payload = rules._payload(project_id, expected_version)
            row = self.repository.connection.execute(
                "SELECT report_json FROM studio_verifications WHERE project_id=? AND report_id=?",
                (project_id, report_id),
            ).fetchone()
            if row is None:
                raise StudioError("EVIDENCE_INSUFFICIENT", "找不到試做報告。")
            report = json.loads(row[0])
            additions = []
            for index in range(len(report["cases"])):
                evidence = self.inspect(project_id, f"replay:{report_id}:{index + 1}", payload)[
                    "evidence"
                ]
                existing = next((e for e in payload["evidence"] if e["id"] == evidence["id"]), None)
                if existing and existing != evidence:
                    raise StudioError("REVISION_CONFLICT", "同名規則內容已修改，請先處理命名衝突。")
                if not existing:
                    additions.append(evidence)
            if not additions:
                raise StudioError("EVIDENCE_INSUFFICIENT", "沒有尚未加入的通過案例證據。")
            payload["evidence"].extend(additions)
            return rules._commit(
                project_id,
                expected_version,
                payload,
                "evidence.replay_attached",
                {"report_id": report_id, "evidence_ids": [e["id"] for e in additions]},
            )
