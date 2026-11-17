import json
from uuid import uuid4

from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import now


class GoldenCases:
    def __init__(self, studio):
        self.studio = studio

    def save(self, project_id, expected_version, specification, *, case_id=None):
        self._validate(project_id, specification)
        with self.studio.repository.transaction() as db:
            self.studio.projects._editable(db, project_id, expected_version)
            version = self.get(project_id, case_id)["version"] + 1 if case_id else 1
            case_id = case_id or "case_" + uuid4().hex
            db.execute(
                "INSERT INTO studio_test_cases VALUES (?,?,?,?,?,?)",
                (case_id, version, project_id, canonical_json(specification), "active", now()),
            )
            db.execute(
                "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
                (now(), project_id),
            )
        return self.get(project_id, case_id)

    def from_candidate(self, project_id, expected_version, candidate):
        from ..discovery.evidence_graph import EvidenceGraphs

        graph = EvidenceGraphs(self.studio.repository).get(
            project_id, candidate["graph_id"], candidate["graph_version"]
        )["graph"]
        files = {r["path"]: r for r in graph["files"]}

        def ref(path):
            if path not in files:
                raise StudioError("INPUT_INVALID", "案例檔案不存在於證據圖。")
            return {
                "source_id": graph["source_id"],
                "version": graph["source_version"],
                "path": path,
                "sha256": files[path]["sha256"],
            }

        return self.save(
            project_id,
            expected_version,
            {
                "name": candidate["input_path"] + " → " + candidate["expected_path"],
                "input": ref(candidate["input_path"]),
                "expected": ref(candidate["expected_path"]),
                "comparison": "exact_bytes",
            },
        )

    def _validate(self, project_id, specification):
        if (
            set(specification) != {"name", "input", "expected", "comparison"}
            or specification["comparison"] != "exact_bytes"
        ):
            raise StudioError(
                "INPUT_INVALID", "Golden Case 必須有名稱、輸入、預期成果與 exact_bytes 比對規則。"
            )
        if not isinstance(specification["name"], str) or not specification["name"].strip():
            raise StudioError("INPUT_INVALID", "測試案例名稱不可空白。")
        for side in ("input", "expected"):
            ref = specification[side]
            if set(ref) != {"source_id", "version", "path", "sha256"}:
                raise StudioError("INPUT_INVALID", "案例須綁定來源版本、檔案與指紋。")
            source = self.studio.sources.get(project_id, ref["source_id"], version=ref["version"])
            record = next((r for r in source.manifest["files"] if r["path"] == ref["path"]), None)
            if record is None or record["sha256"] != ref["sha256"]:
                raise StudioError("SOURCE_CORRUPT", "測試案例來源指紋不符。")
            self.studio.sources.content(
                project_id, ref["source_id"], ref["path"], version=ref["version"]
            )

    def get(self, project_id, case_id, version=None):
        row = self.studio.repository.connection.execute(
            "SELECT * FROM studio_test_cases WHERE project_id=? AND case_id=? AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1",
            (project_id, case_id, version, version),
        ).fetchone()
        if row is None:
            raise StudioError("TEST_CASE_NOT_FOUND", "找不到測試案例版本。")
        result = dict(row)
        result["specification"] = json.loads(result.pop("spec_json"))
        return result

    def list(self, project_id, include_archived=False):
        rows = self.studio.repository.connection.execute(
            "SELECT DISTINCT case_id FROM studio_test_cases WHERE project_id=?", (project_id,)
        ).fetchall()
        results = [self.get(project_id, r[0]) for r in rows]
        return [r for r in results if include_archived or r["status"] == "active"]

    def archive(self, project_id, case_id, expected_version):
        with self.studio.repository.transaction() as db:
            self.studio.projects._editable(db, project_id, expected_version)
            current = self.get(project_id, case_id)
            db.execute(
                "INSERT INTO studio_test_cases VALUES (?,?,?,?,?,?)",
                (
                    case_id,
                    current["version"] + 1,
                    project_id,
                    canonical_json(current["specification"]),
                    "archived",
                    now(),
                ),
            )
            db.execute(
                "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
                (now(), project_id),
            )
        return self.get(project_id, case_id)

    def bytes(self, project_id, case_id):
        case = self.get(project_id, case_id)
        if case["status"] != "active":
            raise StudioError("TEST_CASE_NOT_FOUND", "測試案例已封存。")
        self._validate(project_id, case["specification"])
        refs = [case["specification"][side] for side in ("input", "expected")]
        return tuple(
            self.studio.sources.content(project_id, r["source_id"], r["path"], version=r["version"])
            for r in refs
        )
