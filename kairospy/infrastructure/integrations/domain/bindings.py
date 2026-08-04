from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kairospy.domain.reference import ParticipantKind, ParticipantRef
from .products import ProductFamily


class AccessScope(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class TransportKind(StrEnum):
    REST = "rest"
    MARKET_STREAM = "websocket_market_stream"
    USER_STREAM = "websocket_user_stream"
    REQUEST_API = "websocket_request_api"


@dataclass(frozen=True, slots=True)
class IntegrationBinding:
    participant: ParticipantRef
    product: ProductFamily | None
    access: AccessScope
    transport: TransportKind

    def __post_init__(self) -> None:
        if self.access is AccessScope.PUBLIC and self.participant.kind not in {
            ParticipantKind.EXCHANGE,
            ParticipantKind.PROVIDER,
        }:
            raise ValueError("public market access requires an exchange or provider")
        if self.access is AccessScope.PRIVATE and self.participant.kind not in {
            ParticipantKind.BROKER,
            ParticipantKind.EXCHANGE,
        }:
            raise ValueError("private access requires a broker or exchange")
        if self.access is AccessScope.PUBLIC and self.transport not in {
            TransportKind.REST,
            TransportKind.MARKET_STREAM,
        }:
            raise ValueError("public access only supports REST or market stream transport")
        if self.access is AccessScope.PRIVATE and self.transport not in {
            TransportKind.REST,
            TransportKind.USER_STREAM,
            TransportKind.REQUEST_API,
        }:
            raise ValueError("private access requires REST, user stream, or request API transport")


__all__ = [
    "AccessScope",
    "IntegrationBinding",
    "TransportKind",
]
