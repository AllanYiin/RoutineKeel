"""Shared local application boundary; never starts a server or model on import."""

from . import __version__
from .project.service import ProjectService
from .storage import Repository
from .storage.sqlite import SQLiteRepository


class StudioService:
    def __init__(self, repository: Repository):
        self.repository = repository

    @classmethod
    def open_workspace(cls, workspace):
        from pathlib import Path

        return cls(SQLiteRepository(Path(workspace) / "share_states" / "workflow_studio"))

    @property
    def projects(self) -> ProjectService:
        if not isinstance(self.repository, SQLiteRepository):
            raise TypeError("Project operations require persistent Studio storage")
        return ProjectService(self.repository)

    @property
    def sources(self):
        from .ingestion.service import SourceService

        if not isinstance(self.repository, SQLiteRepository):
            raise TypeError("Source operations require persistent Studio storage")
        return SourceService(self.repository)

    @property
    def history(self):
        from .project.history import RevisionHistory

        return RevisionHistory(self)

    @property
    def tools(self):
        from .registry.catalog import Catalog

        return Catalog(self.repository)

    @property
    def handlers(self):
        from .registry.handlers import HandlerLibrary

        return HandlerLibrary(self.repository)

    def discovery(self, provider, budget, tool_policy):
        from .discovery.planner import DiscoveryPlanner

        return DiscoveryPlanner(self, provider, budget, tool_policy)

    def compile(self, project_id, revision_id, tool_policy):
        from .compiler.generator import Compiler

        return Compiler(self).compile(project_id, revision_id, tool_policy)

    def verify(
        self, project_id, build_id, cases, tool_policy, *, runtime_python=None, authorization=None
    ):
        from .verifier.report import Verifier

        return Verifier(self, runtime_python).verify(
            project_id, build_id, cases, tool_policy, authorization=authorization
        )

    def repair(self, project_id, build_id, cases, tool_policy, proposer, *, runtime_python=None):
        from .verifier.repair import RepairLoop
        from .verifier.report import Verifier

        return RepairLoop(self, Verifier(self, runtime_python)).run(
            project_id, build_id, cases, tool_policy, proposer
        )

    def save_ir(self, project_id, expected_version, payload, policy):
        return self.projects.save_ir(
            project_id,
            expected_version,
            payload,
            {key: tool.signature() for key, tool in self.tools.allowed(policy).items()},
        )

    def model_repair(self, provider, budget, tool_policy, *, runtime_python=None):
        from .verifier.model_repair import ModelRepair

        return ModelRepair(self, provider, budget, tool_policy, runtime_python=runtime_python)

    def editor(self, tool_policy):
        from .ir.editor import FlowEditor

        return FlowEditor(self, tool_policy)

    def evidence(self, tool_policy):
        from .governance.evidence import EvidenceRules

        return EvidenceRules(self, tool_policy)

    def reviews(self, tool_policy):
        from .governance.reviews import BuildReviews

        return BuildReviews(self, tool_policy)

    def confirmed_evidence(self, project_id, payload):
        from .governance.evidence import EvidenceRecords
        from .governance.machine_evidence import MachineEvidence

        return EvidenceRecords(self.repository).confirmed_ids(
            project_id, payload.get("evidence", [])
        ) | MachineEvidence(self.repository).valid_ids(project_id, payload)

    def status(self) -> dict:
        return {"version": __version__, "schema_version": self.repository.schema_version}

    def close(self) -> None:
        self.repository.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
