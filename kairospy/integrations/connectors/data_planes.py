from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DataPlaneEndpoint:
    protocol: str
    address: str | None = None
    format: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.protocol.strip():
            raise ValueError("data plane endpoint protocol cannot be empty")
        if self.format is not None and not self.format.strip():
            raise ValueError("data plane endpoint format cannot be empty")


@dataclass(frozen=True, slots=True)
class ProviderDataPlaneSpec:
    plane_id: str
    service_id: str
    endpoint: DataPlaneEndpoint
    features: tuple[str, ...] = ()
    side_effecting: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.plane_id.strip() or not self.service_id.strip():
            raise ValueError("provider data plane spec requires plane_id and service_id")
        for feature in self.features:
            if not feature.strip():
                raise ValueError("provider data plane feature cannot be empty")


@runtime_checkable
class ProviderDataPlane(Protocol):
    plane_id: str
    service_id: str

    def describe(self) -> ProviderDataPlaneSpec:
        ...
