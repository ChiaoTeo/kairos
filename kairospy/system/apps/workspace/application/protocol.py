from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kairospy.system.domain.workspace import Workspace


class WorkspaceReader(Protocol):
    def open(self, root: str | Path) -> Workspace: ...
