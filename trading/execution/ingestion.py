from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from trading.accounting.ledger import LedgerService
from trading.adapters.binance.adapter import UserFillUpdate
from trading.domain.execution import FundingPayment, TradeExecution, TradeSide


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
