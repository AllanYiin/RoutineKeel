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
    def tools(self):
        from .registry.catalog import Catalog

        return Catalog(self.repository)

    def discovery(self, provider, budget, tool_policy):
        from .discovery.planner import DiscoveryPlanner

        return DiscoveryPlanner(self, provider, budget, tool_policy)

    def save_ir(self, project_id, expected_version, payload, policy):
        return self.projects.save_ir(
            project_id,
            expected_version,
            payload,
            {key: tool.signature() for key, tool in self.tools.allowed(policy).items()},
        )

    def status(self) -> dict:
        return {"version": __version__, "schema_version": self.repository.schema_version}

    def close(self) -> None:
        self.repository.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
