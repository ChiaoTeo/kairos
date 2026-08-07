"""One-shot Market CLI application facade."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from ..system.binaries import resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class MarketCliApplication:
    """Invoke the independent Market CLI for direct, one-shot commands."""

    workspace: Workspace | None = None
    binary: str | None = None

    def run(self, arguments: list[str]) -> dict[str, Any]:
        command = [self.binary or resolve_binary("kairos-market-cli"), "--output", "json"]
        command.extend(arguments)
        result = subprocess.run(
            command,
            cwd=str(self.workspace.paths.root) if self.workspace is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "market CLI failed")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("market CLI returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("market CLI must return a JSON object")
        return value


__all__ = ["MarketCliApplication"]
