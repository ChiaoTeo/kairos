from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ProjectionReader(Protocol):
    """Read-only storage capability consumed by Biz projection queries."""

    @property
    def root(self) -> Path: ...

    def exists(self, name: str) -> bool: ...

    def read_json(self, name: str) -> dict[str, object]: ...

    def read_jsonl(self, name: str) -> list[dict[str, object]]: ...


__all__ = ["ProjectionReader"]
