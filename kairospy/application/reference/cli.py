"""One-shot Reference CLI application facade."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Any, Sequence

from ..system.binaries import reject_owned_options, resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class ReferenceCliApplication:
    """Invoke Reference-owned one-shot use cases with machine output."""

    workspace: Workspace
    binary: str | None = None

    def command(self, arguments: Sequence[str]) -> list[str]:
        reject_owned_options(arguments, {"--workspace", "--output", "--format"})
        return [
            self.binary or resolve_binary("kairos-reference-cli"),
            "--workspace",
            str(self.workspace.paths.root),
            "--output",
            "json",
            *arguments,
        ]

    def invoke(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Forward canonical Rust argv without selecting a new output format."""
        reject_owned_options(arguments, {"--workspace"})
        return subprocess.run(
            [
                self.binary or resolve_binary("kairos-reference-cli"),
                "--workspace",
                str(self.workspace.paths.root),
                *arguments,
            ],
            cwd=str(self.workspace.paths.root),
            capture_output=True,
            text=True,
            check=False,
        )

    def run(self, arguments: Sequence[str]) -> dict[str, Any]:
        result = subprocess.run(
            self.command(arguments),
            cwd=str(self.workspace.paths.root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "reference CLI failed")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("reference CLI returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("reference CLI must return a JSON object")
        return value


__all__ = ["ReferenceCliApplication"]
