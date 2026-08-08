"""One-shot Account CLI application facade."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..system.binaries import reject_owned_options, resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class AccountCliApplication:
    """Invoke the Account module's independent CLI."""

    workspace: Workspace
    binaries: Mapping[str, str] = field(default_factory=dict)

    def command(self, arguments: Sequence[str], *, output: str | None = "json") -> list[str]:
        """Build a Rust command; only this adapter owns workspace binding."""
        reject_owned_options(arguments, {"--workspace"})
        command = [
            self.binaries.get("account") or resolve_binary("kairos-account-cli"),
            "--workspace", str(self.workspace.paths.root),
        ]
        if output is not None:
            command.extend(("--output", output))
        command.extend(arguments)
        return command

    def invoke(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Forward canonical Rust argv without selecting an output format."""
        return subprocess.run(
            self.command(arguments, output=None),
            cwd=str(self.workspace.paths.root),
            capture_output=True,
            text=True,
            check=False,
        )

    def run(self, arguments: Sequence[str]) -> Any:
        """Run an internal JSON call with the adapter's machine output."""
        reject_owned_options(arguments, {"--output", "--format"})
        result = subprocess.run(
            self.command(arguments),
            cwd=str(self.workspace.paths.root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "account CLI failed")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("account CLI returned invalid JSON") from error
        return value


__all__ = ["AccountCliApplication"]
