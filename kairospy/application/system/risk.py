"""Composition of the Rust Risk process for the Python Supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path

from .supervisor import ProcessSpec
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class RiskProcessConfig:
    """Process-level configuration; risk state remains owned by Rust Risk."""

    workspace: Workspace
    binary: str = "kairos-risk"
    interval_ms: int = 1_000
    environment: Mapping[str, str] = field(default_factory=dict)
    stop_timeout: float = 15.0

    def __post_init__(self) -> None:
        if not self.binary.strip():
            raise ValueError("risk binary is required")
        if self.interval_ms <= 0:
            raise ValueError("risk interval_ms must be positive")
        if self.stop_timeout <= 0:
            raise ValueError("risk stop_timeout must be positive")

    def process_spec(self) -> ProcessSpec:
        socket_path = self.workspace.paths.risk_socket()
        health_file = self.workspace.paths.risk_health()
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        command = [self.binary, "--workspace", str(self.workspace.paths.root)]
        command.extend(("--interval-ms", str(self.interval_ms)))
        return ProcessSpec(
            name="risk",
            command=tuple(command),
            cwd=self.workspace.paths.root,
            environment=dict(self.environment),
            health_file=health_file,
            control_socket=socket_path,
            stop_timeout=self.stop_timeout,
        )
