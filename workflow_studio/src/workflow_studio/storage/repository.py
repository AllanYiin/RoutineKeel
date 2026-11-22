"""Minimal lifecycle protocol; persistent operations are added with their contracts."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Repository(Protocol):
    @property
    def schema_version(self) -> int: ...

    def close(self) -> None: ...


class MemoryRepository:
    """Stage 0 test storage; no application data or persistence is claimed."""

    schema_version = 0

    def close(self) -> None:
        pass
