from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from kairospy.accounting.ledger import LedgerService
from kairospy.ports import OrderAck
from kairospy.connectors.binance.user_data_stream import UserFillUpdate
from kairospy.domain.execution import DividendPayment, FundingPayment, TradeExecution, TradeSide
from kairospy.domain.corporate_action import SplitEvent
from kairospy.domain.identity import AssetId, InstrumentId, VenueId
from kairospy.domain.ledger import Ledger, LedgerBook
from kairospy.domain.product import CryptoSpotSpec, ProductType
from kairospy.execution.ingestion import DurableAccountingIngestionService, DurableExecutionIngestionService
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from kairospy.products.equity.corporate_actions import CorporateActionService
from tests.test_runtime_store import request
from kairospy.reference import BrokerId, ExecutionRoute, ReferenceCatalog, RouteId
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def catalog() -> ReferenceCatalog:
    value = ReferenceCatalog()
    publish_test_instrument(value, InstrumentId("instrument-1"), ProductType.CRYPTO_SPOT, "Test spot", CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")), AssetId("USDT"), VenueId("simulated"), "BTCUSDT", datetime(2020, 1, 1, tzinfo=timezone.utc), quantity_increment=Decimal("0.001"), minimum_quantity=Decimal("0.001"))
    listing = value.active_listings(InstrumentId("instrument-1"), NOW)[0]
    account = request().account
    value.routes.add(ExecutionRoute(RouteId("route:simulated:account-1"), BrokerId("simulated"), account, listing.listing_id, datetime(2020, 1, 1, tzinfo=timezone.utc)))
    return value


class DurableExecutionIngestionTests(unittest.TestCase):
    def test_dividend_and_corporate_action_are_durable_idempotent_ledger_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "venue-corporate-action", NOW,
            ))
            ledger = Ledger(); ledger_service = LedgerService(ledger, catalog())
            fill = TradeExecution(
                UUID("00000000-0000-0000-0000-000000000601"), NOW + timedelta(seconds=1),
                order.account, order.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("10"),
                AssetId("USDT"), Decimal("0"), order.client_order_id,
            )
            DurableExecutionIngestionService(ledger_service, store).ingest(
                fill, external_key="corporate-base-fill", client_order_id=order.client_order_id, fully_filled=True,
            )
            accounting = DurableAccountingIngestionService(ledger_service, store)
            split = SplitEvent(
                UUID("00000000-0000-0000-0000-000000000602"), order.instrument_id,
                NOW + timedelta(seconds=2), Decimal("2"),
            )
            transaction = CorporateActionService(ledger_service).build_split(order.account, split)
            assert transaction is not None
            self.assertIsNotNone(accounting.ingest_corporate_action(
                split, transaction, external_key="ibkr:corporate-action:602", occurred_at=split.effective_at,
            ))
            self.assertIsNone(accounting.ingest_corporate_action(
                split, transaction, external_key="ibkr:corporate-action:602", occurred_at=split.effective_at,
            ))
            dividend = DividendPayment(
                UUID("00000000-0000-0000-0000-000000000603"), NOW + timedelta(seconds=3),
                order.account, order.instrument_id, AssetId("USDT"), Decimal("2"), Decimal("0.2"),
            )
            self.assertIsNotNone(accounting.ingest_dividend(
                dividend, external_key="ibkr:dividend:603", cursor_name="ibkr:cash-transactions", cursor_value="603",
            ))
            self.assertIsNone(accounting.ingest_dividend(dividend, external_key="ibkr:dividend:603"))
            self.assertEqual(store.cursor("ibkr:cash-transactions"), "603")
            self.assertEqual(len(store.load_ledger().transactions), 3)

    def test_fill_order_ledger_and_cursor_commit_as_one_idempotent_fact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            ack = OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "venue-order-1", NOW,
            )
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=ack)

            ledger = Ledger()
            ingestion = DurableExecutionIngestionService(LedgerService(ledger, catalog()), store)
            execution = TradeExecution(
                UUID("00000000-0000-0000-0000-000000000101"), NOW + timedelta(seconds=1),
                order.account, order.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("10"),
                AssetId("USDT"), Decimal("0.1"), order.client_order_id,
            )
            transaction = ingestion.ingest(
                execution, external_key="simulated:fill-1", client_order_id=order.client_order_id,
                fully_filled=True, cursor_name="simulated:fills", cursor_value="101",
            )
            self.assertIsNotNone(transaction)
            self.assertEqual(store.order(order.client_order_id).status, DurableOrderStatus.FILLED)  # type: ignore[union-attr]
            self.assertEqual(store.cursor("simulated:fills"), "101")
            self.assertEqual(len(ledger.transactions), 1)
            self.assertEqual(
                ledger.book_balance(order.account, LedgerBook.CASH, AssetId("USDT")), Decimal("-10.1"),
            )

            duplicate = ingestion.ingest(
                execution, external_key="simulated:fill-1", client_order_id=order.client_order_id,
                fully_filled=True, cursor_name="simulated:fills", cursor_value="101",
            )
            self.assertIsNone(duplicate)
            self.assertEqual(len(ledger.transactions), 1)

            rebuilt = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3").load_ledger()
            self.assertEqual(rebuilt.transactions, ledger.transactions)
            self.assertEqual(rebuilt.entries, ledger.entries)

    def test_conflicting_duplicate_execution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            ack = OrderAck(
                order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                order.correlation_id, "venue-order-1", NOW,
            )
            store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, NOW, ack=ack)
            ingestion = DurableExecutionIngestionService(LedgerService(Ledger(), catalog()), store)
            execution = TradeExecution(
                UUID("00000000-0000-0000-0000-000000000102"), NOW + timedelta(seconds=1),
                order.account, order.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("10"),
                AssetId("USDT"), Decimal("0"), order.client_order_id,
            )
            ingestion.ingest(
                execution, external_key="simulated:fill-conflict", client_order_id=order.client_order_id,
                fully_filled=False,
            )
            changed = TradeExecution(
                execution.execution_id, execution.timestamp, execution.account, execution.instrument_id,
                execution.side, Decimal("2"), execution.price, execution.fee_asset, execution.fee,
                execution.order_id,
            )
            with self.assertRaisesRegex(ValueError, "conflicting content"):
                ingestion.ingest(
                    changed, external_key="simulated:fill-conflict", client_order_id=order.client_order_id,
                    fully_filled=False,
                )

    def test_websocket_and_rest_fill_share_one_durable_external_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            order = request()
            store.create_order(order, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, NOW)
            store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, NOW)
            store.transition_order(
                order.client_order_id,
                DurableOrderStatus.ACKNOWLEDGED,
                NOW,
                ack=OrderAck(
                    order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
                    order.correlation_id, "venue-order-1", NOW,
                ),
            )
            ledger = Ledger()
            ingestion = DurableExecutionIngestionService(LedgerService(ledger, catalog()), store)
            update = UserFillUpdate(
                "501", "venue-order-1", order.client_order_id, order.account, order.instrument_id,
                "buy", Decimal("1"), Decimal("10"), Decimal("0.1"), AssetId("USDT"),
                NOW + timedelta(seconds=1),
            )
            self.assertIsNotNone(ingestion.ingest_binance(update, fully_filled=True, product="spot"))
            rest_execution = TradeExecution(
                UUID("807fdcb1-d53b-54a6-b3a4-f81a4d3afe5b"),
                update.event_time,
                order.account,
                order.instrument_id,
                TradeSide.BUY,
                update.quantity,
                update.price,
                update.commission_asset,
                update.commission,
                order.client_order_id,
            )
            # UUID is derived from the same Binance product/trade identity used by REST recovery.
            from uuid import NAMESPACE_URL, uuid5
            rest_execution = TradeExecution(
                uuid5(NAMESPACE_URL, "binance:spot:trade:501"),
                rest_execution.timestamp,
                rest_execution.account,
                rest_execution.instrument_id,
                rest_execution.side,
                rest_execution.quantity,
                rest_execution.price,
                rest_execution.fee_asset,
                rest_execution.fee,
                rest_execution.order_id,
            )
            self.assertIsNone(ingestion.ingest(
                rest_execution,
                external_key="binance:spot:trade:501",
                client_order_id=order.client_order_id,
                fully_filled=True,
                cursor_name=f"binance:spot:fills:{order.account.value}",
                cursor_value=f"{int(update.event_time.timestamp() * 1000)}:501",
            ))
            self.assertEqual(len(store.load_ledger().transactions), 1)

    def test_funding_event_ledger_and_cursor_are_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            ledger = Ledger()
            service = DurableAccountingIngestionService(LedgerService(ledger, catalog()), store)
            payment = FundingPayment(
                UUID("00000000-0000-0000-0000-000000000601"),
                NOW + timedelta(hours=8),
                request().account,
                request().instrument_id,
                AssetId("USDT"),
                Decimal("5"),
                Decimal("0.0001"),
                Decimal("50000"),
            )
            self.assertIsNotNone(service.ingest_funding(
                payment,
                external_key="binance:funding:601",
                cursor_name="binance:funding",
                cursor_value="601",
            ))
            self.assertIsNone(service.ingest_funding(
                payment,
                external_key="binance:funding:601",
                cursor_name="binance:funding",
                cursor_value="601",
            ))
            self.assertEqual(store.cursor("binance:funding"), "601")
            self.assertEqual(store.load_ledger().transactions, ledger.transactions)
            changed = FundingPayment(
                payment.payment_id, payment.timestamp, payment.account, payment.instrument_id,
                payment.settlement_asset, Decimal("6"), payment.funding_rate, payment.position_notional,
            )
            with self.assertRaisesRegex(ValueError, "conflicting content"):
                service.ingest_funding(changed, external_key="binance:funding:601")


if __name__ == "__main__":
    unittest.main()
