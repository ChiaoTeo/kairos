"""Workspace-owned operation journal.

The journal stores infrastructure actions as append-only JSON lines.  It has
no knowledge of account, execution, market or risk business state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .domain import Workspace


@dataclass(frozen=True, slots=True)
class OperationJournal:
    workspace: Workspace

    def append(self, operation: str, *, subject: str | None = None, **details: Any) -> dict[str, Any]:
        if not operation.strip():
            raise ValueError("operation is required")
        value: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
        }
        if subject is not None:
            value["subject"] = subject
        if details:
            value["details"] = details
        path = self.workspace.paths.operations_journal()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
        return value

    def list(self) -> list[dict[str, Any]]:
        path = self.workspace.paths.operations_journal()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


__all__ = ["OperationJournal"]
