"""Invocation adapter for the canonical Rust Reference CLI.

The Python layer owns binary resolution and workspace binding only. Reference
command syntax and business options belong to ``kairos-reference-cli``.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..system.binaries import reject_owned_options, resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class ReferenceCliApplication:
    """Invoke the canonical one-shot Reference CLI.

    ``arguments`` are Rust CLI arguments and are never augmented with business
    flags such as provider or endpoint. The workspace is the only option owned
    by this adapter and is added exactly once.
    """

    workspace: Workspace
    binary: str | None = None

    def command(self, arguments: Sequence[str]) -> list[str]:
        reject_owned_options(arguments, {"--workspace"})
        return [
            self.binary or resolve_binary("kairos-reference-cli"),
            "--workspace",
            str(self.workspace.paths.root),
            *arguments,
        ]

    def invoke(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(arguments),
            cwd=str(self.workspace.paths.root),
            env=os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
        )

    def run(self, arguments: Sequence[str]) -> Any:
        """Run a JSON-producing Reference command and decode its result."""
        result = self.invoke(arguments)
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(message or f"reference CLI failed: {result.returncode}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("reference CLI returned invalid JSON") from error


__all__ = ["ReferenceCliApplication"]
