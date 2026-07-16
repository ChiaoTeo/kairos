from __future__ import annotations

import os
import unittest

from trading.adapters.base import Environment
from trading.adapters.base import ReferenceDataRequest
from trading.adapters.binance.adapter import (
    BinanceAccountAdapter, BinanceSigner, BinanceSpotReferenceAdapter, RateLimiter,
    UrllibBinanceTransport, synchronize_clock,
)
from trading.domain.identity import AccountKey, AccountType, VenueId
from trading.domain.product import ProductType


@unittest.skipUnless(
    os.getenv("RUN_BINANCE_TESTNET") == "1" and os.getenv("BINANCE_TESTNET_API_KEY") and os.getenv("BINANCE_TESTNET_API_SECRET"),
    "set RUN_BINANCE_TESTNET=1 and testnet-only Binance credentials",
)
class BinanceTestnetContractTests(unittest.TestCase):
    def test_public_catalog_clock_and_readonly_account_contracts(self) -> None:
        transport = UrllibBinanceTransport("https://testnet.binance.vision")
        limiter = RateLimiter(10, 1)
        definitions = BinanceSpotReferenceAdapter(transport, limiter).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",)))
        self.assertEqual(definitions[0].listing(VenueId("binance")).symbol, "BTCUSDT")
        signer = BinanceSigner(os.environ["BINANCE_TESTNET_API_KEY"], os.environ["BINANCE_TESTNET_API_SECRET"])
        synchronize_clock(transport, signer, limiter)
        account = AccountKey(VenueId("binance"), os.getenv("BINANCE_TESTNET_ACCOUNT", "testnet"), AccountType.CRYPTO_SPOT)
        state = BinanceAccountAdapter(transport, signer, Environment.TESTNET, limiter=limiter).account_state(account)
        self.assertEqual(state.account, account)


if __name__ == "__main__":
    unittest.main()
