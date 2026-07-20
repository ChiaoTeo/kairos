from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.product import (
    EquitySpec, ExerciseStyle, ListedOptionSpec, OptionRight, ProductType,
    SettlementSession, SettlementType,
)
from kairos.pricing import PricingContextResolver
from kairos.reference import ReferenceCatalog
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class PricingReferenceTests(unittest.TestCase):
    def test_context_uses_reference_graph_instead_of_symbol_parsing(self) -> None:
        underlying = InstrumentId("equity:us:AAPL")
        option = InstrumentId("option:us:AAPL260817C00200000")
        catalog = ReferenceCatalog()
        publish_test_instrument(catalog, underlying, ProductType.EQUITY, "AAPL", EquitySpec("XNAS", "US", AssetId("USD")), AssetId("USD"), VenueId("xnas"), "AAPL", NOW)
        spec = ListedOptionSpec(underlying, NOW + timedelta(days=31), Decimal("200"), OptionRight.CALL, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), NOW + timedelta(days=31))
        publish_test_instrument(catalog, option, ProductType.LISTED_OPTION, "opaque-provider-id", spec, AssetId("USD"), VenueId("xnas"), "opaque-provider-id", NOW)
        context = PricingContextResolver(catalog).resolve(option, NOW, {underlying: Decimal("210")})
        self.assertEqual(context.underlying_instrument_id, underlying)
        self.assertEqual(context.underlying_value, Decimal("210"))


if __name__ == "__main__":
    unittest.main()
