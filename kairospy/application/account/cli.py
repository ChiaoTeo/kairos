"""One-shot Account CLI application facade."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..system.binaries import resolve_binary
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class AccountCliApplication:
    """Invoke the Account module's independent CLI."""

    workspace: Workspace
    binaries: Mapping[str, str] = field(default_factory=dict)

    def run(self, arguments: list[str]) -> dict[str, Any]:
        global_flags = {
            "--provider", "--product", "--credential-id", "--api-key",
            "--secret", "--passphrase", "--account-id", "--confirm-live",
        }
        prefix: list[str] = []
        command_arguments: list[str] = []
        index = 0
        while index < len(arguments):
            item = arguments[index]
            if item in global_flags:
                prefix.append(item)
                if item != "--confirm-live":
                    index += 1
                    if index >= len(arguments):
                        raise ValueError(f"missing value for {item}")
                    prefix.append(arguments[index])
            else:
                command_arguments.append(item)
            index += 1
        command = [
            self.binaries.get("account") or resolve_binary("kairos-account-cli"),
            "--workspace", str(self.workspace.paths.root),
            "--output", "json",
            *prefix,
            *command_arguments,
        ]
        result = subprocess.run(
            command,
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
        if not isinstance(value, dict):
            raise ValueError("account CLI must return a JSON object")
        return value


__all__ = ["AccountCliApplication"]
