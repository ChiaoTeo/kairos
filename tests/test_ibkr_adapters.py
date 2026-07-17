from __future__ import annotations

from trading.domain.identity import InstitutionId

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from trading.adapters.base import (
    ComboLegRequest, ComboOrderRequest, Environment, OrderRequest, ReferenceDataRequest, VenueOrderStatus,
)
from trading.adapters.ibkr.adapter import IbkrAccountAdapter, IbkrExecutionAdapter, IbkrMarketDataAdapter, IbkrReferenceAdapter
from trading.adapters.ibkr.ingestion import IbkrDurableFillIngestion
from trading.domain.capability import OrderType
from trading.domain.execution import TradeSide
from trading.domain.identity import AccountKey, AccountType, AssetId, VenueId
from trading.domain.order import ExecutionInstructions, TimeInForce
from trading.domain.product import ProductType
from trading.execution.recovery import OrderRecoveryReport
from trading.application.clock import FixedClock


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


class FakeEvent:
    def __init__(self): self.callbacks = []
    def __iadd__(self, callback): self.callbacks.append(callback); return self
    def __isub__(self, callback): self.callbacks.remove(callback); return self
    def emit(self, *args):
        for callback in tuple(self.callbacks): callback(*args)


class FakeIb:
    def __init__(self):
        self.next_id = 100
        self.trades = []
        self.cancelled = []
        self.commissionReportEvent = FakeEvent()
        self.connectedEvent = FakeEvent()

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
    def test_commission_event_drives_durable_fill_backfill_and_reconnect_cursor_cycle(self) -> None:
        session = FakeSession()
        class Recovery:
            def __init__(self): self.calls = 0
            def recover(self, at):
                self.calls += 1
                return OrderRecoveryReport((f"order-{self.calls}",), ())
        recovery = Recovery()
        ingestion = IbkrDurableFillIngestion(session, recovery, clock=FixedClock(NOW))  # type: ignore[arg-type]
        self.assertTrue(ingestion.start().complete)
        self.assertEqual(recovery.calls, 1)
        session.ib.commissionReportEvent.emit(object())
        session.ib.connectedEvent.emit()
        self.assertEqual(recovery.calls, 3)
        self.assertTrue(ingestion.healthy)
        ingestion.stop()
        session.ib.commissionReportEvent.emit(object())
        self.assertEqual(recovery.calls, 3)

    def test_stock_reference_market_execution_and_account_are_not_option_specific(self) -> None:
        session = FakeSession()
        reference = IbkrReferenceAdapter(session)
        stock = reference.sync(ReferenceDataRequest(ProductType.EQUITY, ("AAPL",))).instruments.values()[0]
        self.assertEqual(stock.instrument_type, ProductType.EQUITY)
        self.assertEqual(stock.display_name, "AAPL")
        market = IbkrMarketDataAdapter(session)
        quote = market.snapshot((stock,))[0]
        self.assertEqual((quote.bid, quote.ask), (Decimal("99"), Decimal("101")))
        trade = market.recent_trades((stock,))[0]
        self.assertEqual((trade.price, trade.quantity), (Decimal("100.50"), Decimal("3")))
        bar = market.historical_bars(
            stock, end=NOW + timedelta(days=1), duration="1 D", bar_size="1 min",
        )[0]
        self.assertEqual((bar.open, bar.close, bar.end - bar.start), (Decimal("100"), Decimal("101"), timedelta(minutes=1)))
        account = AccountKey(InstitutionId("ibkr"), "DU123", AccountType.SECURITIES_MARGIN)
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

    def test_ibkr_synchronized_trade_and_fill_recovery(self) -> None:
        session = FakeSession()
        stock = IbkrReferenceAdapter(session).sync(ReferenceDataRequest(ProductType.EQUITY, ("AAPL",))).instruments.values()[0]
        account = AccountKey(InstitutionId("ibkr"), "DU123", AccountType.SECURITIES_MARGIN)
        request = OrderRequest(
            "internal-recovery", "client-recovery", "strategy", "intent", "correlation", account,
            stock.instrument_id, TradeSide.BUY, Decimal("10"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("100")),
        )
        adapter = IbkrExecutionAdapter(session, Environment.PAPER)
        ack = adapter.place_order(request)
        trade = session.ib.trades[-1]
        trade.orderStatus = SimpleNamespace(status="Filled")
        trade.log = [SimpleNamespace(time=NOW)]
        trade.fills = [SimpleNamespace(
            execution=SimpleNamespace(
                execId="exec-900-1", orderId=900, time=NOW, side="BOT", shares=10, price=100,
            ),
            commissionReport=SimpleNamespace(commission=Decimal("1.25"), currency="USD"),
        )]

        recovered = adapter.recover_order(account, request, ack.venue_order_id)

        self.assertTrue(session.connected)
        self.assertEqual(recovered.status, VenueOrderStatus.FILLED)
        self.assertEqual(recovered.acknowledgement.venue_order_id, "900")  # type: ignore[union-attr]
        self.assertEqual(len(recovered.executions), 1)
        self.assertEqual(recovered.executions[0].execution.quantity, Decimal("10"))
        self.assertEqual(recovered.executions[0].execution.fee, Decimal("1.25"))
        self.assertTrue(recovered.executions[0].fully_filled)

    def test_listed_option_reference_and_native_combo_are_separate_typed_paths(self) -> None:
        session = FakeSession()
        reference = IbkrReferenceAdapter(session)
        definitions = reference.sync(ReferenceDataRequest(
            ProductType.LISTED_OPTION,
            ("AAPL:20260821:200:C", "AAPL:20260821:210:C"),
        )).instruments.values()
        self.assertTrue(all(item.instrument_type is ProductType.LISTED_OPTION for item in definitions))
        account = AccountKey(InstitutionId("ibkr"), "DU123", AccountType.SECURITIES_MARGIN)
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

    def test_ibkr_combo_recovery_preserves_leg_level_executions(self) -> None:
        session = FakeSession()
        definitions = IbkrReferenceAdapter(session).sync(ReferenceDataRequest(
            ProductType.LISTED_OPTION,
            ("AAPL:20260821:200:C", "AAPL:20260821:210:C"),
        )).instruments.values()
        account = AccountKey(InstitutionId("ibkr"), "DU123", AccountType.SECURITIES_MARGIN)
        request = ComboOrderRequest(
            "combo-recovery-internal", "combo-recovery-client", "spread", "intent", "correlation",
            account,
            (ComboLegRequest(definitions[0].instrument_id, TradeSide.BUY, 1),
             ComboLegRequest(definitions[1].instrument_id, TradeSide.SELL, 1)),
            Decimal("1"), ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("1.25")),
        )
        adapter = IbkrExecutionAdapter(session, Environment.PAPER)
        ack = adapter.place_combo_order(request)
        trade = session.ib.trades[-1]
        trade.orderStatus = SimpleNamespace(status="Filled")
        trade.log = [SimpleNamespace(time=NOW)]
        trade.fills = [
            SimpleNamespace(
                contract=session.contracts[definitions[0].instrument_id],
                execution=SimpleNamespace(execId="combo-exec-1", orderId=900, time=NOW, side="BOT", shares=1, price=2),
                commissionReport=SimpleNamespace(commission=Decimal("0.65"), currency="USD"),
            ),
            SimpleNamespace(
                contract=session.contracts[definitions[1].instrument_id],
                execution=SimpleNamespace(execId="combo-exec-2", orderId=900, time=NOW, side="SLD", shares=1, price=1),
                commissionReport=SimpleNamespace(commission=Decimal("0.65"), currency="USD"),
            ),
        ]
        recovered = adapter.recover_order(account, request, ack.venue_order_id)
        self.assertEqual(recovered.status, VenueOrderStatus.FILLED)
        self.assertEqual(
            tuple(item.execution.instrument_id for item in recovered.executions),
            tuple(item.instrument_id for item in definitions),
        )
        self.assertEqual(tuple(item.execution.side for item in recovered.executions), (TradeSide.BUY, TradeSide.SELL))
        self.assertEqual(tuple(item.fully_filled for item in recovered.executions), (False, True))


if __name__ == "__main__":
    unittest.main()
