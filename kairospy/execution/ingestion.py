from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.integrations.connectors.binance.user_data_stream import UserFillUpdate
from kairospy.execution.events import TradeExecution, TradeSide
from kairospy.portfolio.ledger_events import DividendPayment, FundingPayment
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


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


class DurableFillIngestionService:
    """Managed service that consumes live fill events into durable execution facts."""

    STATE_KEY_PREFIX = "fill_ingestion"

    def __init__(
        self,
        ingestion: DurableExecutionIngestionService,
        source: object,
        *,
        run_id: str,
        product: str = "spot",
        clock: _Clock | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("fill ingestion service requires run_id")
        if not hasattr(source, "events"):
            raise ValueError("fill ingestion service requires source.events()")
        self.ingestion = ingestion
        self.source = source
        self.run_id = str(run_id)
        self.product = product
        self.clock = clock or _SystemClock()
        self.ingested = 0
        self.duplicates = 0
        self.fill_ingestion_latency_last_ms: float | None = None
        self.fill_ingestion_latency_max_ms: float | None = None

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.run_id}:last"

    def managed_service(self, name: str | None = None):
        from kairospy.runtime.service_supervisor import ManagedServiceSpec

        return ManagedServiceSpec(name or f"fill-ingestion:{self.run_id}", self.run)

    async def run(self) -> None:
        self._persist("running", {"reason": "started"})
        try:
            async for event in self.source.events():
                self.ingest_event(event)
        except asyncio.CancelledError:
            self._persist("stopped", {"reason": "service stopped"})
            raise
        except Exception as error:
            self._persist("failed", {
                "error_type": type(error).__name__,
                "message": str(error),
            })
            raise

    def ingest_event(self, event: object):
        update, fully_filled = _fill_update_and_status(event)
        transaction = self.ingestion.ingest_binance(
            update,
            fully_filled=fully_filled,
            product=self.product,
        )
        if transaction is None:
            self.duplicates += 1
        else:
            self.ingested += 1
        observed_at = self.clock.now()
        self.fill_ingestion_latency_last_ms = max(0.0, (observed_at - update.event_time).total_seconds() * 1000.0)
        self.fill_ingestion_latency_max_ms = max(
            self.fill_ingestion_latency_max_ms or 0.0,
            self.fill_ingestion_latency_last_ms,
        )
        self._persist("running", {
            "last_execution_id": update.execution_id,
            "last_client_order_id": update.client_order_id,
            "last_event_time": update.event_time.isoformat(),
            "fill_ingestion_latency_last_ms": self.fill_ingestion_latency_last_ms,
            "fill_ingestion_latency_max_ms": self.fill_ingestion_latency_max_ms,
        })
        return transaction

    def _persist(self, phase: str, evidence: dict[str, object]) -> None:
        at = self.clock.now()
        self.ingestion.runtime_store.set_runtime_state(self.state_key, {
            "run_id": self.run_id,
            "phase": phase,
            "product": self.product,
            "ingested_count": self.ingested,
            "duplicate_count": self.duplicates,
            "updated_at": at.isoformat(),
            **evidence,
        }, at)


def _fill_update_and_status(event: object) -> tuple[UserFillUpdate, bool]:
    if isinstance(event, tuple) and len(event) == 2 and isinstance(event[0], UserFillUpdate):
        return event[0], bool(event[1])
    if isinstance(event, UserFillUpdate):
        return event, bool(event.fully_filled)
    raise TypeError(f"unsupported fill ingestion event: {type(event).__name__}")


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
