from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from trading.adapters.binance import BinanceSigner
from trading.adapters.transfer import BinanceTransferAdapter, BinanceWalletRoute
from trading.domain.identity import AssetId
from trading.reference import NetworkAssetDefinition, NetworkDefinition, NetworkType, ReferenceCatalog
from trading.reference.identity import LocationId, NetworkAssetId, NetworkId
from trading.treasury import CryptoTransferInstruction, FeePolicy, InternalTransferInstruction, TransferStatus


NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)


class Transport:
    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, headers=None):
        self.calls.append((method, path, params, headers))
        if path == "/sapi/v1/asset/transfer" and method == "POST":
            return {"tranId": 123}
        if path == "/sapi/v1/capital/withdraw/apply":
            return {"id": "withdraw-1"}
        if path == "/sapi/v1/capital/withdraw/history":
            return [{"id": "withdraw-1", "status": 6, "amount": "10", "transactionFee": "1", "coin": "USDT", "txId": "0xtx"}]
        return {"rows": [{"status": "SUCCESS"}]}


class BinanceTransferAdapterTests(unittest.TestCase):
    def setUp(self):
        self.source = LocationId("binance:spot")
        self.destination = LocationId("binance:usdm")
        self.catalog = ReferenceCatalog()
        network = NetworkDefinition(NetworkId("ETH"), NetworkType.BLOCKCHAIN, "Ethereum", datetime(2010, 1, 1, tzinfo=timezone.utc), native_asset=AssetId("ETH"))
        self.catalog.networks.add(network)
        self.catalog.network_assets.add(NetworkAssetDefinition(NetworkAssetId("ETH:USDT"), AssetId("USDT"), network.network_id, 6, datetime(2010, 1, 1, tzinfo=timezone.utc)))
        self.transport = Transport()
        self.adapter = BinanceTransferAdapter(self.transport, BinanceSigner("key", "secret"), self.catalog, (BinanceWalletRoute(self.source, self.destination, "MAIN_UMFUTURE"),))

    def test_internal_wallet_transfer_is_idempotently_identified(self):
        instruction = InternalTransferInstruction("instruction", uuid4(), self.source, self.destination, AssetId("USDT"), Decimal("100"), "idem")
        result = self.adapter.submit(instruction)
        self.assertEqual(result.provider_reference, "binance:internal:123")
        self.assertEqual(self.transport.calls[0][2]["clientTranId"], "idem")

    def test_withdrawals_are_separately_disabled_and_status_is_normalized(self):
        instruction = CryptoTransferInstruction("instruction", uuid4(), self.source, NetworkAssetId("ETH:USDT"), "0xabc", Decimal("10"), FeePolicy.ADD_TO_AMOUNT, "idem")
        with self.assertRaises(PermissionError):
            self.adapter.submit(instruction)
        enabled = BinanceTransferAdapter(self.transport, BinanceSigner("key", "secret"), self.catalog, enable_withdrawals=True)
        submitted = enabled.submit(instruction)
        self.assertEqual(submitted.provider_reference, "binance:withdrawal:withdraw-1")
        status = enabled.status(submitted.provider_reference)
        self.assertEqual(status.status, TransferStatus.CONFIRMED)
        self.assertEqual(status.transaction_hash, "0xtx")
        self.assertEqual(status.fee_amount, Decimal("1"))


if __name__ == "__main__":
    unittest.main()
