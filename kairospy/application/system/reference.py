"""Composition of the Rust Reference process for the Python Supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path

from .supervisor import ProcessSpec
from ..workspace import Workspace

@dataclass(frozen=True, slots=True)
class ReferenceProcessConfig:
    """Business-level configuration for one Reference process instance."""

    workspace: Workspace
    provider: str = "binance-spot"
    endpoint: str | None = None
    binary: str = "kairos-reference-server"
    channel: str = "aeron:udp?endpoint=localhost:40123"
    aeron_dir: Path | None = None
    refresh_seconds: int = 300
    catalog_stream: int = 1201
    markets_stream: int = 1202
    lifecycle_stream: int = 1203
    api_key: str | None = None
    secret: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    once: bool = False
    stop_timeout: float = 15.0

    def __post_init__(self) -> None:
        if self.endpoint is not None and not self.endpoint.strip():
            raise ValueError("reference endpoint is required")
        if not self.provider.strip():
            raise ValueError("reference provider is required")
        if self.refresh_seconds <= 0:
            raise ValueError("refresh_seconds must be positive")
        if min(self.catalog_stream, self.markets_stream, self.lifecycle_stream) < 0:
            raise ValueError("reference stream ids must be non-negative")

    def process_spec(self) -> ProcessSpec:
        socket_path = self.workspace.paths.reference_socket()
        health_file = self.workspace.paths.reference_health()
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.binary,
            "--workspace",
            str(self.workspace.paths.root),
            "--provider",
            self.provider,
            "--channel",
            self.channel,
            "--refresh-seconds",
            str(self.refresh_seconds),
            "--catalog-stream",
            str(self.catalog_stream),
            "--markets-stream",
            str(self.markets_stream),
            "--lifecycle-stream",
            str(self.lifecycle_stream),
        ]
        if self.endpoint is not None:
            command.extend(("--endpoint", self.endpoint))
        if self.aeron_dir is not None:
            command.extend(("--aeron-dir", str(self.aeron_dir)))
        command.extend(("--socket", str(socket_path), "--health-file", str(health_file)))
        environment = dict(self.environment)
        if self.api_key is not None:
            if self.provider == "massive-options":
                environment["MASSIVE_API_KEY"] = self.api_key
            elif self.provider == "binance-equity":
                environment["BINANCE_API_KEY"] = self.api_key
        if self.secret is not None:
            environment["BINANCE_API_SECRET"] = self.secret
        if self.once:
            command.append("--once")
        return ProcessSpec(
            name="reference",
            command=tuple(command),
            environment=environment,
            cwd=self.workspace.paths.root,
            health_file=health_file,
            control_socket=socket_path,
            stop_timeout=self.stop_timeout,
        )
