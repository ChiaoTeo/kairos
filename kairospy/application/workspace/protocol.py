from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .domain import Workspace


class WorkspaceReader(Protocol):
    def open(self, root: str | Path) -> Workspace: ...
