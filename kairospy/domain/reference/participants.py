from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .identity import BrokerId, EntityId, ExchangeId, ProviderId, ReferenceId


class ParticipantKind(StrEnum):
    EXCHANGE = "exchange"
    BROKER = "broker"
    PROVIDER = "provider"


ParticipantId = ExchangeId | BrokerId | ProviderId


@dataclass(frozen=True, slots=True)
class ParticipantRef:
    """Shared identity of an external exchange, broker, or provider."""

    kind: ParticipantKind
    id: ParticipantId

    def __post_init__(self) -> None:
        if not isinstance(self.id, (ExchangeId, BrokerId, ProviderId)):
            raise TypeError("participant id must be a shared reference identifier")
        expected = {
            ParticipantKind.EXCHANGE: ExchangeId,
            ParticipantKind.BROKER: BrokerId,
            ParticipantKind.PROVIDER: ProviderId,
        }[self.kind]
        if not isinstance(self.id, expected):
            raise TypeError(f"{self.kind.value} participant requires {expected.__name__}")


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


@dataclass(frozen=True, slots=True)
class Exchange:
    exchange_id: ExchangeId | str
    name: str
    entity_id: EntityId | str | None = None
    country: str | None = None
    timezone: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange_id", _id(self.exchange_id, ExchangeId, "exchange_id"))
        object.__setattr__(self, "entity_id", None if self.entity_id is None else _id(self.entity_id, EntityId, "entity_id"))
        object.__setattr__(self, "name", _required_text(self.name, "exchange name"))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class Broker:
    broker_id: BrokerId | str
    name: str
    exchange_id: ExchangeId | str | None = None
    entity_id: EntityId | str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker_id", _id(self.broker_id, BrokerId, "broker_id"))
        object.__setattr__(self, "exchange_id", None if self.exchange_id is None else _id(self.exchange_id, ExchangeId, "exchange_id"))
        object.__setattr__(self, "entity_id", None if self.entity_id is None else _id(self.entity_id, EntityId, "entity_id"))
        object.__setattr__(self, "name", _required_text(self.name, "broker name"))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class Provider:
    provider_id: ProviderId | str
    name: str
    entity_id: EntityId | str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _id(self.provider_id, ProviderId, "provider_id"))
        object.__setattr__(self, "entity_id", None if self.entity_id is None else _id(self.entity_id, EntityId, "entity_id"))
        object.__setattr__(self, "name", _required_text(self.name, "provider name"))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ParticipantRegistry:
    _exchanges: Mapping[str, Exchange] = field(default_factory=dict)
    _brokers: Mapping[str, Broker] = field(default_factory=dict)
    _providers: Mapping[str, Provider] = field(default_factory=dict)
    _exchange_aliases: Mapping[str, str] = field(default_factory=dict)
    _broker_aliases: Mapping[str, str] = field(default_factory=dict)
    _provider_aliases: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        exchanges: tuple[Exchange, ...] = (),
        brokers: tuple[Broker, ...] = (),
        providers: tuple[Provider, ...] = (),
    ) -> "ParticipantRegistry":
        exchange_map = _index(exchanges, "exchange", lambda item: str(item.exchange_id))
        broker_map = _index(brokers, "broker", lambda item: str(item.broker_id))
        provider_map = _index(providers, "provider", lambda item: str(item.provider_id))
        return cls(
            MappingProxyType(exchange_map),
            MappingProxyType(broker_map),
            MappingProxyType(provider_map),
            MappingProxyType(_aliases(exchange_map.values(), lambda item: str(item.exchange_id))),
            MappingProxyType(_aliases(broker_map.values(), lambda item: str(item.broker_id))),
            MappingProxyType(_aliases(provider_map.values(), lambda item: str(item.provider_id))),
        )

    def exchanges(self) -> tuple[Exchange, ...]:
        return tuple(self._exchanges[key] for key in sorted(self._exchanges))

    def brokers(self) -> tuple[Broker, ...]:
        return tuple(self._brokers[key] for key in sorted(self._brokers))

    def providers(self) -> tuple[Provider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    def resolve_exchange(self, value: ExchangeId | str) -> Exchange:
        key = _lookup_key(value)
        exchange_id = self._exchange_aliases.get(key, key)
        try:
            return self._exchanges[exchange_id]
        except KeyError as error:
            raise KeyError(f"unknown exchange: {value}") from error

    def resolve_broker(self, value: BrokerId | str) -> Broker:
        key = _lookup_key(value)
        broker_id = self._broker_aliases.get(key, key)
        try:
            return self._brokers[broker_id]
        except KeyError as error:
            raise KeyError(f"unknown broker: {value}") from error

    def resolve_provider(self, value: ProviderId | str) -> Provider:
        key = _lookup_key(value)
        provider_id = self._provider_aliases.get(key, key)
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise KeyError(f"unknown provider: {value}") from error


def _required_text(value: str | ReferenceId, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))


def _index(items: tuple[object, ...], label: str, key_fn) -> dict[str, object]:
    values: dict[str, object] = {}
    for item in items:
        key = key_fn(item)
        if key in values:
            raise ValueError(f"duplicate {label}: {key}")
        values[key] = item
    return values


def _aliases(items, id_fn) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        participant_id = id_fn(item)
        for alias in (participant_id, *_metadata_texts(item.metadata, "aliases")):
            key = _lookup_key(alias)
            current = values.get(key)
            if current is not None and current != participant_id:
                raise ValueError(f"duplicate participant alias: {alias}")
            values[key] = participant_id
    return values


def _metadata_texts(metadata: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = metadata.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _lookup_key(value: str | ReferenceId) -> str:
    return _required_text(value, "participant reference").casefold()


BINANCE = Exchange(
    "binance",
    "Binance",
    entity_id="entity:venue:binance",
    metadata={"aliases": ("binance",), "default_markets": ("spot", "future", "swap")},
)
HYPERLIQUID = Exchange(
    "hyperliquid",
    "Hyperliquid",
    entity_id="entity:venue:hyperliquid",
    metadata={"aliases": ("hyperliquid",), "default_markets": ("swap",)},
)
OKX = Exchange(
    "okx",
    "OKX",
    entity_id="entity:venue:okx",
    metadata={"aliases": ("okx", "okex"), "default_markets": ("spot", "swap")},
)
NASDAQ = Exchange(
    "nasdaq",
    "Nasdaq",
    entity_id="entity:venue:nasdaq",
    country="US",
    timezone="America/New_York",
    metadata={"aliases": ("nasdaq", "xnas"), "mic": "XNAS", "default_markets": ("equity",)},
)
NYSE = Exchange(
    "nyse",
    "New York Stock Exchange",
    entity_id="entity:venue:nyse",
    country="US",
    timezone="America/New_York",
    metadata={"aliases": ("nyse", "xnys"), "mic": "XNYS", "default_markets": ("equity",)},
)
OKEX = OKX

BINANCE_BROKER = Broker(
    "binance",
    "Binance",
    exchange_id=BINANCE.exchange_id,
    entity_id=BINANCE.entity_id,
    metadata={"default_markets": ("spot",), "credential_kind": "api_key_secret"},
)
OKX_BROKER = Broker(
    "okx",
    "OKX",
    exchange_id=OKX.exchange_id,
    entity_id=OKX.entity_id,
    metadata={
        "aliases": ("okex",),
        "default_markets": ("spot", "swap"),
        "credential_kind": "api_key_secret_passphrase",
    },
)
MASSIVE = Provider(
    "massive",
    "Massive",
    entity_id="entity:provider:massive",
    metadata={"default_markets": ("equity",)},
)

DEFAULT_PARTICIPANTS = ParticipantRegistry.build(
    exchanges=(BINANCE, HYPERLIQUID, OKX, NASDAQ, NYSE),
    brokers=(BINANCE_BROKER, OKX_BROKER),
    providers=(MASSIVE,),
)


def exchanges() -> tuple[Exchange, ...]:
    return DEFAULT_PARTICIPANTS.exchanges()


def brokers() -> tuple[Broker, ...]:
    return DEFAULT_PARTICIPANTS.brokers()


def providers() -> tuple[Provider, ...]:
    return DEFAULT_PARTICIPANTS.providers()


def resolve_exchange(value: ExchangeId | str) -> Exchange:
    return DEFAULT_PARTICIPANTS.resolve_exchange(value)


def resolve_broker(value: BrokerId | str) -> Broker:
    return DEFAULT_PARTICIPANTS.resolve_broker(value)


def resolve_provider(value: ProviderId | str) -> Provider:
    return DEFAULT_PARTICIPANTS.resolve_provider(value)


__all__ = [
    "BINANCE",
    "BINANCE_BROKER",
    "Broker",
    "BrokerId",
    "BrokerRef",
    "DEFAULT_PARTICIPANTS",
    "Exchange",
    "ExchangeId",
    "ExchangeRef",
    "HYPERLIQUID",
    "MASSIVE",
    "NASDAQ",
    "NYSE",
    "OKEX",
    "OKX",
    "OKX_BROKER",
    "ParticipantRegistry",
    "ParticipantId",
    "ParticipantKind",
    "ParticipantRef",
    "Provider",
    "ProviderId",
    "ProviderRef",
    "brokers",
    "exchanges",
    "providers",
    "resolve_broker",
    "resolve_exchange",
    "resolve_provider",
]
