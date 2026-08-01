from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.domain.reference import catalog_from_market_rows, market_to_primitive
from kairospy.infrastructure.persistence.reference.sqlite_store import ReferenceStore
from kairospy.application.domain.reference.serde import listing_from_primitive, listing_to_primitive, market_from_primitive
from kairospy.core.reference import ExchangeId, MarketTypeId, SourceSymbol
from kairospy.core.reference import LifecycleEvent, LifecycleEventType


def test_reference_store_persists_catalog_and_events_in_sqlite(tmp_path) -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = ReferenceStore(tmp_path / "reference")
    catalog = catalog_from_market_rows(
        (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "status": "trading",
            },
        ),
        effective_from=as_of,
    )

    store.save_catalog(catalog)
    store.append_events((LifecycleEvent(LifecycleEventType.LISTED, as_of, venue="binance", source_symbol="BTC/USDT"),))

    assert store.database_path.name == "reference.sqlite"
    assert store.database_path.exists()
    stored_market = store.load_catalog().list_markets(at=as_of)[0]
    stored_listing = store.load_catalog().active_listings(at=as_of)[0]
    assert str(stored_listing.exchange_id) == "binance"
    assert str(stored_listing.listing_symbol) == "BTC/USDT"
    assert stored_listing.exchange_instrument_id is None
    assert isinstance(stored_market.venue, ExchangeId)
    assert isinstance(stored_market.market, MarketTypeId)
    assert isinstance(stored_market.source_symbol, SourceSymbol)
    assert str(stored_market.exchange_id) == "binance"
    assert str(stored_market.market_type) == "spot"
    assert str(stored_market.source_symbol) == "BTC/USDT"
    primitive = market_to_primitive(stored_market)
    assert primitive["exchange_id"] == "binance"
    assert primitive["market_type"] == "spot"
    assert primitive["venue"] == "binance"
    assert primitive["market"] == "spot"
    listing_roundtrip = listing_from_primitive(listing_to_primitive(stored_listing))
    market_roundtrip = market_from_primitive(primitive)
    assert str(listing_roundtrip.exchange_id) == "binance"
    assert str(listing_roundtrip.listing_symbol) == "BTC/USDT"
    assert str(market_roundtrip.exchange_id) == "binance"
    assert str(market_roundtrip.market_type) == "spot"
    assert store.load_events()[0].event_type is LifecycleEventType.LISTED
