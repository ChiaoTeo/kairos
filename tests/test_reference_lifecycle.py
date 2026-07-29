from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.service.domain.reference import ReferenceLifecycleService, catalog_from_market_rows
from kairospy.core.reference import LifecycleEventType


def test_reference_lifecycle_changes_market_symbol_through_market_identity() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    changed_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    catalog = catalog_from_market_rows(
        (
            {
                "venue": "nasdaq",
                "market": "equity",
                "source_symbol": "OLD",
                "base": "OLD",
                "quote": "USD",
                "status": "trading",
            },
        ),
        effective_from=as_of,
    )
    market = catalog.list_markets(at=as_of)[0]

    event = ReferenceLifecycleService(catalog).change_symbol(
        market_id=market.market_id,
        new_symbol="NEW",
        effective_at=changed_at,
    )

    changed_market = catalog.get_market(market.market_id, changed_at)
    changed_listing = catalog.get_listing(market.listing_id, changed_at)
    assert event.event_type is LifecycleEventType.SYMBOL_CHANGED
    assert event.listing_id == market.listing_id
    assert event.market_id == market.market_id
    assert str(changed_market.source_symbol) == "NEW"
    assert str(changed_listing.trading_symbol) == "NEW"


def test_reference_lifecycle_records_instrument_actions() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    catalog = catalog_from_market_rows(
        (
            {
                "venue": "nasdaq",
                "market": "equity",
                "source_symbol": "AAPL",
                "base": "AAPL",
                "quote": "USD",
                "status": "trading",
            },
        ),
        effective_from=as_of,
    )
    instrument_id = catalog.list_markets(at=as_of)[0].instrument_id
    service = ReferenceLifecycleService(catalog)

    split = service.record_split(instrument_id=instrument_id, effective_at=as_of, ratio="4")
    dividend = service.record_dividend(
        instrument_id=instrument_id,
        ex_date=as_of,
        pay_date=as_of,
        amount_per_share="0.25",
    )

    assert split.event_type is LifecycleEventType.SPLIT
    assert split.instrument_id == instrument_id
    assert split.market_id is None
    assert dividend.event_type is LifecycleEventType.DIVIDEND
    assert dividend.instrument_id == instrument_id
    assert dividend.market_id is None
