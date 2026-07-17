from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from trading.accounting.ledger import LedgerService
from trading.adapters.binance.adapter import UserFillUpdate
from trading.domain.execution import DividendPayment, FundingPayment, TradeExecution, TradeSide
from trading.execution.order_state import DurableOrderStatus
from trading.orchestration.runtime_store import SQLiteRuntimeStore


class ExecutionIngestionService:
    """Idempotent bridge from normalized Venue fills to the shared Ledger reducer."""

    def __init__(self, ledger_service: LedgerService) -> None:
        self.ledger_service = ledger_service
        self._execution_ids: set[str] = set()

    def ingest(self, execution: TradeExecution):
        key = str(execution.execution_id)
        transaction_id = uuid5(NAMESPACE_URL, f"execution:{execution.execution_id}")
        if key in self._execution_ids or any(item.transaction_id == transaction_id for item in self.ledger_service.ledger.transactions):
            return None
        transaction = self.ledger_service.trade(execution)
        self._execution_ids.add(key)
        return transaction

    def ingest_binance(self, update: UserFillUpdate):
        return self.ingest(TradeExecution(
            uuid5(NAMESPACE_URL, f"binance-execution:{update.execution_id}"), update.event_time,
            update.account, update.instrument_id, TradeSide(update.side), update.quantity, update.price,
            update.commission_asset, update.commission, update.order_id,
        ))

    def ingest_funding(self, payment: FundingPayment):
        transaction_id = uuid5(NAMESPACE_URL, f"funding:{payment.payment_id}")
        if any(item.transaction_id == transaction_id for item in self.ledger_service.ledger.transactions):
            return None
        return self.ledger_service.funding(payment)


class DurableExecutionIngestionService:
    """Transactional execution ingestion for paper/testnet/live runtimes."""

    def __init__(self, ledger_service: LedgerService, runtime_store: SQLiteRuntimeStore) -> None:
        self.ledger_service = ledger_service
        self.runtime_store = runtime_store

    def ingest(self, execution: TradeExecution, *, external_key: str, client_order_id: str,
               fully_filled: bool, cursor_name: str | None = None, cursor_value: str | None = None):
        transaction = self.ledger_service.build_trade(execution)
        target = DurableOrderStatus.FILLED if fully_filled else DurableOrderStatus.PARTIALLY_FILLED
        committed = self.runtime_store.commit_execution(
            external_key, execution, transaction, client_order_id, target, execution.timestamp,
            cursor_name=cursor_name, cursor_value=cursor_value,
        )
        if not committed:
            return None
        self.ledger_service.ledger.post(transaction)
        return transaction

    def ingest_binance(
        self,
        update: UserFillUpdate,
        *,
        fully_filled: bool,
        product: str = "spot",
    ):
        execution = TradeExecution(
            uuid5(NAMESPACE_URL, f"binance:{product}:trade:{update.execution_id}"),
            update.event_time,
            update.account,
            update.instrument_id,
            TradeSide(update.side),
            update.quantity,
            update.price,
            update.commission_asset,
            update.commission,
            update.client_order_id,
        )
        return self.ingest(
            execution,
            external_key=f"binance:{product}:trade:{update.execution_id}",
            client_order_id=update.client_order_id,
            fully_filled=fully_filled,
            cursor_name=f"binance:{product}:fills:{update.account.value}",
            cursor_value=f"{int(update.event_time.timestamp() * 1000)}:{update.execution_id}",
        )


class DurableAccountingIngestionService:
    """Transactional ingestion for funding, settlement, and other Ledger facts."""

    def __init__(self, ledger_service: LedgerService, runtime_store: SQLiteRuntimeStore) -> None:
        self.ledger_service = ledger_service
        self.runtime_store = runtime_store

    def ingest_funding(
        self,
        payment: FundingPayment,
        *,
        external_key: str | None = None,
        cursor_name: str | None = None,
        cursor_value: str | None = None,
    ):
        transaction = self.ledger_service.build_funding(payment)
        committed = self.runtime_store.commit_ledger_event(
            external_key or f"funding:{payment.payment_id}",
            "funding",
            payment,
            transaction,
            payment.timestamp,
            cursor_name=cursor_name,
            cursor_value=cursor_value,
        )
        if not committed:
            return None
        self.ledger_service.ledger.post(transaction)
        return transaction

    def ingest_funding_history(self, payments: tuple[FundingPayment, ...], *, source: str) -> int:
        if not source.strip():
            raise ValueError("funding history source cannot be empty")
        committed = 0
        for payment in sorted(payments, key=lambda item: (item.timestamp, str(item.payment_id))):
            result = self.ingest_funding(
                payment,
                external_key=f"{source}:funding:{payment.payment_id}",
                cursor_name=f"{source}:funding:{payment.account.value}",
                cursor_value=f"{payment.timestamp.isoformat()}:{payment.payment_id}",
            )
            committed += result is not None
        return committed

    def ingest_dividend(
        self, payment: DividendPayment, *, external_key: str | None = None,
        cursor_name: str | None = None, cursor_value: str | None = None,
    ):
        transaction = self.ledger_service.build_dividend(payment)
        committed = self.runtime_store.commit_ledger_event(
            external_key or f"dividend:{payment.payment_id}", "dividend", payment, transaction,
            payment.timestamp, cursor_name=cursor_name, cursor_value=cursor_value,
        )
        if not committed:
            return None
        self.ledger_service.ledger.post(transaction)
        return transaction

    def ingest_corporate_action(
        self, event: object, transaction, *, external_key: str, occurred_at,
        cursor_name: str | None = None, cursor_value: str | None = None,
    ):
        committed = self.runtime_store.commit_ledger_event(
            external_key, "corporate_action", event, transaction, occurred_at,
            cursor_name=cursor_name, cursor_value=cursor_value,
        )
        if not committed:
            return None
        self.ledger_service.ledger.post(transaction)
        return transaction

    def ingest_settlement(
        self,
        event: object,
        transaction,
        *,
        external_key: str,
        occurred_at,
        cursor_name: str | None = None,
        cursor_value: str | None = None,
    ):
        committed = self.runtime_store.commit_ledger_event(
            external_key,
            "settlement",
            event,
            transaction,
            occurred_at,
            cursor_name=cursor_name,
            cursor_value=cursor_value,
        )
        if not committed:
            return None
        self.ledger_service.ledger.post(transaction)
        return transaction
