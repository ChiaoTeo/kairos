from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Exchange:
    exchange_id: str
    name: str
    entity_id: str | None = None
    country: str | None = None
    timezone: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange_id", _required_text(self.exchange_id, "exchange_id"))
        object.__setattr__(self, "name", _required_text(self.name, "exchange name"))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class Broker:
    broker_id: str
    name: str
    exchange_id: str | None = None
    entity_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker_id", _required_text(self.broker_id, "broker_id"))
        object.__setattr__(self, "name", _required_text(self.name, "broker name"))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class Provider:
    provider_id: str
    name: str
    entity_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _required_text(self.provider_id, "provider_id"))
        object.__setattr__(self, "name", _required_text(self.name, "provider name"))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _required_text(value: str, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


BINANCE = Exchange(
    "binance",
    "Binance",
    entity_id="entity:venue:binance",
    metadata={"aliases": ("binance",), "default_markets": ("spot", "swap")},
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


__all__ = [
    "BINANCE",
    "Broker",
    "Exchange",
    "HYPERLIQUID",
    "NASDAQ",
    "NYSE",
    "OKEX",
    "OKX",
    "Provider",
]
