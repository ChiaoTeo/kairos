from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic, sleep
from typing import Literal, Protocol

from kairospy.application.usecases.market.domain.datasets import parse_market_dataset_id
from kairospy.domain.market import Bar, OrderBookSnapshot, Quote, RateObservation, TradePrint

from .sources import parse_event_time
from ..domain.specs import MarketDataSpec
from ..domain.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from ..protocol import MarketDataReader, MarketDataWriter, MarketHistoricalClient
from kairospy.application.usecases.market.application.requests import MarketDataRow, MarketTime


BacktestMissingDataAction = Literal["error", "download", "skip"]


class RowWriter(Protocol):
    def __call__(self, rows: Iterable[MarketDataRow]) -> None:
        ...


class HistoricalClientFactory(Protocol):
    def __call__(self, spec: MarketDataSpec) -> MarketHistoricalClient:
        ...


@dataclass(frozen=True, slots=True)
class ReplayMarketDataPolicy:
    start: MarketTime
    end: MarketTime
    on_missing: BacktestMissingDataAction = "error"

    def __post_init__(self) -> None:
        if self.on_missing not in {"error", "download", "skip"}:
            raise ValueError("backtest.market.on_missing must be error, download, or skip")
        if parse_event_time(self.start) >= parse_event_time(self.end):
            raise ValueError("backtest.market.start must be before backtest.market.end")


class MarketReplayService:
    def __init__(
        self,
        reader: MarketDataReader,
        *,
        writer: MarketDataWriter | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: MarketHistoricalClient | None = None,
        historical_client_factory: HistoricalClientFactory | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.policy = policy
        self.historical_client = historical_client
        self.historical_client_factory = historical_client_factory

    def set_historical_client_factory(self, factory: HistoricalClientFactory | None) -> None:
        self.historical_client_factory = factory

    def rows_for_subscriptions(self, subscriptions: Iterable[DataSubscription]) -> tuple[MarketDataRow, ...]:
        rows = [dict(row) for subscription in subscriptions for row in self.rows_for_subscription(subscription)]
        return tuple(sorted(rows, key=lambda row: parse_event_time(row["time"])))

    def rows_for_subscription(self, subscription: DataSubscription) -> tuple[MarketDataRow, ...]:
        if self.policy is None:
            return ()
        rows: list[MarketDataRow] = []
        missing: list[str] = []
        for spec in specs_from_subscription(subscription.spec, start=self.policy.start, end=self.policy.end):
            resolved = self.reader.resolve(spec)
            spec_rows = tuple(self.reader.read(spec))
            if not spec_rows:
                missing.append(resolved.dataset_id)
                if self.policy.on_missing == "download":
                    client = self._historical_client(spec)
                    if client is None:
                        raise RuntimeError(f"historical data is missing and no historical client is configured: {resolved.dataset_id}")
                    if self.writer is None:
                        raise RuntimeError("historical data is missing and no data writer is configured")
                    self.writer.download(spec, client)
                    spec_rows = tuple(self.reader.read(spec))
                if self.policy.on_missing == "error" and not spec_rows:
                    raise RuntimeError(f"historical data is missing: {resolved.dataset_id}")
            rows.extend(spec_rows)
        if missing and self.policy.on_missing == "skip":
            return tuple(rows)
        return tuple(rows)

    def _historical_client(self, spec: MarketDataSpec) -> MarketHistoricalClient | None:
        if self.historical_client_factory is not None:
            return self.historical_client_factory(spec)
        return self.historical_client


def specs_from_subscription(
    subscription: MarketDataSubscriptionSpec,
    *,
    start: MarketTime,
    end: MarketTime,
) -> tuple[MarketDataSpec, ...]:
    if subscription.dataset_id is not None:
        dataset = parse_market_dataset_id(subscription.dataset_id)
        return (
            MarketDataSpec(
                symbol=dataset.source_symbol,
                kind=dataset.kind,
                venue=dataset.venue,
                market=dataset.market,
                timeframe=dataset.timeframe,
                start=start,
                end=end,
                dataset=dataset.dataset_id,
            ),
        )
    specs: list[MarketDataSpec] = []
    for selector in subscription.selectors:
        kind = kind_from_selector(selector)
        timeframe = selector.interval
        if kind == "ohlcv" and timeframe is None:
            raise ValueError("bar market data subscriptions require an interval")
        specs.append(
            MarketDataSpec(
                symbol=str(subscription.market.source_symbol),
                kind=kind,
                venue=str(subscription.market.venue),
                market=str(subscription.market.market),
                timeframe=timeframe,
                start=start,
                end=end,
            )
        )
    return tuple(specs)


def kind_from_selector(selector: object) -> str:
    model = getattr(selector, "model", None)
    if model is Bar:
        return "ohlcv"
    if model is Quote:
        return "ticker"
    if model is TradePrint:
        return "trades"
    if model is OrderBookSnapshot:
        return "orderbook"
    if model is RateObservation:
        return "funding_rate" if getattr(selector, "basis", None) == "funding_rate" else "rate"
    raise ValueError(f"unsupported backtest market selector model: {getattr(model, '__name__', model)!r}")


def replay_rows(rows: Iterable[MarketDataRow], *, speed: float, write: RowWriter) -> int:
    if speed < 0:
        raise ValueError("replay speed cannot be negative")
    previous_time: float | None = None
    wall_start = monotonic()
    replay_start: float | None = None
    for row in rows:
        current_time = _timestamp(row["time"])
        if speed > 0:
            if replay_start is None:
                replay_start = current_time
            target_elapsed = (current_time - replay_start) / speed
            sleep_seconds = target_elapsed - (monotonic() - wall_start)
            if previous_time is not None and sleep_seconds > 0:
                sleep(sleep_seconds)
        previous_time = current_time
        write((row,))
    return 0


def _timestamp(value: object) -> float:
    if not isinstance(value, str):
        raise ValueError(f"replay row time must be ISO-8601 text: {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"replay row time must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc).timestamp()


__all__ = [
    "BacktestMissingDataAction",
    "HistoricalClientFactory",
    "MarketReplayService",
    "ReplayMarketDataPolicy",
    "RowWriter",
    "kind_from_selector",
    "replay_rows",
    "specs_from_subscription",
]
