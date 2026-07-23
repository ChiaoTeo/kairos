from __future__ import annotations

import asyncio
from decimal import Decimal
import unittest

from kairospy.identity import InstrumentId
from kairospy.integrations.connectors.ccxt import CcxtOrderBookEventSource, CcxtSymbolMapper
from kairospy.integrations.connectors.ccxt.exchange_factory import normalized_ccxt_exchange_id
from kairospy.integrations.connectors.ccxt.market_stream import DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS
from kairospy.integrations.connectors.ccxt.market_stream import parse_ccxt_order_book


INSTRUMENT = InstrumentId("crypto:kraken:spot:BTC-USD")


class FakeCcxtProExchange:
    def __init__(self, books):
        self.books = iter(books)
        self.calls = []
        self.closed = False

    async def watch_order_book(self, symbol, limit=None):
        self.calls.append((symbol, limit))
        await asyncio.sleep(0)
        return next(self.books)

    async def close(self):
        self.closed = True


class CcxtProStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_book_event_source_yields_canonical_snapshots_and_closes_exchange(self) -> None:
        exchange = FakeCcxtProExchange((
            {
                "symbol": "BTC/USD",
                "bids": [[50000, 1.25]],
                "asks": [[50001, 2.5]],
                "nonce": 17,
                "timestamp": 1752753600000,
            },
        ))
        source = CcxtOrderBookEventSource.for_instruments(
            exchange,
            provider="kraken",
            instrument_ids=(INSTRUMENT,),
            symbol_mapper=CcxtSymbolMapper({INSTRUMENT: "BTC/USD"}),
            depth=10,
        )

        events = source.events()
        snapshot = await events.__anext__()
        await events.aclose()

        self.assertEqual(exchange.calls, [("BTC/USD", 10)])
        self.assertTrue(exchange.closed)
        self.assertEqual(snapshot.instrument_id, INSTRUMENT)
        self.assertEqual(snapshot.sequence, 17)
        self.assertEqual(snapshot.asks[0].price, Decimal("50001"))
        self.assertEqual(snapshot.asks[0].quantity, Decimal("2.5"))
        self.assertEqual(snapshot.bids[0].price, Decimal("50000"))

    async def test_order_book_event_source_supports_ccxt_pro_new_updates_flag(self) -> None:
        exchange = FakeCcxtProExchange((
            {"bids": [[1, 2]], "asks": [[3, 4]]},
        ))
        CcxtOrderBookEventSource.for_instruments(
            exchange,
            provider="kraken",
            instrument_ids=(INSTRUMENT,),
            symbol_mapper=CcxtSymbolMapper({INSTRUMENT: "BTC/USD"}),
            new_updates=False,
            close_on_exit=False,
        )

        self.assertFalse(exchange.newUpdates)

    def test_parse_order_book_uses_zero_sequence_when_exchange_omits_nonce(self) -> None:
        snapshot = parse_ccxt_order_book({"bids": [["1", "2"]], "asks": [["3", "4"]]}, INSTRUMENT)

        self.assertEqual(snapshot.sequence, 0)
        self.assertEqual(snapshot.bids[0].quantity, Decimal("2"))

    def test_default_live_smoke_symbols_cover_requested_exchanges(self) -> None:
        self.assertEqual(normalized_ccxt_exchange_id("okex"), "okx")
        self.assertEqual(DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS["binance"], "BTC/USDT")
        self.assertEqual(DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS["okx"], "BTC/USDT")
        self.assertEqual(DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS["hyperliquid"], "BTC/USDC:USDC")


if __name__ == "__main__":
    unittest.main()
