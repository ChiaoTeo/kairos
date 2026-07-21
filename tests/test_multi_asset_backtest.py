from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from kairospy.backtest.fill import (
    CryptoOptionFillModel, CryptoOrderBookFillModel, DeliveryFutureFillModel,
    EquityBarFillModel, EquityTopOfBookFillModel, PerpetualFillModel,
    SingleAssetOrder, StressWrapperFillModel,
)
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import InstrumentId
from kairospy.trading.market_data import (
    Bar, OrderBookLevel, OrderBookSnapshot, Quote, TradingState, TradingStatus,
)


NOW = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
ORDER_ID = UUID("00000000-0000-0000-0000-000000000001")


def order(instrument: str, side=TradeSide.BUY, quantity="10", *, eligible_at=NOW, limit=None):
    return SingleAssetOrder(
        ORDER_ID, InstrumentId(instrument), side, Decimal(quantity), eligible_at,
        Decimal(limit) if limit is not None else None,
    )


class MultiAssetFillModelTests(unittest.TestCase):
    def test_equity_top_of_book_partial_fill_halt_and_stress(self) -> None:
        quote = Quote(InstrumentId("equity:aapl"), Decimal("199.90"), Decimal("200.10"), Decimal("5"), Decimal("4"), NOW)
        model = EquityTopOfBookFillModel()
        partial = model.attempt(order("equity:aapl"), quote)
        self.assertEqual(partial.reason, "partially_filled")
        self.assertEqual(partial.fill.quantity, Decimal("4"))
        halted = model.attempt(order("equity:aapl"), quote, TradingStatus(InstrumentId("equity:aapl"), TradingState.HALTED, "news", NOW))
        self.assertEqual(halted.reason, "trading_halted")
        stressed = StressWrapperFillModel(model).attempt(order("equity:aapl", quantity="4"), quote)
        self.assertGreater(stressed.fill.price, partial.fill.price)
        self.assertGreater(stressed.fill.fee, partial.fill.fee)

    def test_equity_bar_never_uses_a_bar_that_started_before_order_eligibility(self) -> None:
        bar = Bar(InstrumentId("equity:aapl"), NOW, NOW + timedelta(minutes=1), Decimal("200"), Decimal("205"), Decimal("195"), Decimal("202"), Decimal("1000"))
        model = EquityBarFillModel()
        rejected = model.attempt(order("equity:aapl", eligible_at=NOW + timedelta(seconds=1)), bar)
        self.assertEqual(rejected.reason, "bar_started_before_order_eligible")
        filled = model.attempt(order("equity:aapl", eligible_at=NOW, limit="198"), bar)
        self.assertEqual(filled.fill.price, Decimal("198"))
        self.assertEqual(filled.fill.timestamp, bar.end)

    def test_crypto_order_book_walk_is_deterministic_and_stress_is_adverse(self) -> None:
        book = OrderBookSnapshot(
            InstrumentId("crypto:binance:spot:BTCUSDT"),
            (OrderBookLevel(Decimal("49990"), Decimal("0.5")),),
            (OrderBookLevel(Decimal("50000"), Decimal("0.1")), OrderBookLevel(Decimal("50010"), Decimal("0.2"))),
            42, NOW,
        )
        request = order("crypto:binance:spot:BTCUSDT", quantity="0.25")
        model = CryptoOrderBookFillModel()
        first = model.attempt(request, book)
        second = model.attempt(request, book)
        self.assertEqual(first, second)
        self.assertEqual(first.fill.quantity, Decimal("0.25"))
        self.assertEqual(first.fill.price, (Decimal("50000") * Decimal("0.1") + Decimal("50010") * Decimal("0.15")) / Decimal("0.25"))
        stressed = StressWrapperFillModel(model, adverse_bps=Decimal("20")).attempt(request, book)
        self.assertGreater(stressed.fill.price, first.fill.price)
        self.assertGreater(stressed.fill.slippage, 0)

    def test_perpetual_rejects_mark_index_divergence_and_supports_stress_sell(self) -> None:
        book = OrderBookSnapshot(
            InstrumentId("crypto:binance:perpetual:BTCUSDT"),
            (OrderBookLevel(Decimal("50000"), Decimal("2")),),
            (OrderBookLevel(Decimal("50010"), Decimal("2")),),
            7, NOW,
        )
        request = order("crypto:binance:perpetual:BTCUSDT", TradeSide.SELL, "1")
        model = PerpetualFillModel(maximum_mark_divergence=Decimal("0.02"))
        rejected = model.attempt(request, book, mark_price=Decimal("52000"), index_price=Decimal("50000"))
        self.assertEqual(rejected.reason, "mark_index_divergence")
        normal = model.attempt(request, book, mark_price=Decimal("50020"), index_price=Decimal("50000"))
        stressed = StressWrapperFillModel(model).attempt(request, book, mark_price=Decimal("50020"), index_price=Decimal("50000"))
        self.assertLess(stressed.fill.price, normal.fill.price)
        self.assertGreater(stressed.fill.fee, normal.fill.fee)

    def test_delivery_future_and_crypto_option_have_explicit_stress_models(self) -> None:
        for instrument, model in (
            ("crypto:binance:future:BTCUSDT_260925", DeliveryFutureFillModel()),
            ("crypto:binance:option:BTC-250628-60000-C", CryptoOptionFillModel()),
        ):
            with self.subTest(instrument=instrument):
                book = OrderBookSnapshot(
                    InstrumentId(instrument),
                    (OrderBookLevel(Decimal("99"), Decimal("10")),),
                    (OrderBookLevel(Decimal("101"), Decimal("10")),),
                    1, NOW,
                )
                request = order(instrument, quantity="2")
                conservative = model.attempt(request, book)
                stress = StressWrapperFillModel(model, adverse_bps=Decimal("25")).attempt(request, book)
                self.assertGreater(stress.fill.price, conservative.fill.price)
                self.assertGreater(stress.fill.fee, conservative.fill.fee)


if __name__ == "__main__":
    unittest.main()
