from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, TypeAlias


RuntimeDomain = Literal["data", "market", "account", "execution", "intent", "clock", "system"]
RuntimePayload: TypeAlias = object | None


@dataclass(frozen=True, slots=True)
class RuntimeEnvelope:
    domain: RuntimeDomain | str
    kind: str
    time: datetime
    sequence: int
    payload: RuntimePayload = None

    def __post_init__(self) -> None:
        if not str(self.domain).strip() or not self.kind.strip():
            raise ValueError("runtime envelope domain and kind are required")
        if self.time.tzinfo is None:
            raise ValueError("runtime envelope time must be timezone-aware")
        if self.sequence < 1:
            raise ValueError("runtime envelope sequence must be positive")

    def changed(self, domain: str, kind: str | None = None) -> bool:
        return str(self.domain) == domain and (kind is None or self.kind == kind)


def system_envelope(
    kind: str,
    *,
    time: datetime,
    sequence: int,
    payload: Mapping[str, object] | None = None,
) -> RuntimeEnvelope:
    return RuntimeEnvelope(
        "system",
        kind,
        time,
        sequence,
        payload=dict(payload or {}),
    )


__all__ = [
    "RuntimeDomain",
    "RuntimeEnvelope",
    "RuntimePayload",
    "system_envelope",
]
