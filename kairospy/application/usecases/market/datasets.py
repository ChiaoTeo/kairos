from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from kairospy.core.market import Bar, MarketSelector, OptionGreeks, OrderBookSnapshot, Quote, RateObservation, TradePrint
from kairospy.core.reference import MarketRef
from kairospy.core.reference.identity import reference_slug


@dataclass(frozen=True, slots=True)
class MarketDataset:
    dataset_id: str
    kind: str
    venue: str
    market: str
    symbol: str
    timeframe: str | None = None

    @property
    def market_key(self) -> str:
        return "_".join((self.venue, self.market, self.symbol))

    @property
    def source_symbol(self) -> str:
        if self.market == "option" or self.kind in {"option_greeks", "greeks"}:
            return "-".join(part.upper() for part in self.symbol.split("_") if part)
        if "_" in self.symbol:
            base, quote = self.symbol.split("_", 1)
            return f"{base.upper()}/{quote.upper()}"
        return self.symbol.upper()

    @property
    def market_ref(self) -> MarketRef:
        return MarketRef.ephemeral(venue=self.venue, market=self.market, source_symbol=self.source_symbol)

    @property
    def selector(self) -> MarketSelector:
        if self.kind == "ohlcv":
            if self.timeframe is None:
                raise ValueError("ohlcv dataset id requires a timeframe")
            return Bar.select(interval=self.timeframe)
        if self.kind in {"ticker", "quote"}:
            return Quote.select()
        if self.kind == "orderbook":
            return OrderBookSnapshot.select()
        if self.kind in {"trades", "trade"}:
            return TradePrint.select()
        if self.kind == "funding_rate":
            return RateObservation.select(basis="funding_rate")
        if self.kind == "rate":
            return RateObservation.select()
        if self.kind in {"option_greeks", "greeks"}:
            return OptionGreeks.select()
        raise ValueError(f"unsupported market dataset kind: {self.kind}")


def market_dataset_id(kind: object, market_ref: MarketRef, *, timeframe: str | None = None) -> str:
    parts = (
        "market",
        _dataset_kind(kind),
        reference_slug(market_ref.venue),
        reference_slug(market_ref.market),
        reference_slug(market_ref.source_symbol),
        _optional_segment(timeframe),
    )
    return ".".join(part for part in parts if part)


def parse_market_dataset_id(value: object) -> MarketDataset:
    text = str(value).strip()
    parts = text.split(".")
    if len(parts) >= 3 and parts[0] == "market" and "_" in parts[2]:
        raise ValueError(f"market dataset id must use canonical segments: {text!r}")
    if len(parts) < 5 or parts[0] != "market":
        raise ValueError(f"invalid market dataset id: {text!r}")
    kind = _dataset_kind(parts[1])
    timeframe = parts[5] if len(parts) > 5 else None
    return MarketDataset(
        dataset_id=".".join(parts[:6]) if timeframe is not None else ".".join(parts[:5]),
        kind=kind,
        venue=parts[2],
        market=parts[3],
        symbol=parts[4],
        timeframe=timeframe,
    )


@dataclass(frozen=True, slots=True)
class MarketPartition:
    time_grain: str = "none"
    path_fields: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_partitioned(self) -> bool:
        return self.time_grain != "none" or bool(self.path_fields)


class MarketDatasetStore(Protocol):
    def read_rows(
        self,
        dataset: object,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        partition: MarketPartition | None = None,
    ) -> list[Mapping[str, object]]:
        ...

    def write(
        self,
        dataset: object,
        rows: Iterable[Mapping[str, object]],
        *,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def write_bars(
        self,
        dataset: object,
        bars: Iterable[Bar],
        *,
        market: MarketRef,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def write_funding_rates(
        self,
        dataset: object,
        rates: Iterable[RateObservation],
        *,
        market: MarketRef,
        mode: str = "append",
        partition: MarketPartition | None = None,
    ) -> Path:
        ...

    def consume(
        self,
        dataset: object,
        events: AsyncIterable[Mapping[str, object]],
        *,
        partition: MarketPartition | None = None,
        limit: int | None = None,
    ) -> int:
        ...


def _dataset_kind(value: object) -> str:
    text = str(value).strip().lower()
    if text == "trade":
        return "trades"
    if text == "bar":
        return "ohlcv"
    if not text:
        raise ValueError("market data kind cannot be empty")
    return text


def _optional_segment(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        raise ValueError("market data timeframe cannot be empty")
    return text


__all__ = [
    "MarketDataset",
    "MarketDatasetStore",
    "MarketPartition",
    "market_dataset_id",
    "parse_market_dataset_id",
]
