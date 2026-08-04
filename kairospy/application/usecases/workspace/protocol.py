"""Ports consumed by workspace application capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class WorkspaceLocator(Protocol):
    def workspace(self, start: str | Path | None = None) -> object: ...


__all__ = ["WorkspaceLocator"]
