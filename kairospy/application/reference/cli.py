"""One-shot Reference CLI application facade."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..system.binaries import resolve_binary
from ..workspace import Workspace


def _default_endpoint(provider: str) -> str:
    return {
        "hyperliquid": "https://api.hyperliquid.xyz/info",
        "massive-options": "https://api.polygon.io",
        "massive-equity": "https://api.polygon.io",
    }.get(provider, "https://api.binance.com/api/v3/exchangeInfo")


@dataclass(frozen=True, slots=True)
class ReferenceCliApplication:
    """Invoke the Reference module's one-shot CLI.

    Surface code supplies already-normalized module command arguments. Binary
    resolution, workspace binding, provider defaults, and result decoding stay
    inside the Reference application boundary.
    """

    workspace: Workspace
    binary: str | None = None

    def run(self, arguments: list[str]) -> Any:
        provider = os.environ.get("KAIROS_REFERENCE_PROVIDER", "binance-spot")
        command = [
            self.binary or resolve_binary("kairos-reference-cli"),
            "--workspace", str(self.workspace.paths.root),
            "--provider", provider,
            "--endpoint", os.environ.get("KAIROS_REFERENCE_ENDPOINT", _default_endpoint(provider)),
            *arguments,
        ]
        result = subprocess.run(
            command,
            cwd=str(self.workspace.paths.root),
            env=os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(message or f"reference CLI failed: {result.returncode}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("reference CLI returned invalid JSON") from error


__all__ = ["ReferenceCliApplication"]
