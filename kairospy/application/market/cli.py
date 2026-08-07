"""One-shot and remote Market CLI application facade."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from ..system.binaries import resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class MarketCliApplication:
    """Invoke Market CLI commands and, for remote commands, its server."""

    workspace: Workspace | None = None
    binary: str | None = None

    def run(self, arguments: list[str], *, remote: bool = False) -> dict[str, Any]:
        command = [self.binary or resolve_binary("kairos-market-cli"), "--output", "json"]
        if remote:
            if self.workspace is None:
                raise ValueError("a workspace is required for a remote market command")
            from ..system import ComponentProcessApplication

            ComponentProcessApplication(self.workspace).ensure_running("market")
            command.extend(("--workspace", str(self.workspace.paths.root)))
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
