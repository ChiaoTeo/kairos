from __future__ import annotations

from typing import Protocol

from .artifacts import ProviderEvent
from .transports import TransportRequest, TransportResponse


class ProviderCodec(Protocol):
    codec_id: str

    def encode(self, request: object) -> TransportRequest:
        ...

    def decode(self, response: TransportResponse) -> ProviderEvent | object:
        ...
