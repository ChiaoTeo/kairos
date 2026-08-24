"""Thin adapter for provider operations owned by kairos-integration."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from kairospy.system.apps.components.application.binaries import (
    reject_owned_options,
    resolve_binary,
)


@dataclass(frozen=True, slots=True)
class IntegrationCliApplication:
    """Invoke the independent Integration operations CLI."""

    binaries: Mapping[str, str] = field(default_factory=dict)

    def command(
        self, arguments: Sequence[str], *, output: str | None = None
    ) -> list[str]:
        reject_owned_options(arguments, {"--output", "--format"})
        command = [
            self.binaries.get("integration") or resolve_binary("kairos-integration-cli")
        ]
        if output is not None:
            command.extend(("--output", output))
        command.extend(arguments)
        return command

    def invoke(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(arguments), capture_output=True, text=True, check=False
        )

    def run(self, arguments: Sequence[str]) -> Any:
        result = subprocess.run(
            self.command(arguments, output="json"),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "integration CLI failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("integration CLI returned invalid JSON") from error


__all__ = ["IntegrationCliApplication"]
