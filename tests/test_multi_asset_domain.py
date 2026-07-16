from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timezone
from decimal import Decimal

from trading.catalog.repository import CatalogRepository
from trading.catalog.service import InstrumentCatalog
from trading.domain.capability import ExecutionCapabilities, MarketDataCapabilities, MarketDataKind, OrderType, ReferenceCapabilities
from trading.domain.identity import Amount, AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.product import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType


class MultiAssetDomainTests(unittest.TestCase):
    def test_ids_normalize_and_capabilities_are_enforced(self) -> None:
        self.assertEqual(AssetId(" usd ").value, "USD")
        self.assertEqual(Amount(AssetId("USD"), Decimal("12.5")).quantity, Decimal("12.5"))
        self.assertEqual(VenueId("IBKR").value, "ibkr")
        market_data = MarketDataCapabilities(frozenset({MarketDataKind.QUOTE}), frozenset({ProductType.EQUITY}))
        market_data.require_market_data(MarketDataKind.QUOTE)
        execution = ExecutionCapabilities(frozenset({OrderType.LIMIT}), frozenset({ProductType.EQUITY}))
        with self.assertRaises(ValueError):
            execution.require_order_type(OrderType.MARKET)
        equity_only = ReferenceCapabilities(frozenset({ProductType.EQUITY}))
        equity_only.require_product(ProductType.EQUITY)
        with self.assertRaises(ValueError):
            equity_only.require_product(ProductType.LISTED_OPTION)
        self.assertFalse(hasattr(market_data, "order_types"))
        self.assertFalse(hasattr(execution, "market_data"))
        self.assertFalse(hasattr(equity_only, "order_types"))

    def test_spxw_option_is_explicit_european_cash_settlement(self) -> None:
        expiry = datetime(2026, 7, 16, 16, tzinfo=timezone.utc)
        definition = InstrumentDefinition(
            InstrumentId("option:spxw:20260716:6300:c"), ProductType.LISTED_OPTION, "SPXW",
            None, AssetId("USD"),
            ListedOptionSpec(
                InstrumentId("index:spx"), expiry, Decimal("6300"), OptionRight.CALL,
                ExerciseStyle.EUROPEAN, SettlementType.CASH, SettlementSession.PM,
                Decimal("100"), expiry,
            ),
            (VenueListing(VenueId("ibkr"), "42", "SPXW  260716C06300000", Decimal("0.05"), Decimal("1"), Decimal("1")),),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(definition.product_type, ProductType.LISTED_OPTION)
        self.assertIsInstance(definition.product_spec, ListedOptionSpec)
        self.assertEqual(definition.product_spec.exercise_style, ExerciseStyle.EUROPEAN)
        self.assertEqual(definition.product_spec.settlement_type, SettlementType.CASH)
        self.assertEqual(definition.listing(VenueId("ibkr")).external_id, "42")

    def test_catalog_is_time_versioned_and_round_trips(self) -> None:
        first = InstrumentDefinition(
            InstrumentId("index:spx"), ProductType.INDEX, "SPX", None, AssetId("USD"),
            IndexSpec(AssetId("USD")),
            (VenueListing(VenueId("ibkr"), "1", "SPX", Decimal("0.01"), Decimal("1"), Decimal("1")),),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        catalog = InstrumentCatalog()
        catalog.add(first)
        at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(catalog.get(first.instrument_id, at), first)
        self.assertEqual(catalog.resolve(VenueId("ibkr"), "1", at), first)
        with self.assertRaises(ValueError):
            catalog.add(first)
        with tempfile.TemporaryDirectory() as directory:
            repository = CatalogRepository(f"{directory}/catalog.json")
            repository.save(catalog)
            loaded = repository.load()
            self.assertEqual(loaded.definitions(), catalog.definitions())


if __name__ == "__main__":
    unittest.main()
