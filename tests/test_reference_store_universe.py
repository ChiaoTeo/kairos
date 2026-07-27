from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from kairospy.integrations.instruments import catalog_from_market_rows
from kairospy.core.reference import (
    AssetId,
    InstrumentType,
    LifecycleEventType,
    MarketStatus,
    ReferenceCatalog,
    ReferenceStore,
    UniverseQuery,
    UniverseSelector,
)


UTC = timezone.utc


def _catalog(as_of: datetime):
    return catalog_from_market_rows(
        (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "venue_instrument_id": "BTCUSDT",
                "base": "BTC",
                "quote": "USDT",
                "active": True,
                "price_precision": 2,
                "amount_precision": 6,
                "min_notional": "5",
            },
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "ETH/USDT",
                "venue_instrument_id": "ETHUSDT",
                "base": "ETH",
                "quote": "USDT",
                "active": True,
            },
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/EUR",
                "venue_instrument_id": "BTCEUR",
                "base": "BTC",
                "quote": "EUR",
                "active": True,
            },
        ),
        effective_from=as_of,
    )


def test_reference_store_round_trips_catalog_and_lifecycle_events(tmp_path) -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    catalog = _catalog(as_of)
    store = ReferenceStore(tmp_path / "reference")

    store.save_catalog(catalog)
    loaded = store.load_catalog()

    assert store.database_path == tmp_path / "reference" / "reference.sqlite"
    assert store.database_path.exists()
    assert not (tmp_path / "reference" / "markets.jsonl").exists()

    market = loaded.resolve_market("BTC/USDT", venue="binance", market="spot", at=as_of)
    assert market.min_notional == 5
    assert loaded.get_instrument(market.instrument_id, as_of).instrument_type is InstrumentType.SPOT
    assert loaded.get_asset("asset:crypto:usdt", as_of).symbol == "USDT"

    changed = replace(market, status=MarketStatus.HALTED)
    events = ReferenceCatalog.diff_markets((market,), (changed,), event_time=as_of)
    store.append_events(events)

    assert not (tmp_path / "reference" / "lifecycle_events.jsonl").exists()
    restored_events = store.load_events()
    assert restored_events[0].event_type is LifecycleEventType.STATUS_CHANGED
    assert restored_events[0].market_id == market.market_id
    assert restored_events[0].current == {"status": "halted"}


def test_universe_selector_filters_by_venue_market_quote_and_symbols() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    catalog = _catalog(as_of)

    universe = UniverseSelector(catalog).select(
        "binance_usdt_spot",
        at=as_of,
        query=UniverseQuery(
            venue="binance",
            market="spot",
            quote_asset_id=AssetId("asset:crypto:usdt"),
            symbols=("ETH/USDT", "BTC/USDT"),
        ),
    )

    assert universe.source_symbols == ("BTC/USDT", "ETH/USDT")
    assert universe.market_ids == ("market:binance:spot:btc_usdt", "market:binance:spot:eth_usdt")
