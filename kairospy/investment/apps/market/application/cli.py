"""One-shot Market CLI application facade."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from kairospy.system.apps.components.application.binaries import (
    reject_owned_options,
    resolve_binary,
)
from kairospy.system.apps.workspace.application import Workspace


@dataclass(frozen=True, slots=True)
class MarketCommandLine:
    """Public `kairos market` options separated from owner CLI arguments."""

    workspace: Path | None
    output: str | None
    arguments: tuple[str, ...]


def parse_market_command_line(arguments: Sequence[str]) -> MarketCommandLine:
    """Parse the public `kairos market` adapter options once for every surface."""

    workspace: Path | None = None
    output: str | None = None
    result: list[str] = []
    index = 0
    while index < len(arguments):
        item = arguments[index]
        option, separator, inline_value = item.partition("=")
        if option not in {"--workspace", "--format", "--output"}:
            result.append(item)
            index += 1
            continue
        if separator:
            value = inline_value
            index += 1
        else:
            if index + 1 >= len(arguments):
                raise ValueError(f"{option} requires a value")
            value = arguments[index + 1]
            index += 2
        if not value:
            raise ValueError(f"{option} requires a value")
        if option == "--workspace":
            if workspace is not None:
                raise ValueError("--workspace may be specified only once")
            workspace = Path(value)
        else:
            if output is not None:
                raise ValueError("--format/--output may be specified only once")
            if value not in {"text", "json", "table"}:
                raise ValueError("--format/--output must be text, json, or table")
            output = value
    return MarketCommandLine(workspace, output, tuple(result))


@dataclass(frozen=True, slots=True)
class MarketCliApplication:
    """Invoke the independent Market CLI for direct, one-shot commands."""

    workspace: Workspace | None = None
    binary: str | None = None

    def command(
        self, arguments: Sequence[str], *, output: str | None = "json"
    ) -> list[str]:
        """Build a Rust command; output is explicit adapter configuration."""
        reject_owned_options(arguments, {"--workspace"})
        command = [self.binary or resolve_binary("kairos-market-cli")]
        if self.workspace is not None:
            command.extend(("--workspace", str(self.workspace.paths.root)))
        if output is not None:
            command.extend(("--output", output))
        command.extend(arguments)
        return command

    def shell_command(
        self, arguments: Sequence[str], *, output: str | None = None
    ) -> list[str]:
        """Build the stable public command accepted by Shell and Workbench."""

        parsed = parse_market_command_line(arguments)
        if parsed.workspace is not None:
            raise ValueError("shell command arguments must not repeat --workspace")
        if parsed.output is not None:
            raise ValueError(
                "shell command arguments must not repeat --format/--output"
            )
        command = ["kairos", "market"]
        if self.workspace is not None:
            command.extend(("--workspace", str(self.workspace.paths.root)))
        if output is not None:
            command.extend(("--format", output))
        command.extend(parsed.arguments)
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

    def routes(self, *, market_type: str, observation_kind: str) -> dict[str, Any]:
        return self.run(
            (
                "standalone",
                "routes",
                "--market-type",
                market_type,
                "--observation-kind",
                observation_kind,
            )
        )

    def once(
        self,
        *,
        market_id: str,
        instrument_id: str,
        exchange_id: str,
        market_type: str,
        symbol: str,
        provider: str,
        observation_kind: str,
    ) -> dict[str, Any]:
        return self.run(
            (
                "standalone",
                "once",
                *self._descriptor_arguments(
                    market_id=market_id,
                    instrument_id=instrument_id,
                    exchange_id=exchange_id,
                    market_type=market_type,
                    symbol=symbol,
                ),
                "--provider",
                provider,
                "--observation-kind",
                observation_kind,
            )
        )

    def validate(
        self,
        *,
        market_id: str,
        instrument_id: str,
        exchange_id: str,
        market_type: str,
        symbol: str,
    ) -> dict[str, Any]:
        return self.run(
            (
                "standalone",
                "validate",
                *self._descriptor_arguments(
                    market_id=market_id,
                    instrument_id=instrument_id,
                    exchange_id=exchange_id,
                    market_type=market_type,
                    symbol=symbol,
                ),
            )
        )

    def datasets(self) -> dict[str, Any]:
        return self.run(("standalone", "datasets"))

    def reference_universe(
        self, *, instrument_kind: str, limit: int = 10_000
    ) -> dict[str, Any]:
        if limit <= 0:
            raise ValueError("reference universe limit must be positive")
        return self.run(
            (
                "standalone",
                "reference-universe",
                "--instrument-kind",
                instrument_kind,
                "--limit",
                str(limit),
            )
        )

    def download(
        self,
        *,
        provider: str,
        symbol: str,
        market_type: str,
        data_kind: str,
        instrument_id: str,
        start_unix_millis: int,
        end_unix_millis: int,
        destination: str,
        market_id: str | None = None,
        interval: str | None = None,
    ) -> dict[str, Any]:
        if start_unix_millis >= end_unix_millis:
            raise ValueError("historical start must be before end")
        arguments = [
            "standalone",
            "download",
            "--provider",
            provider,
            "--symbol",
            symbol,
            "--market-type",
            market_type,
            "--data-kind",
            data_kind,
            "--instrument-id",
            instrument_id,
            "--start",
            str(start_unix_millis),
            "--end",
            str(end_unix_millis),
            "--file",
            destination,
        ]
        if market_id is not None:
            arguments.extend(("--market-id", market_id))
        if interval is not None:
            arguments.extend(("--interval", interval))
        return self.run(arguments)

    def replay(
        self,
        *,
        market_id: str,
        instrument_id: str,
        exchange_id: str,
        market_type: str,
        symbol: str,
        files: Sequence[str],
    ) -> dict[str, Any]:
        if not files:
            raise ValueError("at least one replay file is required")
        arguments = [
            "standalone",
            "replay",
            *self._descriptor_arguments(
                market_id=market_id,
                instrument_id=instrument_id,
                exchange_id=exchange_id,
                market_type=market_type,
                symbol=symbol,
            ),
        ]
        for path in files:
            arguments.extend(("--file", path))
        return self.run(arguments)

    def connected_snapshot(
        self,
        *,
        market_id: str,
        provider: str,
        kind: str,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        """Read one workspace-scoped Market view without routing through Typer."""

        if self.workspace is None:
            raise ValueError("connected Market snapshot requires a Workspace")
        arguments = [
            "connected",
            "snapshot",
            "--socket",
            str(self.workspace.paths.process_socket("market")),
            "--view-root",
            str(self.workspace.paths.child("snapshots", "market", "market-shared")),
            kind,
            "--market-id",
            market_id,
            "--provider",
            provider,
        ]
        if timeframe is not None:
            arguments.extend(("--timeframe", timeframe))
        return self.run(arguments)

    def connected_freshness(
        self,
        *,
        market_id: str,
        provider: str,
        observation: str | None = None,
    ) -> dict[str, Any]:
        """Read freshness from the workspace-scoped Market current view."""

        if self.workspace is None:
            raise ValueError("connected Market freshness requires a Workspace")
        arguments = [
            "connected",
            "freshness",
            "--socket",
            str(self.workspace.paths.process_socket("market")),
            "--view-root",
            str(self.workspace.paths.child("snapshots", "market", "market-shared")),
            "--market-id",
            market_id,
            "--provider",
            provider,
        ]
        if observation is not None:
            arguments.extend(("--observation", observation))
        return self.run(arguments)

    @staticmethod
    def _descriptor_arguments(
        *,
        market_id: str,
        instrument_id: str,
        exchange_id: str,
        market_type: str,
        symbol: str,
    ) -> tuple[str, ...]:
        return (
            "--market-id",
            market_id,
            "--instrument-id",
            instrument_id,
            "--exchange-id",
            exchange_id,
            "--market-type",
            market_type,
            "--symbol",
            symbol,
        )


__all__ = [
    "MarketCliApplication",
    "MarketCommandLine",
    "parse_market_command_line",
]
