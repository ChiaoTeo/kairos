from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class TransportRequest:
    resource_id: str
    operation: str
    payload: Mapping[str, object] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_id.strip() or not self.operation.strip():
            raise ValueError("transport request requires resource_id and operation")


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: str
    payload: object
    headers: Mapping[str, str] = field(default_factory=dict)
    receipt: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.status.strip():
            raise ValueError("transport response requires status")


class ProviderTransport(Protocol):
    transport_id: str

    def send(self, request: TransportRequest) -> TransportResponse:
        ...
