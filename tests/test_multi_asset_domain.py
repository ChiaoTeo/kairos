from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timezone
from decimal import Decimal

from trading.domain.capability import ExecutionCapabilities, MarketDataCapabilities, MarketDataKind, OrderType, ReferenceCapabilities
from trading.domain.identity import Amount, AssetId, InstrumentId, VenueId
from trading.domain.product import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.reference import ReferenceCatalog
from trading.reference.repository import ReferenceCatalogRepository
from tests.reference_support import publish_test_instrument


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
        catalog = ReferenceCatalog()
        definition = publish_test_instrument(
            catalog, InstrumentId("option:spxw:20260716:6300:c"), ProductType.LISTED_OPTION, "SPXW",
            ListedOptionSpec(
                InstrumentId("index:spx"), expiry, Decimal("6300"), OptionRight.CALL,
                ExerciseStyle.EUROPEAN, SettlementType.CASH, SettlementSession.PM,
                Decimal("100"), expiry,
            ),
            AssetId("USD"), VenueId("ibkr"), "SPXW  260716C06300000", datetime(2025, 1, 1, tzinfo=timezone.utc),
            price_increment=Decimal("0.05"),
        )
        self.assertEqual(definition.instrument_type, ProductType.LISTED_OPTION)
        self.assertIsInstance(definition.contract_spec, ListedOptionSpec)
        self.assertEqual(definition.contract_spec.exercise_style, ExerciseStyle.EUROPEAN)
        self.assertEqual(definition.contract_spec.settlement_type, SettlementType.CASH)
        self.assertEqual(catalog.active_listings(definition.instrument_id, expiry)[0].trading_symbol, "SPXW  260716C06300000")

    def test_catalog_is_time_versioned_and_round_trips(self) -> None:
        catalog = ReferenceCatalog()
        first = publish_test_instrument(
            catalog, InstrumentId("index:spx"), ProductType.INDEX, "SPX", IndexSpec(AssetId("USD")),
            AssetId("USD"), VenueId("ibkr"), "SPX", datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(catalog.instruments.get(first.instrument_id, at), first)
        self.assertEqual(catalog.active_listings(first.instrument_id, at)[0].trading_symbol, "SPX")
        catalog.instruments.add(first)
        self.assertEqual(catalog.instruments.values(), (first,))
        with tempfile.TemporaryDirectory() as directory:
            repository = ReferenceCatalogRepository(f"{directory}/catalog.json")
            repository.save(catalog)
            loaded = repository.load()
            self.assertEqual(loaded.instruments.values(), catalog.instruments.values())


if __name__ == "__main__":
    unittest.main()
