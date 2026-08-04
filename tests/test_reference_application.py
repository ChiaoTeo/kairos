from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairospy.application.usecases.reference.application.component import ReferenceApplication
from kairospy.application.usecases.reference.services.builders import catalog_from_market_rows
from kairospy.application.usecases.reference.application.query import ReferenceQuery
from kairospy.application.actor.market.application import ReferenceActor
from kairospy.domain.reference import AssetType, LifecycleEvent, ReferenceCatalog
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

    def catalog(self, *, as_of, market=None, params=None) -> ReferenceCatalog:
        return self.catalog_value


def test_reference_application_owns_catalog_and_resolution_capabilities() -> None:
    app = ReferenceApplication(MemoryReferenceStore(), default_venue="binance", default_market="spot")
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    asset = app.add_asset(symbol="BTC", asset_type=AssetType.CRYPTO, effective_from=at)

    assert str(asset.asset_id) == "asset:crypto:btc"
    assert app.catalog().maybe_get_asset(asset.asset_id, at) == asset
    assert app.resolver(as_of=at).snapshot() == {}


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
