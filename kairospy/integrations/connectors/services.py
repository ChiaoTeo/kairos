from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .artifacts import ProviderEstimate, SourceArtifact
from .resources import ProviderResource, ProviderResourceSpec


class ProviderService(Protocol):
    service_id: str
    service_kind: str

    def resources(self) -> Mapping[str, ProviderResource]:
        ...


class HistoricalMarketDataService(ProviderService, Protocol):
    def estimate(self, request: object) -> ProviderEstimate:
        ...

    def fetch(self, request: object) -> SourceArtifact:
        ...


@dataclass(frozen=True, slots=True)
class ProviderServiceSpec:
    service_id: str
    service_kind: str
    resources: tuple[ProviderResourceSpec, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.service_id.strip() or not self.service_kind.strip():
            raise ValueError("provider service spec requires service_id and service_kind")
