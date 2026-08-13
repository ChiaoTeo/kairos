"""Composition of the Rust Reference process for the Python Supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .supervisor import ProcessSpec
from ..workspace import Workspace

REFERENCE_CHANGES_STREAM = 1201


@dataclass(frozen=True, slots=True)
class ReferenceProcessConfig:
    """Business-level configuration for one Reference process instance."""

    workspace: Workspace
    binary: str = "kairos-reference-server"
    aeron_channel: str = "aeron:udp?endpoint=localhost:40123"
    aeron_dir: Path | None = None
    refresh_interval: str = "5m"
    reference_changes_stream: int = REFERENCE_CHANGES_STREAM
    run_mode: str = "daemon"
    stop_timeout: float = 15.0

    def __post_init__(self) -> None:
        if self.run_mode not in {"daemon", "once"}:
            raise ValueError("run_mode must be daemon or once")
        if not self.refresh_interval.strip():
            raise ValueError("refresh_interval is required")
        if self.reference_changes_stream <= 0:
            raise ValueError("reference stream ids must be positive")
        if self.reference_changes_stream != REFERENCE_CHANGES_STREAM:
            raise ValueError(
                f"reference_changes_stream must be {REFERENCE_CHANGES_STREAM}"
            )
        if not self.aeron_channel.strip():
            raise ValueError("reference Aeron channel is required")

    def process_spec(self) -> ProcessSpec:
        socket_path = self.workspace.paths.reference_socket()
        health_file = self.workspace.paths.reference_health()
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.binary,
            "--workspace",
            str(self.workspace.paths.root),
            "--aeron-channel",
            self.aeron_channel,
            "--refresh-interval",
            self.refresh_interval,
            "--reference-changes-stream",
            str(self.reference_changes_stream),
        ]
        if self.aeron_dir is not None:
            command.extend(("--aeron-dir", str(self.aeron_dir)))
        command.extend(
            ("--socket", str(socket_path), "--health-file", str(health_file))
        )
        command.extend(("--run-mode", self.run_mode))
        return ProcessSpec(
            name="reference",
            command=tuple(command),
            environment={},
            cwd=self.workspace.paths.root,
            health_file=health_file,
            control_socket=socket_path,
            stop_timeout=self.stop_timeout,
        )
