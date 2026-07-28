from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.service.domain.reference import ReferenceStore, catalog_from_market_rows
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
    assert store.load_catalog().list_markets(at=as_of)[0].source_symbol == "BTC/USDT"
    assert store.load_events()[0].event_type is LifecycleEventType.LISTED
