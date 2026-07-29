from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.service.domain.market import MarketDataResolver, MarketDataSpec
from kairospy.application.service.modes.backtest import BacktestMarketDataService
from kairospy.application.runtime.services import MarketDataSubscriptionSpec
from kairospy.core.market import MarketEvent, Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore


class EmptyStrategy:
    strategy_id = "s"

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: object) -> None:
        return None

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


async def _collect(source: object) -> list[object]:
    return [event async for event in source.events()]


def test_store_backed_market_data_service_feeds_runtime_projection(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service = BacktestMarketDataService(
        DataStore(tmp_path, storage_format="jsonl"),
        resolver=MarketDataResolver(MarketResolver(default_venue="binance", default_market="spot")),
    )
    spec = MarketDataSpec("BTC/USDT", "quote", venue="binance", market="spot")
    resolved = service.resolve(spec)
    service.store.write(
        resolved.dataset_id,
        (
            {
                "time": now.isoformat(),
                "kind": "quote",
                "instrument_id": resolved.market_ref.instrument_id,
                "market_id": resolved.market_ref.market_id,
                "market_key": resolved.market_ref.market_key,
                "bid": "100",
                "ask": "101",
            },
        ),
        mode="replace",
    )

    rows = service.read(spec)
    assert rows[0]["bid"] == "100"

    source = service.source_from_store(spec)
    events = asyncio.run(_collect(source))
    assert isinstance(events[0].payload, MarketEvent)
    assert isinstance(events[0].payload.value, Quote)
    assert events[0].payload.value.ask == Decimal("101")

    service.subscribe(MarketDataSubscriptionSpec(resolved.market_ref, (Quote,), identity="strategy-a"))
    kernel = RuntimeKernel(EmptyStrategy(), data=service)
    session = kernel.start()
    session.process(events[0])

    assert kernel.views.require("market.service").subscription_count == 1
    assert kernel.views.require("market.subscriptions").active_count == 1
    assert kernel.views.require("market.quotes").quotes[0].ask == Decimal("101")
