from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from trading.domain.identity import AssetId, InstrumentId
from trading.domain.product import (
    ContractType,
    CryptoOptionSpec,
    ExerciseStyle,
    FutureSpec,
    ListedOptionSpec,
    OptionRight,
    OptionSpec,
    SettlementSession,
    SettlementType,
    is_option_spec,
    option_multiplier,
)


class OptionSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expiry = datetime(2099, 1, 1, tzinfo=timezone.utc)

    def test_listed_and_crypto_options_share_the_option_contract(self) -> None:
        listed = ListedOptionSpec(
            InstrumentId("equity:us:AAPL"), self.expiry, Decimal("200"), OptionRight.CALL,
            ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM,
            Decimal("100"), self.expiry,
        )
        crypto = CryptoOptionSpec(
            AssetId("BTC"), AssetId("USD"), AssetId("USDC"), AssetId("BTC"),
            self.expiry, Decimal("100000"), OptionRight.PUT, ExerciseStyle.EUROPEAN,
            Decimal("0.1"), "btc_usd",
        )

        self.assertIsInstance(listed, OptionSpec)
        self.assertIsInstance(crypto, OptionSpec)
        self.assertTrue(is_option_spec(listed))
        self.assertTrue(is_option_spec(crypto))
        self.assertEqual(option_multiplier(listed), Decimal("100"))
        self.assertEqual(option_multiplier(crypto), Decimal("0.1"))

    def test_non_option_derivative_does_not_match_option_contract(self) -> None:
        future = FutureSpec(
            AssetId("BTC"), AssetId("USD"), self.expiry, Decimal("1"),
            ContractType.LINEAR, "btc_usd",
        )

        self.assertFalse(is_option_spec(future))


if __name__ == "__main__":
    unittest.main()
