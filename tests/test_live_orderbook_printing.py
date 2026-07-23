from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from contextlib import redirect_stdout
import unittest

from kairospy.identity import InstrumentId
from kairospy.market.canonical import canonical_from_trading_market_data
from kairospy.market.types import OrderBookLevel, OrderBookSnapshot
from kairospy.runtime.kernel import CanonicalMarketProjection
from kairospy.strategy.protocols import Context
from kairospy.strategy.views import MarketView, PortfolioView
from examples.strategies.printer import build


class LiveOrderBookPrintingTests(unittest.TestCase):
    def test_canonical_market_projection_exposes_order_book_top_of_book(self) -> None:
        at = datetime(2026, 7, 23, 1, 2, 3, tzinfo=timezone.utc)
        instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
        snapshot = OrderBookSnapshot(
            instrument,
            (OrderBookLevel(Decimal("65000.00"), Decimal("0.5")),),
            (OrderBookLevel(Decimal("65000.50"), Decimal("0.4")),),
            123,
            at,
        )
        event = canonical_from_trading_market_data(
            snapshot,
            source="binance",
            source_instance="kairospy-runtime:binance_spot_book",
            stream_id="btcusdt@depth5",
            receive_time=at,
            published_time=at,
        )[0]

        market = CanonicalMarketProjection().apply(event)

        self.assertIsNotNone(market)
        assert market is not None
        view = MarketView.from_snapshot(market)
        self.assertEqual(view.top_of_book[0].instrument_id, instrument)
        self.assertEqual(view.top_of_book[0].bid, Decimal("65000.00"))
        self.assertEqual(view.top_of_book[0].ask, Decimal("65000.50"))
        self.assertEqual(view.top_of_book[0].spread, Decimal("0.50"))

    def test_printer_strategy_prints_order_book_prices(self) -> None:
        at = datetime(2026, 7, 23, 1, 2, 3, tzinfo=timezone.utc)
        instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
        context = Context(
            MarketView(
                at,
                7,
                (instrument,),
                data_binding="kairospy-runtime:binance_spot_book",
                top_of_book=((
                    instrument,
                    Decimal("65000.00"),
                    Decimal("0.5"),
                    Decimal("65000.50"),
                    Decimal("0.4"),
                ),),
            ),
            PortfolioView(),
        )

        output = StringIO()
        with redirect_stdout(output):
            build().on_market(context)

        rendered = output.getvalue()
        self.assertIn("[book 007]", rendered)
        self.assertIn("bid=65000.00", rendered)
        self.assertIn("ask=65000.50", rendered)
        self.assertIn("spread=0.50", rendered)


if __name__ == "__main__":
    unittest.main()
