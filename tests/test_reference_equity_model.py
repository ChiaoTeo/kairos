from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.integrations import EquityReferenceSnapshotProvider, catalog_from_equity_rows, massive_corporate_action_events
from kairospy.core.reference import (
    CorporateActionService,
    LifecycleEventType,
    ReferenceRefreshService,
    ReferenceStore,
)


UTC = timezone.utc


class FakeEquityProvider:
    def __init__(self, rows) -> None:
        self.rows = tuple(rows)

    def fetch_markets(self, *, params=None):
        assert params == {"asset_class": "equity"}
        return self.rows


def test_equity_rows_use_stable_ids_across_ticker_changes(tmp_path) -> None:
    store = ReferenceStore(tmp_path / "reference")
    refresh = ReferenceRefreshService(store)
    first_time = datetime(2022, 1, 1, tzinfo=UTC)
    second_time = datetime(2023, 1, 1, tzinfo=UTC)

    first_snapshot = catalog_from_equity_rows(
        (
            {
                "venue": "nasdaq",
                "ticker": "FB",
                "name": "Meta Platforms",
                "instrument_id": "meta_platforms",
                "currency": "USD",
                "active": True,
            },
        ),
        effective_from=first_time,
    )
    refresh.refresh_snapshot(first_snapshot, as_of=first_time, venue="nasdaq", market="equity")

    second_snapshot = catalog_from_equity_rows(
        (
            {
                "venue": "nasdaq",
                "ticker": "META",
                "name": "Meta Platforms",
                "instrument_id": "meta_platforms",
                "currency": "USD",
                "active": True,
            },
        ),
        effective_from=second_time,
    )
    result = refresh.refresh_snapshot(second_snapshot, as_of=second_time, venue="nasdaq", market="equity")

    assert [event.event_type for event in result.events] == [LifecycleEventType.SYMBOL_CHANGED]
    catalog = store.load_catalog()
    old_market = catalog.resolve_market("FB", venue="nasdaq", market="equity", at=first_time)
    new_market = catalog.resolve_market("META", venue="nasdaq", market="equity", at=second_time)
    assert old_market.market_id == new_market.market_id
    assert old_market.instrument_id == new_market.instrument_id
    assert catalog.get_asset("asset:equity:meta_platforms", first_time).symbol == "FB"
    assert catalog.get_asset("asset:equity:meta_platforms", second_time).symbol == "META"


def test_equity_reference_snapshot_provider_and_corporate_action_events() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = EquityReferenceSnapshotProvider(
        FakeEquityProvider((
            {
                "venue": "nasdaq",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "cik": "320193",
                "currency": "USD",
                "active": True,
            },
        ))
    ).reference_snapshot(as_of=as_of, params={"asset_class": "equity"})

    market = snapshot.resolve_market("AAPL", venue="nasdaq", market="equity")
    assert str(market.instrument_id) == "instrument:equity:320193"

    actions = CorporateActionService(snapshot.catalog)
    split = actions.split(instrument_id=market.instrument_id, effective_at=as_of, ratio=Decimal("4"))
    dividend = actions.dividend(
        instrument_id=market.instrument_id,
        ex_date=as_of,
        pay_date=datetime(2026, 2, 1, tzinfo=UTC),
        amount_per_share=Decimal("0.25"),
        currency="USD",
    )

    assert split.event_type is LifecycleEventType.SPLIT
    assert split.current == {"ratio": "4"}
    assert dividend.event_type is LifecycleEventType.DIVIDEND
    assert dividend.current["amount_per_share"] == "0.25"


def test_massive_corporate_action_rows_map_to_unified_lifecycle_events(tmp_path) -> None:
    store = ReferenceStore(tmp_path / "reference")
    refresh = ReferenceRefreshService(store)
    first_time = datetime(2022, 1, 1, tzinfo=UTC)
    second_time = datetime(2023, 1, 1, tzinfo=UTC)
    first_snapshot = catalog_from_equity_rows(
        (
            {
                "venue": "nasdaq",
                "ticker": "FB",
                "name": "Meta Platforms",
                "instrument_id": "meta_platforms",
                "currency": "USD",
                "active": True,
            },
        ),
        effective_from=first_time,
    )
    refresh.refresh_snapshot(first_snapshot, as_of=first_time, venue="nasdaq", market="equity")
    second_snapshot = catalog_from_equity_rows(
        (
            {
                "venue": "nasdaq",
                "ticker": "META",
                "name": "Meta Platforms",
                "instrument_id": "meta_platforms",
                "currency": "USD",
                "active": True,
            },
        ),
        effective_from=second_time,
    )
    catalog = refresh.refresh_snapshot(second_snapshot, as_of=second_time, venue="nasdaq", market="equity").catalog

    events = massive_corporate_action_events(
        splits=({"ticker": "META", "execution_date": "2026-01-02", "split_from": 1, "split_to": 4, "id": "split-1"},),
        dividends=({"ticker": "META", "ex_dividend_date": "2026-02-02", "pay_date": "2026-02-09", "cash_amount": 0.26},),
        ticker_events=(
            {"date": "2012-05-18", "ticker_change": {"ticker": "FB"}, "type": "ticker_change"},
            {"date": "2023-01-01", "ticker_change": {"ticker": "META"}, "type": "ticker_change"},
        ),
        catalog=catalog,
        ticker="META",
        venue="nasdaq",
    )

    assert [event.event_type for event in events] == [
        LifecycleEventType.SYMBOL_CHANGED,
        LifecycleEventType.SPLIT,
        LifecycleEventType.DIVIDEND,
    ]
    symbol, split, dividend = events
    assert symbol.previous == {"symbol": "FB"}
    assert symbol.current["symbol"] == "META"
    assert split.current["ratio"] == "4"
    assert dividend.current["amount_per_share"] == "0.26"
