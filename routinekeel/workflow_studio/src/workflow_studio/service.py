"""Shared local application boundary; never starts a server or model on import."""

from . import __version__
from .storage import Repository


class StudioService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def status(self) -> dict:
        return {"version": __version__, "schema_version": self.repository.schema_version}

    def close(self) -> None:
        self.repository.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
