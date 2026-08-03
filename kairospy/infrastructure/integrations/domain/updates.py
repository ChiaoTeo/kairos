from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    """Typed technical event envelope; vendor payload stays inside services."""

    connection_id: str
    observed_at: datetime
    kind: str

    def __post_init__(self) -> None:
        if not self.connection_id.strip() or not self.kind.strip():
            raise ValueError("integration event identity is required")
        if self.observed_at.tzinfo is None:
            raise ValueError("integration event timestamp must be timezone-aware")


__all__ = ["IntegrationEvent"]
