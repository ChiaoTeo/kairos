from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from kairospy.core.reference import MarketRef, MarketResolver


DataMode = Literal["history", "stream", "both"]


class DataBinder(Protocol):
    def attach(
        self,
        name: str,
        *,
        dataset: str | None = None,
        stream: str | None = None,
        mode: DataMode = "history",
    ) -> object:
        ...


@dataclass(frozen=True, slots=True)
class MarketDataBinding:
    data: DataBinder
    market_ref: MarketRef

    def bind(
        self,
        name: str,
        *,
        kind: str,
        timeframe: str | None = None,
        mode: DataMode = "history",
    ) -> object:
        dataset = self.dataset(kind, timeframe=timeframe) if mode in {"history", "both"} else None
        stream = self.stream(kind, timeframe=timeframe) if mode in {"stream", "both"} else None
        return self.data.attach(name, dataset=dataset, stream=stream, mode=mode)

    def ohlcv(self, timeframe: str, *, name: str = "bars", mode: DataMode = "history") -> object:
        return self.bind(name, kind="ohlcv", timeframe=timeframe, mode=mode)

    def ticker(self, *, name: str = "ticker", mode: DataMode = "history") -> object:
        return self.bind(name, kind="ticker", mode=mode)

    def orderbook(self, *, name: str = "orderbook", mode: DataMode = "stream") -> object:
        return self.bind(name, kind="orderbook", mode=mode)

    def dataset(self, kind: str, *, timeframe: str | None = None) -> str:
        return ".".join(part for part in ("market", _name(kind), self.market_ref.market_key, timeframe) if part)

    def stream(self, kind: str, *, timeframe: str | None = None) -> str:
        return ".".join(part for part in ("market", _name(kind), self.market_ref.market_key, timeframe) if part)


def bind_market_data(
    data: DataBinder,
    resolver: MarketResolver,
    market_ref: object | MarketRef,
    *,
    venue: str | None = None,
    market: str | None = None,
) -> MarketDataBinding:
    return MarketDataBinding(data, resolver.resolve(market_ref, venue=venue, market=market))


def _name(value: object) -> str:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("market data kind cannot be empty")
    return text


__all__ = ["DataBinder", "MarketDataBinding", "bind_market_data"]
