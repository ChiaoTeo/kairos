from __future__ import annotations

from kairos.domain.identity import InstitutionId

import os
import unittest

from kairos.ports import Environment
from kairos.ports import ReferenceDataRequest
from kairos.connectors.binance.account_gateway import BinanceAccountGateway
from kairos.connectors.binance.reference_data import BinanceSpotReferenceDataClient
from kairos.connectors.binance.request_signing import BinanceSigner, synchronize_clock
from kairos.connectors.binance.rest_transport import RateLimiter, UrllibBinanceTransport
from kairos.domain.identity import AccountKey, AccountType, VenueId
from kairos.domain.product import ProductType


@unittest.skipUnless(
    os.getenv("RUN_BINANCE_TESTNET") == "1" and os.getenv("BINANCE_TESTNET_API_KEY") and os.getenv("BINANCE_TESTNET_API_SECRET"),
    "set RUN_BINANCE_TESTNET=1 and testnet-only Binance credentials",
)
class BinanceTestnetContractTests(unittest.TestCase):
    def test_public_catalog_clock_and_readonly_account_contracts(self) -> None:
        transport = UrllibBinanceTransport("https://testnet.binance.vision")
        limiter = RateLimiter(10, 1)
        catalog = BinanceSpotReferenceDataClient(transport, limiter).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",)))
        definition = catalog.instruments.values()[0]
        self.assertEqual(catalog.active_listings(definition.instrument_id, definition.effective_from)[0].trading_symbol, "BTCUSDT")
        signer = BinanceSigner(os.environ["BINANCE_TESTNET_API_KEY"], os.environ["BINANCE_TESTNET_API_SECRET"])
        synchronize_clock(transport, signer, limiter)
        account = AccountKey(InstitutionId("binance"), os.getenv("BINANCE_TESTNET_ACCOUNT", "testnet"), AccountType.CRYPTO_SPOT)
        state = BinanceAccountGateway(transport, signer, Environment.TESTNET, limiter=limiter).account_state(account)
        self.assertEqual(state.account, account)


if __name__ == "__main__":
    unittest.main()
