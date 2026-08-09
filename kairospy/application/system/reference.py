"""Composition of the Rust Reference process for the Python Supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path

from .supervisor import ProcessSpec
from ..workspace import Workspace

REFERENCE_CHANGES_STREAM = 1201

@dataclass(frozen=True, slots=True)
class ReferenceProcessConfig:
    """Business-level configuration for one Reference process instance."""

    workspace: Workspace
    provider: str = "default"
    endpoint: str | None = None
    credential_id: str | None = None
    binary: str = "kairos-reference-server"
    aeron_channel: str = "aeron:udp?endpoint=localhost:40123"
    aeron_dir: Path | None = None
    refresh_interval: str = "5m"
    reference_changes_stream: int = REFERENCE_CHANGES_STREAM
    snapshot_slot_size_mib: int = 64
    api_key: str | None = None
    secret: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    run_mode: str = "daemon"
    stop_timeout: float = 15.0

    def __post_init__(self) -> None:
        if self.endpoint is not None and not self.endpoint.strip():
            raise ValueError("reference endpoint is required")
        if not self.provider.strip():
            raise ValueError("reference provider is required")
        if self.provider not in {
            "default", "binance-spot", "binance-options", "binance-usdm-futures",
            "binance-coinm-futures", "binance-equity", "okx-spot", "okx-equity",
            "okx-swap", "okx-futures", "okx-options", "massive", "massive-equity",
            "massive-options", "hyperliquid",
        }:
            raise ValueError(f"unsupported reference provider: {self.provider}")
        if self.run_mode not in {"daemon", "once"}:
            raise ValueError("run_mode must be daemon or once")
        if self.credential_id is not None and not self.credential_id.strip():
            raise ValueError("reference credential_id must not be empty")
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
        if not 1 <= self.snapshot_slot_size_mib <= 4096:
            raise ValueError("snapshot_slot_size_mib must be between 1 and 4096")

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
            "--aeron-channel",
            self.aeron_channel,
            "--refresh-interval",
            self.refresh_interval,
            "--reference-changes-stream",
            str(self.reference_changes_stream),
            "--snapshot-slot-size-mib",
            str(self.snapshot_slot_size_mib),
        ]
        if self.endpoint is not None:
            command.extend(("--endpoint", self.endpoint))
        if self.credential_id is not None:
            command.extend(("--credential-id", self.credential_id))
        if self.aeron_dir is not None:
            command.extend(("--aeron-dir", str(self.aeron_dir)))
        command.extend(("--socket", str(socket_path), "--health-file", str(health_file)))
        environment = dict(self.environment)
        if self.api_key is not None:
            if self.provider == "binance-equity":
                environment["BINANCE_API_KEY"] = self.api_key
        if self.secret is not None:
            environment["BINANCE_API_SECRET"] = self.secret
        command.extend(("--run-mode", self.run_mode))
        return ProcessSpec(
            name="reference",
            command=tuple(command),
            environment=environment,
            cwd=self.workspace.paths.root,
            health_file=health_file,
            control_socket=socket_path,
            stop_timeout=self.stop_timeout,
        )
