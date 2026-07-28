from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from kairospy.infrastructure.integrations import BinanceMarketDataConnector, CcxtDriver
from kairospy.core.reference import (
    Asset,
    AssetId,
    AssetType,
    InstrumentDefinition,
    InstrumentId,
    InstrumentType,
    LifecycleEventType,
    ListingDefinition,
    ListingId,
    MarketDefinition,
    MarketId,
    MarketStatus,
    ReferenceCatalog,
    instrument_product_for_market,
)
from kairospy.application.service.domains.reference import catalog_from_reference_rows


UTC = timezone.utc


class FakeMarketExchange:
    def load_markets(self, params=None):
        assert params == {"type": "spot"}
        return {
            "BTC/USDT": {
                "id": "BTCUSDT",
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "spot": True,
                "active": True,
                "precision": {"price": 2, "amount": 6},
                "limits": {"amount": {"min": "0.00001"}, "cost": {"min": "5"}},
            },
            "OLD/USDT": {
                "id": "OLDUSDT",
                "symbol": "OLD/USDT",
                "base": "OLD",
                "quote": "USDT",
                "spot": True,
                "active": False,
            },
        }

    def close(self):
        pass


def test_binance_discovers_markets_as_reference_catalog() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    driver = CcxtDriver(exchange_factory=lambda exchange_id: FakeMarketExchange())

    catalog = BinanceMarketDataConnector(driver).fetch_reference_catalog(as_of=as_of, params={"type": "spot"})

    market = catalog.resolve_market("BTC/USDT", venue="binance", market="spot", at=as_of)
    assert market.market_id == MarketId("market:binance:spot:btc_usdt")
    assert market.instrument_id == InstrumentId("instrument:spot:btc:usdt")
    assert market.status is MarketStatus.ACTIVE
    assert market.min_notional == 5
    assert catalog.get_asset("asset:crypto:btc", as_of).symbol == "BTC"
    assert catalog.resolve_market("OLD/USDT", venue="binance", market="spot", at=as_of).status is MarketStatus.DELISTED


def test_reference_catalog_keeps_symbol_lifecycle_point_in_time() -> None:
    listed = datetime(2020, 1, 1, tzinfo=UTC)
    renamed = datetime(2023, 1, 1, tzinfo=UTC)
    before = datetime(2022, 6, 1, tzinfo=UTC)
    after = datetime(2024, 1, 1, tzinfo=UTC)
    catalog = ReferenceCatalog(
        assets=(
            Asset(AssetId("asset:equity:meta"), AssetType.EQUITY, "META", name="Meta Platforms", effective_from=listed),
        ),
        instruments=(
            InstrumentDefinition(
                InstrumentId("instrument:equity:meta"),
                InstrumentType.EQUITY,
                base_asset_id=AssetId("asset:equity:meta"),
                display_name="Meta Platforms common stock",
                effective_from=listed,
            ),
        ),
        listings=(
            ListingDefinition(
                ListingId("listing:nasdaq:meta"),
                InstrumentId("instrument:equity:meta"),
                "nasdaq",
                "FB",
                status=MarketStatus.ACTIVE,
                effective_from=listed,
            ),
        ),
    )

    catalog.supersede_listing(
        ListingDefinition(
            ListingId("listing:nasdaq:meta"),
            InstrumentId("instrument:equity:meta"),
            "nasdaq",
            "META",
            status=MarketStatus.ACTIVE,
            effective_from=renamed,
        ),
        renamed,
    )

    assert catalog.active_listings("instrument:equity:meta", before, venue="nasdaq")[0].trading_symbol == "FB"
    assert catalog.active_listings("instrument:equity:meta", after, venue="nasdaq")[0].trading_symbol == "META"


def test_market_diff_emits_lifecycle_events() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    old = MarketDefinition(
        MarketId("market:binance:spot:old_usdt"),
        InstrumentId("instrument:spot:old:usdt"),
        ListingId("listing:binance:spot:old_usdt"),
        "binance",
        "spot",
        "OLD/USDT",
        effective_from=as_of,
    )
    changed = replace(old, status=MarketStatus.HALTED)
    new = MarketDefinition(
        MarketId("market:binance:spot:new_usdt"),
        InstrumentId("instrument:spot:new:usdt"),
        ListingId("listing:binance:spot:new_usdt"),
        "binance",
        "spot",
        "NEW/USDT",
        effective_from=as_of,
    )

    events = ReferenceCatalog.diff_markets((old,), (changed, new), event_time=as_of)

    assert [event.event_type for event in events] == [
        LifecycleEventType.LISTED,
        LifecycleEventType.STATUS_CHANGED,
    ]
    assert events[0].source_symbol == "NEW/USDT"
    assert events[1].previous == {"status": "active"}
    assert events[1].current == {"status": "halted"}


def test_reference_product_spec_normalizes_market_aliases_and_identity_segments() -> None:
    swap = instrument_product_for_market("swap")
    derivative = instrument_product_for_market("derivative")

    assert swap.instrument_type is InstrumentType.PERPETUAL
    assert swap.identity_segment == "perpetual"
    assert derivative.instrument_type is InstrumentType.PERPETUAL
    assert derivative.identity_segment == "derivative"


def test_catalog_from_reference_rows_routes_equity_products() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)

    catalog = catalog_from_reference_rows(
        (
            {
                "venue": "nasdaq",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "cik": "320193",
                "currency": "USD",
                "active": True,
            },
        ),
        effective_from=as_of,
        product="equity",
    )

    market = catalog.resolve_market("AAPL", venue="nasdaq", market="equity", at=as_of)
    assert market.market_id == MarketId("market:nasdaq:equity:320193")
    assert market.instrument_id == InstrumentId("instrument:equity:320193")
