from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ParticipantKind(StrEnum):
    EXCHANGE = "exchange"
    BROKER = "broker"
    PROVIDER = "provider"


class ExchangeId(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    HYPERLIQUID = "hyperliquid"


class BrokerId(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    IBKR = "ibkr"


class ProviderId(StrEnum):
    MASSIVE = "massive"


ParticipantId = ExchangeId | BrokerId | ProviderId


@dataclass(frozen=True, slots=True)
class ParticipantRef:
    """Typed identity of an external participant.

    The same external name may intentionally occur under different kinds;
    Binance as an exchange and Binance as a broker are different boundaries.
    """

    kind: ParticipantKind
    id: ParticipantId

    def __post_init__(self) -> None:
        if not isinstance(self.id, StrEnum):
            raise TypeError("participant id must be a typed participant identifier")
        _validate_kind(self.kind, self.id)


@dataclass(frozen=True, slots=True)
class ExchangeRef:
    id: ExchangeId

    @property
    def participant(self) -> ParticipantRef:
        return ParticipantRef(ParticipantKind.EXCHANGE, self.id)


@dataclass(frozen=True, slots=True)
class BrokerRef:
    id: BrokerId

    @property
    def participant(self) -> ParticipantRef:
        return ParticipantRef(ParticipantKind.BROKER, self.id)


@dataclass(frozen=True, slots=True)
class ProviderRef:
    id: ProviderId

    @property
    def participant(self) -> ParticipantRef:
        return ParticipantRef(ParticipantKind.PROVIDER, self.id)


def _validate_kind(kind: ParticipantKind, identifier: ParticipantId) -> None:
    expected = {
        ParticipantKind.EXCHANGE: ExchangeId,
        ParticipantKind.BROKER: BrokerId,
        ParticipantKind.PROVIDER: ProviderId,
    }[kind]
    if not isinstance(identifier, expected):
        raise TypeError(f"{kind.value} participant requires {expected.__name__}")


__all__ = [
    "BrokerId",
    "BrokerRef",
    "ExchangeId",
    "ExchangeRef",
    "ParticipantId",
    "ParticipantKind",
    "ParticipantRef",
    "ProviderId",
    "ProviderRef",
]
