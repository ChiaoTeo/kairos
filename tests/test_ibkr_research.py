from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from kairos.connectors.ibkr.research import IbkrSpxwResearchProvider
from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.product import (
    ExerciseStyle,
    ListedOptionSpec,
    OptionRight,
    ProductType,
    SettlementSession,
    SettlementType,
)
from kairos.research.spec import OptionChainCaptureSpec
from kairos.reference import ReferenceCatalog
from kairos.reference.contracts import InstrumentDefinition
from tests.reference_support import publish_test_instrument


def option_definition(catalog: ReferenceCatalog, strike: str) -> InstrumentDefinition:
    expiry = date(2026, 8, 4)
    expiry_at = datetime.combine(expiry, time(16), ZoneInfo("America/New_York"))
    instrument_id = InstrumentId(f"listed-option:spxw:{expiry.isoformat()}:{strike}:put")
    return publish_test_instrument(
        catalog, instrument_id, ProductType.LISTED_OPTION, "SPXW",
        ListedOptionSpec(
            InstrumentId("index:spx"),
            expiry_at,
            Decimal(strike),
            OptionRight.PUT,
            ExerciseStyle.EUROPEAN,
            SettlementType.CASH,
            SettlementSession.PM,
            Decimal("100"),
            expiry_at,
        ),
        AssetId("USD"), VenueId("ibkr"), instrument_id.value, datetime(1970, 1, 1, tzinfo=timezone.utc),
        price_increment=Decimal("0.05"),
    )


class PartialQualificationIb:
    def qualifyContracts(self, *contracts):
        qualified = contracts[1]
        qualified.conId = 12345
        qualified.localSymbol = "SPXW  260804P07300000"
        return [None, qualified]


class TransientTimeoutIb:
    def __init__(self):
        self.attempts = 0

    def qualifyContracts(self, contract):
        self.attempts += 1
        if self.attempts < 3:
            raise TimeoutError
        contract.conId = 416904
        contract.localSymbol = "SPX"
        return [contract]


class IbkrResearchProviderTests(unittest.TestCase):
    def test_underlying_retries_transient_contract_detail_timeouts(self) -> None:
        provider = object.__new__(IbkrSpxwResearchProvider)
        provider._ib = TransientTimeoutIb()
        provider._contracts = {}
        provider.catalog = ReferenceCatalog()

        with patch("kairos.connectors.ibkr.research.sleep"):
            result = provider.underlying(OptionChainCaptureSpec())

        self.assertEqual(result.instrument_id, InstrumentId("index:spx"))
        self.assertEqual(provider._ib.attempts, 3)

    def test_qualify_ignores_failed_contract_placeholders(self) -> None:
        provider = object.__new__(IbkrSpxwResearchProvider)
        provider._ib = PartialQualificationIb()
        provider._contracts = {}
        provider.catalog = ReferenceCatalog()
        missing, available = option_definition(provider.catalog, "7250"), option_definition(provider.catalog, "7300")

        result = provider.qualify((missing, available))

        self.assertEqual([item.instrument_id for item in result], [available.instrument_id])
        self.assertNotIn(missing.instrument_id, provider._contracts)
        self.assertEqual(provider._contracts[available.instrument_id].conId, 12345)


if __name__ == "__main__":
    unittest.main()
