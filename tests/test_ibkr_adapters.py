from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from trading.adapters.base import ComboLegRequest, ComboOrderRequest, Environment, OrderRequest, ReferenceDataRequest
from trading.adapters.ibkr.adapter import IbkrAccountAdapter, IbkrExecutionAdapter, IbkrMarketDataAdapter, IbkrReferenceAdapter
from trading.domain.capability import OrderType
from trading.domain.execution import TradeSide
from trading.domain.identity import AccountKey, AccountType, AssetId, VenueId
from trading.domain.order import ExecutionInstructions, TimeInForce
from trading.domain.product import ProductType


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


class FakeIb:
    def __init__(self):
        self.next_id = 100
        self.trades = []
        self.cancelled = []

    def qualifyContracts(self, *contracts):
        for contract in contracts:
            self.next_id += 1
            contract.conId = self.next_id
            contract.localSymbol = contract.localSymbol or f"{contract.symbol}-{self.next_id}"
            contract.primaryExchange = getattr(contract, "primaryExchange", "") or "NASDAQ"
            contract.tradingClass = getattr(contract, "tradingClass", "") or contract.symbol
            contract.multiplier = getattr(contract, "multiplier", "") or ("100" if contract.secType == "OPT" else "")
        return list(contracts)

    def reqTickers(self, *contracts):
        return [SimpleNamespace(
            time=NOW, bid=Decimal("99"), ask=Decimal("101"),
            bidSize=Decimal("10"), askSize=Decimal("12"),
            last=Decimal("100.50"), lastSize=Decimal("3"),
        ) for _ in contracts]
    def reqMarketDataType(self, market_data_type): self.market_data_type = market_data_type

    def reqHistoricalData(self, contract, **kwargs):
        self.historical_request = (contract, kwargs)
        return [SimpleNamespace(
            date=NOW, open=Decimal("100"), high=Decimal("102"),
            low=Decimal("99"), close=Decimal("101"), volume=Decimal("1000"),
        )]

    def placeOrder(self, contract, order):
        order.orderId = 900 + len(self.trades)
        trade = SimpleNamespace(contract=contract, order=order)
        self.trades.append(trade)
        return trade

    def openTrades(self): return list(self.trades)
    def cancelOrder(self, order): self.cancelled.append(order.orderId)
    def accountSummary(self, account_id): return [SimpleNamespace(tag="TotalCashValue", currency="USD", value="10000")]
    def positions(self, account_id):
        return [SimpleNamespace(contract=self.trades[0].contract, position=Decimal("10"))] if self.trades else []


class FakeSession:
    def __init__(self):
        self.ib, self.contracts, self.readonly = FakeIb(), {}, False
        self.connected = False
    def connect(self): self.connected = True
    def disconnect(self): self.connected = False


class IbkrAdapterTests(unittest.TestCase):
    def test_stock_reference_market_execution_and_account_are_not_option_specific(self) -> None:
        session = FakeSession()
        reference = IbkrReferenceAdapter(session)
        stock = reference.sync(ReferenceDataRequest(ProductType.EQUITY, ("AAPL",)))[0]
        self.assertEqual(stock.product_type, ProductType.EQUITY)
        self.assertEqual(stock.symbol, "AAPL")
        market = IbkrMarketDataAdapter(session)
        quote = market.snapshot((stock,))[0]
        self.assertEqual((quote.bid, quote.ask), (Decimal("99"), Decimal("101")))
        trade = market.recent_trades((stock,))[0]
        self.assertEqual((trade.price, trade.quantity), (Decimal("100.50"), Decimal("3")))
        bar = market.historical_bars(
            stock, end=NOW + timedelta(days=1), duration="1 D", bar_size="1 min",
        )[0]
        self.assertEqual((bar.open, bar.close, bar.end - bar.start), (Decimal("100"), Decimal("101"), timedelta(minutes=1)))
        account = AccountKey(VenueId("ibkr"), "DU123", AccountType.SECURITIES_MARGIN)
        request = OrderRequest(
            "internal", "client", "stock-strategy", "intent", "correlation", account,
            stock.instrument_id, TradeSide.BUY, Decimal("10"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("100")),
        )
        execution = IbkrExecutionAdapter(session, Environment.PAPER)
        self.assertNotIn(ProductType.INDEX, execution.capabilities.product_types)
        self.assertIn(ProductType.INDEX, market.capabilities.product_types)
        ack = execution.place_order(request)
        self.assertEqual(ack.intent_id, "intent")
        state = IbkrAccountAdapter(session, Environment.PAPER).account_state(account)
        self.assertEqual(state.balances[0].total, Decimal("10000"))
        self.assertEqual(state.positions, ((stock.instrument_id, Decimal("10")),))
        execution.cancel_order(account, ack.venue_order_id)
        self.assertEqual(session.ib.cancelled, [900])

    def test_listed_option_reference_and_native_combo_are_separate_typed_paths(self) -> None:
        session = FakeSession()
        reference = IbkrReferenceAdapter(session)
        definitions = reference.sync(ReferenceDataRequest(
            ProductType.LISTED_OPTION,
            ("AAPL:20260821:200:C", "AAPL:20260821:210:C"),
        ))
        self.assertTrue(all(item.product_type is ProductType.LISTED_OPTION for item in definitions))
        account = AccountKey(VenueId("ibkr"), "DU123", AccountType.SECURITIES_MARGIN)
        request = ComboOrderRequest(
            "combo-internal", "combo-client", "covered-call", "combo-intent", "combo-correlation",
            account,
            (ComboLegRequest(definitions[0].instrument_id, TradeSide.BUY, 1), ComboLegRequest(definitions[1].instrument_id, TradeSide.SELL, 1)),
            Decimal("1"), ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("1.25")),
        )
        ack = IbkrExecutionAdapter(session, Environment.PAPER).place_combo_order(request)
        self.assertEqual(ack.intent_id, "combo-intent")
        self.assertEqual(session.ib.trades[-1].contract.secType, "BAG")
        self.assertEqual(len(session.ib.trades[-1].contract.comboLegs), 2)


if __name__ == "__main__":
    unittest.main()
