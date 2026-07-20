from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.product import (
    EquitySpec, ExerciseStyle, ListedOptionSpec, OptionRight, ProductType,
    SettlementSession, SettlementType,
)
from kairos.lifecycle import SettlementResolver
from kairos.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(days=30)


class SettlementReferenceTests(unittest.TestCase):
    def _catalog(self, *, right=OptionRight.CALL, settlement_type=SettlementType.PHYSICAL):
        catalog = ReferenceCatalog()
        underlying = InstrumentId("equity:us:AAPL")
        option = InstrumentId(f"option:us:test:{right.value}:{settlement_type.value}")
        publish_test_instrument(catalog, underlying, ProductType.EQUITY, "AAPL", EquitySpec("XNAS", "US", AssetId("USD")), AssetId("USD"), VenueId("xnas"), "AAPL", NOW)
        spec = ListedOptionSpec(underlying, EXPIRY, Decimal("200"), right, ExerciseStyle.AMERICAN, settlement_type, SettlementSession.PM, Decimal("100"), EXPIRY)
        publish_test_instrument(catalog, option, ProductType.LISTED_OPTION, "option", spec, AssetId("USD"), VenueId("xnas"), "option", NOW, EXPIRY + timedelta(seconds=1))
        return catalog, option

    def test_physical_call_generates_share_and_strike_cash_flows(self):
        catalog, option = self._catalog()
        result = SettlementResolver(catalog).resolve(option, Decimal("2"), Decimal("210"), EXPIRY)
        self.assertEqual([(item.asset_id.value, item.amount) for item in result.flows], [("AAPL", Decimal("200")), ("USD", Decimal("-40000"))])

    def test_physical_put_reverses_deliverable_direction(self):
        catalog, option = self._catalog(right=OptionRight.PUT)
        result = SettlementResolver(catalog).resolve(option, Decimal("1"), Decimal("190"), EXPIRY)
        self.assertEqual([(item.asset_id.value, item.amount) for item in result.flows], [("AAPL", Decimal("-100")), ("USD", Decimal("20000"))])

    def test_cash_option_generates_only_intrinsic_cash(self):
        catalog, option = self._catalog(settlement_type=SettlementType.CASH)
        result = SettlementResolver(catalog).resolve(option, Decimal("-1"), Decimal("210"), EXPIRY)
        self.assertEqual(result.flows[0].asset_id, AssetId("USD"))
        self.assertEqual(result.flows[0].amount, Decimal("-1000"))


if __name__ == "__main__":
    unittest.main()
