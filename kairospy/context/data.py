from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Literal

from kairospy.data import DataStore, StreamFeed
from kairospy.reference import MarketRef, MarketResolver


DataMode = Literal["history", "stream", "both"]


@dataclass(frozen=True, slots=True)
class DataBinding:
    name: str
    dataset: str | None = None
    stream: str | None = None
    mode: DataMode = "history"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("data binding name is required")
        if self.mode not in {"history", "stream", "both"}:
            raise ValueError("data binding mode must be history, stream, or both")
        if self.dataset is None and self.stream is None:
            raise ValueError("data binding requires dataset or stream")
        if self.mode == "history" and self.dataset is None:
            raise ValueError("history data binding requires dataset")
        if self.mode == "stream" and self.stream is None:
            raise ValueError("stream data binding requires stream")
        if self.mode == "both" and (self.dataset is None or self.stream is None):
            raise ValueError("both data binding requires dataset and stream")

    def to_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "name": self.name,
                "dataset": self.dataset,
                "stream": self.stream,
                "mode": self.mode,
            }.items()
            if value is not None
        }


class DataContext:
    def __init__(
        self,
        store: DataStore,
        *,
        stream_feed: StreamFeed | None = None,
        markets: MarketResolver | None = None,
    ) -> None:
        self.store = store
        self.stream_feed = stream_feed
        self.markets = markets or MarketResolver()
        self._bindings: dict[str, DataBinding] = {}

    def attach(
        self,
        name: str,
        *,
        dataset: str | None = None,
        stream: str | None = None,
        mode: DataMode = "history",
    ) -> "DataView":
        binding = DataBinding(name=name, dataset=dataset, stream=stream, mode=mode)
        existing = self._bindings.get(name)
        if existing is not None and existing != binding:
            raise ValueError(f"data binding {name!r} is already attached")
        self._bindings[name] = binding
        return DataView(self, binding)

    def view(self, name: str) -> "DataView":
        try:
            return DataView(self, self._bindings[name])
        except KeyError as error:
            raise KeyError(f"unknown data binding: {name}") from error

    def __getitem__(self, name: str) -> "DataView":
        return self.view(name)

    def for_market(
        self,
        market_ref: object | MarketRef,
        *,
        venue: str | None = None,
        market: str | None = None,
    ) -> "MarketDataView":
        return MarketDataView(self, self.markets.resolve(market_ref, venue=venue, market=market))

    def snapshot(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "bindings": {
                name: binding.to_dict()
                for name, binding in sorted(self._bindings.items())
            },
        }
        markets = self.markets.snapshot()
        if markets:
            snapshot["markets"] = markets
        return snapshot


@dataclass(frozen=True, slots=True)
class DataView:
    context: DataContext
    binding: DataBinding

    @property
    def name(self) -> str:
        return self.binding.name

    def rows(
        self,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        if self.binding.dataset is None:
            raise RuntimeError(f"data view {self.name!r} has no historical dataset")
        return self.context.store.read_rows(
            self.binding.dataset,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )

    def frame(
        self,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> object:
        if self.binding.dataset is None:
            raise RuntimeError(f"data view {self.name!r} has no historical dataset")
        return self.context.store.read(
            self.binding.dataset,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )

    def latest(self) -> dict[str, object] | None:
        rows = self.rows()
        return rows[-1] if rows else None

    def events(self) -> AsyncIterator[dict[str, object]]:
        if self.binding.stream is None:
            raise RuntimeError(f"data view {self.name!r} has no data stream")
        if self.context.stream_feed is None:
            raise RuntimeError("data context has no stream feed")
        return self.context.stream_feed.subscribe(self.binding.stream)


@dataclass(frozen=True, slots=True)
class MarketDataView:
    context: DataContext
    market_ref: MarketRef

    def bind(
        self,
        name: str,
        *,
        kind: str,
        timeframe: str | None = None,
        mode: DataMode = "history",
    ) -> DataView:
        dataset = self.dataset(kind, timeframe=timeframe) if mode in {"history", "both"} else None
        stream = self.stream(kind, timeframe=timeframe) if mode in {"stream", "both"} else None
        return self.context.attach(name, dataset=dataset, stream=stream, mode=mode)

    def ohlcv(self, timeframe: str, *, name: str = "bars", mode: DataMode = "history") -> DataView:
        return self.bind(name, kind="ohlcv", timeframe=timeframe, mode=mode)

    def ticker(self, *, name: str = "ticker", mode: DataMode = "history") -> DataView:
        return self.bind(name, kind="ticker", mode=mode)

    def orderbook(self, *, name: str = "orderbook", mode: DataMode = "stream") -> DataView:
        return self.bind(name, kind="orderbook", mode=mode)

    def dataset(self, kind: str, *, timeframe: str | None = None) -> str:
        return ".".join(part for part in ("market", _name(kind), self.market_ref.market_key, timeframe) if part)

    def stream(self, kind: str, *, timeframe: str | None = None) -> str:
        return ".".join(part for part in ("market", _name(kind), self.market_ref.market_key, timeframe) if part)


def _name(value: object) -> str:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("data kind cannot be empty")
    return text
