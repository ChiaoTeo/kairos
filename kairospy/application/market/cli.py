"""One-shot Market CLI application facade."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Sequence

from ..system.binaries import reject_owned_options, resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class MarketCliApplication:
    """Invoke the independent Market CLI for direct, one-shot commands."""

    workspace: Workspace | None = None
    binary: str | None = None

    def command(self, arguments: Sequence[str], *, output: str | None = "json") -> list[str]:
        """Build a Rust command; output is explicit adapter configuration."""
        command = [self.binary or resolve_binary("kairos-market-cli")]
        if output is not None:
            command.extend(("--output", output))
        command.extend(arguments)
        return command

    def invoke(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Forward canonical Rust argv without changing its output mode."""
        return subprocess.run(
            self.command(arguments, output=None),
            cwd=str(self.workspace.paths.root) if self.workspace is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )

    def run(self, arguments: Sequence[str]) -> dict[str, Any]:
        """Run an internal JSON call with the adapter's machine output."""
        reject_owned_options(arguments, {"--output", "--format"})
        result = subprocess.run(
            self.command(arguments),
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
