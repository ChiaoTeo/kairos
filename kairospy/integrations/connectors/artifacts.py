from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ProviderEstimate:
    requests: int
    bytes: int | None = None
    cost_class: str = "unknown"
    instruments: int | None = None

    def __post_init__(self) -> None:
        if (
            self.requests < 0
            or self.bytes is not None and self.bytes < 0
            or self.instruments is not None and self.instruments < 0
        ):
            raise ValueError("provider estimates cannot be negative")


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    provider: str
    service: str
    resource: str
    request_fingerprint: str
    receipt_path: str | Path | None = None
    files: tuple[str | Path, ...] = ()
    coverage_hint: Mapping[str, object] = field(default_factory=dict)
    schema_hint: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "service", "resource", "request_fingerprint"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"source artifact {name} cannot be empty")


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    provider: str
    service: str
    resource: str
    received_at: datetime
    payload: Mapping[str, object]
    venue: str | None = None
    sequence: str | int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    status: str
    checked_at: datetime | None = None
    services: Mapping[str, object] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

