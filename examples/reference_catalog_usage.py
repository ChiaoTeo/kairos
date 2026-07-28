from __future__ import annotations

from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.core.market import FIELD_QUOTE_ASK, FIELD_QUOTE_BID, MarketSubscriptionRegistry
from kairospy.core.reference import MarketResolver
from kairospy.service.domains.market import bind_market_data
from kairospy.service.domains.reference import catalog_from_market_rows
from kairospy.service.domains.reference import ReferenceStore


def main() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    catalog = catalog_from_market_rows(
        [
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "status": "trading",
                "price_precision": 2,
                "amount_precision": 6,
                "min_notional": "5",
            }
        ],
        effective_from=as_of,
    )

    with TemporaryDirectory() as temporary:
        store = ReferenceStore(f"{temporary}/reference")
        store.save_catalog(catalog)
        persisted_catalog = store.load_catalog()

        resolver = MarketResolver(persisted_catalog, as_of=as_of)
        market = resolver.resolve("BTC/USDT", venue="binance", market="spot")

        data = DataContext(DataStore(f"{temporary}/data", storage_format="jsonl"))
        bars = bind_market_data(data, resolver, market).ohlcv("1m", mode="both")

        subscriptions = MarketSubscriptionRegistry()
        quote_subscription = subscriptions.subscribe_fields(
            "market",
            market.market_id,
            (FIELD_QUOTE_BID, FIELD_QUOTE_ASK),
            venue=market.venue,
            market=market.market,
            source_symbol=market.source_symbol,
            identity=market.market_key,
        )

        print("canonical market id:", market.market_id)
        print("canonical instrument id:", market.instrument_id)
        print("runtime market key:", market.market_key)
        print("dataset:", bars.binding.dataset)
        print("stream:", bars.binding.stream)
        print("subscription:", quote_subscription.key)
        print("stream plans:", [plan.key for plan in quote_subscription.stream_plans])


if __name__ == "__main__":
    main()
