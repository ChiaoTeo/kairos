from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.application.usecases.reference.application.component import ReferenceApplication
from kairospy.application.usecases.reference.application.builders import catalog_from_market_rows
from kairospy.application.usecases.reference.application.query import ReferenceQuery
from kairospy.application.usecases.reference.application.requests import ReferenceCatalogRequest
from kairospy.application.usecases.reference.services.refresh import ReferenceRefreshService
from kairospy.application.actor.market.application import ReferenceActor
from kairospy.domain.reference import AssetType, FinancialProductDefinition, FinancialProductStatus, FinancialProductType, LifecycleEvent, LifecycleEventType, ReferenceCatalog
from kairospy.domain.reference import AssetId, FinancialProductId, ProviderId
from kairospy.infrastructure.messaging import InMemoryMessageBus


class MemoryReferenceStore:
    def __init__(self) -> None:
        self.catalog_value = ReferenceCatalog()
        self.events_value: tuple[LifecycleEvent, ...] = ()

    def save_catalog(self, catalog: ReferenceCatalog) -> None:
        self.catalog_value = catalog

    def load_catalog(self) -> ReferenceCatalog:
        return self.catalog_value

    def append_events(self, events) -> None:
        self.events_value += tuple(events)

    def load_events(self) -> tuple[LifecycleEvent, ...]:
        return self.events_value


class SnapshotSource:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog_value = catalog

    def catalog(self, request: ReferenceCatalogRequest) -> ReferenceCatalog:
        return self.catalog_value


class ScopedSnapshotSource:
    def __init__(self, catalogs: dict[str, ReferenceCatalog]) -> None:
        self.catalogs = catalogs
        self.requests: list[ReferenceCatalogRequest] = []

    def catalog(self, request: ReferenceCatalogRequest) -> ReferenceCatalog:
        self.requests.append(request)
        return self.catalogs[str(request.underlying).upper()]


def test_reference_application_owns_catalog_and_resolution_capabilities() -> None:
    app = ReferenceApplication(MemoryReferenceStore(), default_venue="binance", default_market="spot")
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    asset = app.add_asset(symbol="BTC", asset_type=AssetType.CRYPTO, effective_from=at)

    assert str(asset.asset_id) == "asset:crypto:btc"
    assert app.catalog().maybe_get_asset(asset.asset_id, at) == asset
    assert app.resolver(as_of=at).snapshot() == {}


def test_reference_application_owns_versioned_financial_product_definitions() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    app = ReferenceApplication(MemoryReferenceStore())
    product = FinancialProductDefinition(
        FinancialProductId("financial_product:binance:earn:p-1"),
        FinancialProductType.SIMPLE_EARN_FLEXIBLE,
        "USDT Flexible Earn",
        AssetId("asset:crypto:usdt"),
        "P-1",
        provider_id=ProviderId("binance"),
        apr=Decimal("0.05"),
        status=FinancialProductStatus.AVAILABLE,
        effective_from=at,
    )
    app.add_financial_product(product)
    assert app.financial_products(at=at) == (product,)
    assert app.catalog().snapshot(at=at)["financial_products"][str(product.product_id)] == product


def test_reference_bootstrap_merges_financial_product_snapshot() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    product = FinancialProductDefinition(
        FinancialProductId("financial_product:binance:earn:p-locked"),
        FinancialProductType.SIMPLE_EARN_LOCKED,
        "USDT Locked Earn",
        AssetId("asset:crypto:usdt"),
        "P-LOCKED",
        provider_id=ProviderId("binance"),
        lock_period_days=30,
        effective_from=at,
    )
    catalog = ReferenceCatalog(financial_products=(product,))
    app = ReferenceApplication(MemoryReferenceStore(), source=SnapshotSource(catalog))
    app.ensure_ready()
    assert app.catalog().get_financial_product(product.product_id, at) == product


def test_reference_bootstrap_refreshes_configured_underlyings_into_one_catalog() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    catalogs = {
        "BTC": catalog_from_market_rows(
            [{"market": "option", "venue": "massive", "source_symbol": "BTC-1", "base": "BTC", "quote": "USD", "underlying_instrument_id": "instrument:crypto:btc", "status": "active"}],
            effective_from=at,
        ),
        "ETH": catalog_from_market_rows(
            [{"market": "option", "venue": "massive", "source_symbol": "ETH-1", "base": "ETH", "quote": "USD", "underlying_instrument_id": "instrument:crypto:eth", "status": "active"}],
            effective_from=at,
        ),
    }
    source = ScopedSnapshotSource(catalogs)
    app = ReferenceApplication(
        MemoryReferenceStore(),
        default_venue="massive",
        default_market="option",
        underlyings=("BTC", "ETH", "BTC"),
        source=source,
    )

    result = app.bootstrap(as_of=at)

    assert result is not None
    assert [request.underlying for request in source.requests] == ["BTC", "ETH"]
    assert {str(item.source_symbol) for item in app.catalog().list_markets(at=at, venue="massive", market="option", active_only=True)} == {"BTC-1", "ETH-1"}


def test_reference_events_bootstrap_persist_and_emit_catalog_changes() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    catalog = catalog_from_market_rows(
        [{"market": "spot", "venue": "binance", "source_symbol": "BTC/USDT", "base": "BTC", "quote": "USDT"}],
        effective_from=at,
    )
    store = MemoryReferenceStore()
    app = ReferenceApplication(
        store,
        default_venue="binance",
        default_market="spot",
        source=SnapshotSource(catalog),
    )

    bus = InMemoryMessageBus()
    subscription = bus.open_inbox()

    async def consume_one():
        actor = ReferenceActor(app, bus, poll_interval_seconds=60)
        await actor.start()
        event = await subscription.receive()
        await actor.stop()
        await bus.close()
        return event

    event = asyncio.run(consume_one())

    assert event.changed("reference", "catalog.changed")
    assert len(store.load_catalog().markets()) == 1


def test_reference_query_returns_subscription_ready_selection() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    catalog = catalog_from_market_rows(
        [
            {"market": "spot", "venue": "binance", "source_symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "status": "active"},
            {"market": "spot", "venue": "binance", "source_symbol": "ETH/USDT", "base": "ETH", "quote": "USDT", "status": "active"},
        ],
        effective_from=at,
    )
    store = MemoryReferenceStore()
    app = ReferenceApplication(store, source=SnapshotSource(catalog))

    selection = app.query(ReferenceQuery(venue="binance", market="spot", quote_asset_id="asset:crypto:usdt"))

    assert selection.market_keys == ("binance_spot_btc_usdt", "binance_spot_eth_usdt")
    assert tuple(str(item.source_symbol) for item in selection.markets) == ("BTC/USDT", "ETH/USDT")
    assert len(store.load_catalog().markets()) == 2


def test_partial_option_refresh_only_reconciles_the_requested_underlying() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    initial = catalog_from_market_rows(
        [
            {
                "market": "option",
                "venue": "binance",
                "source_symbol": "BTC-1",
                "base": "BTC",
                "quote": "USDT",
                "underlying_instrument_id": "instrument:crypto:btc",
                "expiry": "2026-02-01T00:00:00+00:00",
                "strike_price": "50000",
                "contract_type": "call",
                "status": "active",
            },
            {
                "market": "option",
                "venue": "binance",
                "source_symbol": "ETH-1",
                "base": "ETH",
                "quote": "USDT",
                "underlying_instrument_id": "instrument:crypto:eth",
                "expiry": "2026-02-01T00:00:00+00:00",
                "strike_price": "3000",
                "contract_type": "put",
                "status": "active",
            },
        ],
        effective_from=at,
    )
    btc_only = catalog_from_market_rows(
        [
            {
                "market": "option",
                "venue": "binance",
                "source_symbol": "BTC-2",
                "base": "BTC",
                "quote": "USDT",
                "underlying_instrument_id": "instrument:crypto:btc",
                "expiry": "2026-02-08T00:00:00+00:00",
                "strike_price": "52000",
                "contract_type": "call",
                "status": "active",
            },
        ],
        effective_from=later,
    )
    store = MemoryReferenceStore()
    store.save_catalog(initial)

    result = ReferenceRefreshService(store).refresh_snapshot(
        btc_only,
        as_of=later,
        venue="binance",
        market="option",
        underlying="btc",
    )

    assert {str(item.source_symbol) for item in result.current_markets} == {"BTC-2"}
    assert {str(item.source_symbol) for item in store.load_catalog().list_markets(at=later, venue="binance", market="option", active_only=True)} == {"BTC-2", "ETH-1"}
    assert any(str(item.source_symbol) == "BTC-1" for item in store.load_catalog().markets())


def test_missing_expired_option_is_recorded_as_expired_not_delisted() -> None:
    listed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expired_at = datetime(2026, 2, 2, tzinfo=timezone.utc)
    initial = catalog_from_market_rows(
        [{
            "market": "option",
            "venue": "binance",
            "source_symbol": "BTC-EXPIRING",
            "base": "BTC",
            "quote": "USDT",
            "underlying_instrument_id": "instrument:crypto:btc",
            "expiry": "2026-02-01T00:00:00+00:00",
            "strike_price": "50000",
            "contract_type": "call",
            "status": "active",
        }],
        effective_from=listed_at,
    )
    store = MemoryReferenceStore()
    store.save_catalog(initial)

    result = ReferenceRefreshService(store).refresh_snapshot(
        ReferenceCatalog(),
        as_of=expired_at,
        venue="binance",
        market="option",
        underlying="btc",
    )

    assert [event.event_type for event in result.events] == [LifecycleEventType.EXPIRED]
    market = store.load_catalog().get_market("market:binance:option:btc_expiring", expired_at)
    assert market.status.value == "expired"


def test_empty_reference_snapshot_does_not_delist_still_tradable_market() -> None:
    listed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    refreshed_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    initial = catalog_from_market_rows(
        [{
            "market": "option",
            "venue": "binance",
            "source_symbol": "BTC-LIVE",
            "base": "BTC",
            "quote": "USDT",
            "underlying_instrument_id": "instrument:crypto:btc",
            "expiry": "2026-02-01T00:00:00+00:00",
            "strike_price": "50000",
            "contract_type": "call",
            "status": "active",
        }],
        effective_from=listed_at,
    )
    store = MemoryReferenceStore()
    store.save_catalog(initial)

    with pytest.raises(ValueError, match="empty snapshot"):
        ReferenceRefreshService(store).refresh_snapshot(
            ReferenceCatalog(),
            as_of=refreshed_at,
            venue="binance",
            market="option",
            underlying="btc",
        )

    market = store.load_catalog().get_market("market:binance:option:btc_live", refreshed_at)
    assert market.status.value == "active"


def test_reference_actor_owns_refresh_lifecycle_and_publishes_business_events() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    catalog = catalog_from_market_rows(
        [{"market": "spot", "venue": "binance", "source_symbol": "BTC/USDT", "base": "BTC", "quote": "USDT"}],
        effective_from=at,
    )
    bus = InMemoryMessageBus()
    events = bus.open_inbox()
    reference = ReferenceApplication(MemoryReferenceStore(), source=SnapshotSource(catalog))

    async def scenario() -> None:
        actor = ReferenceActor(reference, bus, poll_interval_seconds=60)
        await actor.start()
        message = await asyncio.wait_for(events.receive(), timeout=1)
        assert message.topic == "reference.catalog.changed"
        assert message.changed("reference", "catalog.changed")
        await actor.stop()
        await bus.close()

    asyncio.run(scenario())
