from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Generic, Literal, Mapping, TypeAlias, TypeVar


RuntimeDomain = Literal["data", "market", "account", "execution", "intent", "clock", "system"]
RuntimePayload: TypeAlias = object | None
TPayload_co = TypeVar("TPayload_co", covariant=True)


@dataclass(frozen=True, slots=True)
class RuntimeEnvelope(Generic[TPayload_co]):
    domain: RuntimeDomain | str
    kind: str
    time: datetime
    sequence: int
    payload: TPayload_co | None = None

    def __post_init__(self) -> None:
        if not str(self.domain).strip() or not self.kind.strip():
            raise ValueError("runtime envelope domain and kind are required")
        if self.time.tzinfo is None:
            raise ValueError("runtime envelope time must be timezone-aware")
        if self.sequence < 1:
            raise ValueError("runtime envelope sequence must be positive")

    def changed(self, domain: str, kind: str | None = None) -> bool:
        return str(self.domain) == domain and (kind is None or self.kind == kind)


AnyRuntimeEnvelope: TypeAlias = RuntimeEnvelope[object]


def system_envelope(
    kind: str,
    *,
    time: datetime,
    sequence: int,
    payload: Mapping[str, object] | None = None,
) -> RuntimeEnvelope[Mapping[str, object]]:
        return RuntimeEnvelope(
        "system",
        kind,
        time,
        sequence,
        payload=dict(payload or {}),
    )


@dataclass(frozen=True, slots=True)
class RuntimeIncident:
    kind: str
    message: str
    raw: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


__all__ = [
    "AnyRuntimeEnvelope",
    "RuntimeDomain",
    "RuntimeEnvelope",
    "RuntimePayload",
    "RuntimeIncident",
    "system_envelope",
]
