from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.ports import DataSubscription, MarketDataPort, MarketDataSubscriptionSpec
from kairospy.application.service.domain.market import MarketDataOperationsService, MarketDataResolver, MarketDataSpec
from kairospy.application.service.domain.market.datasets import parse_market_dataset_id
from kairospy.application.service.domain.market.operations import HistoricalMarketDataClient
from kairospy.application.service.domain.market.sources import IterableMarketEventSource, parse_event_time, runtime_envelope_from_row
from kairospy.core.market import Bar, OrderBookSnapshot, Quote, RateObservation, TradePrint
from kairospy.infrastructure.data import DataStore

from .common import MarketSubscriptionState, RuntimeMarketDataServiceView


BacktestMissingDataAction = Literal["error", "download", "skip"]


@dataclass(frozen=True, slots=True)
class ReplayMarketDataPolicy:
    start: object
    end: object
    on_missing: BacktestMissingDataAction = "error"

    def __post_init__(self) -> None:
        if self.on_missing not in {"error", "download", "skip"}:
            raise ValueError("backtest.market.on_missing must be error, download, or skip")
        if parse_event_time(self.start) >= parse_event_time(self.end):
            raise ValueError("backtest.market.start must be before backtest.market.end")


class ReplayMarketDataService(MarketDataOperationsService, MarketSubscriptionState, MarketDataPort):
    def __init__(
        self,
        store: DataStore,
        *,
        resolver: MarketDataResolver | None = None,
        policy: ReplayMarketDataPolicy | None = None,
        historical_client: HistoricalMarketDataClient | None = None,
        historical_client_factory: Callable[[MarketDataSpec], HistoricalMarketDataClient] | None = None,
    ) -> None:
        MarketDataOperationsService.__init__(self, store, resolver=resolver)
        MarketSubscriptionState.__init__(self)
        self.policy = policy
        self.historical_client = historical_client
        self.historical_client_factory = historical_client_factory
        self._subscription_rows: dict[str, tuple[Mapping[str, object], ...]] = {}

    def set_historical_client_factory(
        self,
        factory: Callable[[MarketDataSpec], HistoricalMarketDataClient] | None,
    ) -> None:
        self.historical_client_factory = factory

    def source_from_store(self, spec: MarketDataSpec) -> IterableMarketEventSource:
        resolved = self.resolve(spec)
        return IterableMarketEventSource(resolved.stream_name, self.read(spec))

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = super().subscribe(spec)
        if self.policy is not None:
            self._subscription_rows[subscription.key] = tuple(self._rows_for_subscription(spec))
        return subscription

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if not self.subscriptions():
            raise RuntimeError("backtest strategy did not subscribe to market data")
        rows = sorted(
            (dict(row) for batch in self._subscription_rows.values() for row in batch),
            key=lambda row: parse_event_time(row["time"]),
        )
        for sequence, row in enumerate(rows, start=1):
            yield runtime_envelope_from_row(row, sequence=sequence, stream=str(row.get("source") or "backtest"))

    def view(self) -> RuntimeMarketDataServiceView:
        subscriptions = self.subscriptions()
        return RuntimeMarketDataServiceView(
            source=type(self.store).__name__,
            subscription_count=len(subscriptions),
            subscriptions=subscriptions,
        )

    def _rows_for_subscription(self, subscription: MarketDataSubscriptionSpec) -> tuple[Mapping[str, object], ...]:
        if self.policy is None:
            return ()
        specs = tuple(_specs_from_subscription(subscription, start=self.policy.start, end=self.policy.end))
        rows: list[Mapping[str, object]] = []
        missing: list[str] = []
        for spec in specs:
            resolved = self.resolve(spec)
            spec_rows = tuple(self.read(spec))
            if not spec_rows:
                missing.append(resolved.dataset_id)
                if self.policy.on_missing == "download":
                    client = self._historical_client(spec)
                    if client is None:
                        raise RuntimeError(f"historical data is missing and no historical client is configured: {resolved.dataset_id}")
                    self.download(spec, client)
                    spec_rows = tuple(self.read(spec))
                if self.policy.on_missing == "error" and not spec_rows:
                    raise RuntimeError(f"historical data is missing: {resolved.dataset_id}")
            rows.extend(spec_rows)
        if missing and self.policy.on_missing == "skip":
            return tuple(rows)
        return tuple(rows)

    def _historical_client(self, spec: MarketDataSpec) -> HistoricalMarketDataClient | None:
        if self.historical_client_factory is not None:
            return self.historical_client_factory(spec)
        return self.historical_client


def _specs_from_subscription(
    subscription: MarketDataSubscriptionSpec,
    *,
    start: object,
    end: object,
) -> Iterable[MarketDataSpec]:
    if subscription.dataset_id is not None:
        dataset = parse_market_dataset_id(subscription.dataset_id)
        yield MarketDataSpec(
            symbol=dataset.source_symbol,
            kind=dataset.kind,
            venue=dataset.venue,
            market=dataset.market,
            timeframe=dataset.timeframe,
            start=start,
            end=end,
            dataset=dataset.dataset_id,
        )
        return
    for selector in subscription.selectors:
        kind = _kind_from_selector(selector)
        timeframe = selector.interval
        if kind == "ohlcv" and timeframe is None:
            raise ValueError("bar market data subscriptions require an interval")
        yield MarketDataSpec(
            symbol=str(subscription.market.source_symbol),
            kind=kind,
            venue=str(subscription.market.venue),
            market=str(subscription.market.market),
            timeframe=timeframe,
            start=start,
            end=end,
        )


def _kind_from_selector(selector: object) -> str:
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


__all__ = ["ReplayMarketDataPolicy", "ReplayMarketDataService", "RuntimeMarketDataServiceView"]
