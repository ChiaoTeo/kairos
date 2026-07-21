from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


class ProviderResource(Protocol):
    resource_id: str


@dataclass(frozen=True, slots=True)
class ProviderResourceSpec:
    resource_id: str
    service_id: str
    path: str | None = None
    method: str | None = None
    parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_id.strip() or not self.service_id.strip():
            raise ValueError("provider resource spec requires resource_id and service_id")
