from __future__ import annotations

import asyncio
import unittest

from kairospy.data import LiveDataRequest
from kairospy.integrations.data_products import KNOWN_PRODUCTS
from kairospy.integrations.data_products.catalog import BUILT_IN_HYPERLIQUID_PRODUCTS, BuiltInLiveDataProtocol
from kairospy.integrations.data_products import (
    binance,
    hyperliquid,
    integration_live_products,
    integration_product_specs,
)
from kairospy.integrations.data_products.live_runtime import provider_live_runtime_config
from kairospy.integrations.data_products.live_stream import provider_live_stream


class IntegrationDataProductsCatalogTests(unittest.TestCase):
    def test_integration_catalog_facade_preserves_existing_product_contracts(self) -> None:
        self.assertEqual(integration_product_specs(), KNOWN_PRODUCTS)
        self.assertIn(binance.BTC_SPOT_DAILY, integration_product_specs())

    def test_integration_catalog_facade_exposes_live_provider_products(self) -> None:
        products = integration_live_products()
        self.assertTrue(any(item.key == "binance.orderbook" for item in products))
        self.assertTrue(any(item.key == "hyperliquid.perpetual.orderbook" for item in products))
        self.assertEqual(hyperliquid.LIVE_PRODUCTS, BUILT_IN_HYPERLIQUID_PRODUCTS)

    def test_integration_live_runtime_matches_legacy_data_protocol_runtime(self) -> None:
        products = {item.key: item for item in integration_live_products()}
        cases = (
            ("binance.orderbook", LiveDataRequest(
                "market.orderbook.crypto.binance.spot.btc-usdt",
                instruments=("BTCUSDT",),
                channel="depth",
                params={"levels": 5},
            )),
            ("massive.quote", LiveDataRequest(
                "market.quote.us_equity.massive.aapl",
                instruments=("AAPL",),
            )),
            ("hyperliquid.perpetual.orderbook", LiveDataRequest(
                "market.orderbook.crypto.hyperliquid.perpetual.btc",
                instruments=("BTC",),
            )),
        )
        for key, request in cases:
            with self.subTest(key=key):
                product = products[key]
                self.assertEqual(
                    dict(provider_live_runtime_config(product, request) or {}),
                    dict(BuiltInLiveDataProtocol(product).runtime_config(request)),
                )

    def test_integration_live_stream_reads_massive_injected_messages(self) -> None:
        products = {item.key: item for item in integration_live_products()}
        request = LiveDataRequest(
            "market.quote.us_equity.massive.aapl",
            instruments=("AAPL",),
            params={"message_source": [{"ev": "Q", "sym": "AAPL", "bp": 181.2, "ap": 181.3, "t": 1_700_000_000_000}]},
        )
        config = dict(provider_live_runtime_config(products["massive.quote"], request) or {})

        rows = asyncio.run(_collect(provider_live_stream(request, config), limit=1))

        self.assertEqual(rows[0]["kind"], "quote")
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(rows[0]["bid"], 181.2)
        self.assertEqual(rows[0]["source"], "massive")

    def test_integration_live_stream_reads_hyperliquid_injected_messages(self) -> None:
        products = {item.key: item for item in integration_live_products()}
        request = LiveDataRequest(
            "market.orderbook.crypto.hyperliquid.perpetual.btc",
            instruments=("BTC",),
            params={"message_source": [{
                "channel": "l2Book",
                "data": {"coin": "BTC", "time": 1_700_000_000_000, "levels": [[{"px": "65000", "sz": "1"}], []]},
            }]},
        )
        config = dict(provider_live_runtime_config(products["hyperliquid.perpetual.orderbook"], request) or {})

        rows = asyncio.run(_collect(provider_live_stream(request, config), limit=1))

        self.assertEqual(rows[0]["kind"], "orderbook")
        self.assertEqual(rows[0]["coin"], "BTC")
        self.assertEqual(rows[0]["source"], "hyperliquid")


async def _collect(source, *, limit: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    async for row in source:
        rows.append(dict(row))
        if len(rows) >= limit:
            break
    return rows


if __name__ == "__main__":
    unittest.main()
