"""Repair proposals only change IR; acceptance fixtures are frozen independently."""

import copy

from ..compiler.generator import Compiler
from ..contracts import StudioError


class RepairLoop:
    def __init__(self, studio, verifier):
        self.studio, self.verifier = studio, verifier

    def run(self, project_id, build_id, cases, policy, proposer, *, max_rounds=2):
        if type(max_rounds) is not int or not 0 <= max_rounds <= 2:
            raise StudioError("INPUT_INVALID", "自動修復最多兩輪。")
        frozen_cases = copy.deepcopy(cases)
        reports = []
        build = Compiler(self.studio).get(project_id, build_id)
        original = self.studio.projects.get_revision(project_id, build["revision_id"]).ir
        for attempt in range(max_rounds + 1):
            report = self.verifier.verify(project_id, build["build_id"], frozen_cases, policy)
            reports.append(report)
            if report["status"] == "passed" or attempt == max_rounds:
                return {
                    "status": report["status"],
                    "build_id": build["build_id"],
                    "reports": reports,
                    "repair_rounds": attempt,
                }
            project = self.studio.projects.get(project_id)
            proposal = proposer(copy.deepcopy(report), copy.deepcopy(original))
            if (
                proposal.get("goal") != original["goal"]
                or proposal.get("workflow_id") != original["workflow_id"]
            ):
                raise StudioError("TEST_FAILED", "修復不能更改工作目標或成功條件。")
            accepted = [
                r.ir["revision"]
                for r in self.studio.projects.revisions(project_id, include_discarded=True)
                if r.ir.get("ir_version") == "1.0" and r.status != "draft"
            ]
            proposal["revision"] = max(accepted, default=0) + 1
            revision = self.studio.save_ir(project_id, project.version, proposal, policy)
            build = self.studio.compile(project_id, revision.revision_id, policy)
