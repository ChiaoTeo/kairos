from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from kairospy.execution.ports import ComboOrderRequest, OrderAck, OrderRequest
from kairospy.execution.events import TradeExecution
from kairospy.identity import AccountRef
from kairospy.portfolio.ledger import Ledger, LedgerTransaction
from kairospy.execution.order_state import (
    DurableOrderRecord,
    DurableOrderStatus,
    require_order_transition,
)
from kairospy.execution.command import OrderCommand, OutboxRecord, OutboxStatus
from kairospy.risk.strategy_positions import StrategyPositionBook
from kairospy.infrastructure.storage.codec import from_primitive, to_primitive

from kairospy.runtime.testing.faults import RuntimeFaultInjector, RuntimeFaultPoint, inject


SCHEMA_VERSION = 7


@dataclass(frozen=True, slots=True)
class ManualOrderResolution:
    resolution_id: int
    client_order_id: str
    previous_status: DurableOrderStatus
    target_status: DurableOrderStatus
    actor: str
    reason: str
    evidence: str
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class DurableExecutionRecord:
    external_key: str
    execution: TradeExecution
    client_order_id: str
    occurred_at: datetime
    order: DurableOrderRecord


class SQLiteRuntimeStore:
    """Transactional local state for one execution runtime.

    Market and workspace datasets remain in Parquet. This store owns small,
    transactional runtime facts whose uniqueness and crash consistency matter.
    """

    def __init__(self, path: str | Path, *, fault_injector: RuntimeFaultInjector | None = None) -> None:
        self.path = Path(path)
        self.fault_injector = fault_injector
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_order(self, request: OrderRequest | ComboOrderRequest, at: datetime) -> DurableOrderRecord:
        _aware(at)
        encoded = _encode(request)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (request.client_order_id,),
            ).fetchone()
            if existing is not None:
                record = _order_record(existing)
                if record.request != request:
                    raise ValueError("client order id was already used for a different request")
                return record
            connection.execute(
                """INSERT INTO orders(
                    client_order_id, internal_order_id, account_key, order_kind, request_json,
                    status, created_at, updated_at, ack_json, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (
                    request.client_order_id,
                    request.internal_order_id,
                    request.account.value,
                    "combo" if isinstance(request, ComboOrderRequest) else "single",
                    encoded,
                    DurableOrderStatus.PLANNED.value,
                    at.isoformat(),
                    at.isoformat(),
                ),
            )
            self._append_order_event(connection, request.client_order_id, None, DurableOrderStatus.PLANNED, at, None)
        return DurableOrderRecord(request, DurableOrderStatus.PLANNED, at, at)

    def enqueue_order_command(self, request: OrderRequest | ComboOrderRequest, at: datetime) -> OutboxRecord:
        """Atomically create the durable Order and its submit command."""

        _aware(at)
        command_id = f"submit:{request.client_order_id}"
        encoded = _encode(request)
        kind = "combo" if isinstance(request, ComboOrderRequest) else "single"
        with self.transaction() as connection:
            order_row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (request.client_order_id,),
            ).fetchone()
            if order_row is None:
                connection.execute(
                    """INSERT INTO orders(
                        client_order_id, internal_order_id, account_key, order_kind, request_json,
                        status, created_at, updated_at, ack_json, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                    (request.client_order_id, request.internal_order_id, request.account.value, kind, encoded,
                     DurableOrderStatus.PLANNED.value, at.isoformat(), at.isoformat()),
                )
                self._append_order_event(
                    connection, request.client_order_id, None, DurableOrderStatus.PLANNED, at, None,
                )
            else:
                current = _order_record(order_row)
                if current.request != request:
                    raise ValueError("client order id was already used for a different request")
                if current.status is not DurableOrderStatus.PLANNED:
                    existing = connection.execute(
                        "SELECT * FROM order_outbox WHERE command_id = ?", (command_id,),
                    ).fetchone()
                    if existing is not None:
                        return _outbox_record(existing)
                    raise RuntimeError("cannot enqueue an outbox command for an order already in progress")
            existing = connection.execute(
                "SELECT * FROM order_outbox WHERE command_id = ?", (command_id,),
            ).fetchone()
            if existing is not None:
                record = _outbox_record(existing)
                if record.command.request != request:
                    raise ValueError("outbox command id refers to a different request")
                return record
            connection.execute(
                """INSERT INTO order_outbox(
                    command_id, client_order_id, command_kind, command_json, status,
                    created_at, updated_at, attempts, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)""",
                (command_id, request.client_order_id, kind, encoded, OutboxStatus.PENDING.value,
                 at.isoformat(), at.isoformat()),
            )
        return OutboxRecord(OrderCommand(command_id, request, at), OutboxStatus.PENDING, at)

    def claim_next_order_command(self, at: datetime) -> OutboxRecord | None:
        """Atomically claim one command and move its Order to SUBMITTING."""

        _aware(at)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM order_outbox WHERE status = ? ORDER BY created_at, command_id LIMIT 1",
                (OutboxStatus.PENDING.value,),
            ).fetchone()
            if row is None:
                return None
            record = _outbox_record(row)
            order_row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (record.command.request.client_order_id,),
            ).fetchone()
            if order_row is None:
                raise RuntimeError("outbox command has no durable order")
            order = _order_record(order_row)
            if order.status is not DurableOrderStatus.PLANNED:
                raise RuntimeError(f"pending outbox order is not planned: {order.status.value}")
            connection.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE client_order_id = ?",
                (DurableOrderStatus.APPROVED.value, at.isoformat(), order.request.client_order_id),
            )
            self._append_order_event(
                connection, order.request.client_order_id, DurableOrderStatus.PLANNED,
                DurableOrderStatus.APPROVED, at, None,
            )
            connection.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE client_order_id = ?",
                (DurableOrderStatus.SUBMITTING.value, at.isoformat(), order.request.client_order_id),
            )
            self._append_order_event(
                connection, order.request.client_order_id, DurableOrderStatus.APPROVED,
                DurableOrderStatus.SUBMITTING, at, None,
            )
            connection.execute(
                """UPDATE order_outbox SET status = ?, updated_at = ?, attempts = attempts + 1
                   WHERE command_id = ?""",
                (OutboxStatus.DISPATCHING.value, at.isoformat(), record.command.command_id),
            )
            updated = connection.execute(
                "SELECT * FROM order_outbox WHERE command_id = ?", (record.command.command_id,),
            ).fetchone()
        assert updated is not None
        return _outbox_record(updated)

    def complete_order_command(self, command_id: str, ack: OrderAck, at: datetime) -> OutboxRecord:
        _aware(at)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM order_outbox WHERE command_id = ?", (command_id,)).fetchone()
            if row is None:
                raise LookupError(f"outbox command not found: {command_id}")
            record = _outbox_record(row)
            if record.status is OutboxStatus.COMPLETED:
                order = connection.execute(
                    "SELECT * FROM orders WHERE client_order_id = ?", (record.command.request.client_order_id,),
                ).fetchone()
                if order is None or _order_record(order).ack != ack:
                    raise ValueError("completed outbox command has a conflicting acknowledgement")
                return record
            if record.status is not OutboxStatus.DISPATCHING:
                raise ValueError(f"cannot complete outbox command from {record.status.value}")
            order_row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (record.command.request.client_order_id,),
            ).fetchone()
            assert order_row is not None
            order = _order_record(order_row)
            require_order_transition(order.status, DurableOrderStatus.ACKNOWLEDGED)
            connection.execute(
                """UPDATE orders SET status = ?, updated_at = ?, ack_json = ?, reason = NULL
                   WHERE client_order_id = ?""",
                (DurableOrderStatus.ACKNOWLEDGED.value, at.isoformat(), _encode(ack),
                 order.request.client_order_id),
            )
            self._append_order_event(
                connection, order.request.client_order_id, order.status,
                DurableOrderStatus.ACKNOWLEDGED, at, None,
            )
            connection.execute(
                "UPDATE order_outbox SET status = ?, updated_at = ?, last_error = NULL WHERE command_id = ?",
                (OutboxStatus.COMPLETED.value, at.isoformat(), command_id),
            )
            updated = connection.execute("SELECT * FROM order_outbox WHERE command_id = ?", (command_id,)).fetchone()
        assert updated is not None
        return _outbox_record(updated)

    def fail_order_command(self, command_id: str, reason: str, at: datetime, *, terminal: bool) -> OutboxRecord:
        _aware(at)
        if not reason.strip():
            raise ValueError("outbox failure requires a reason")
        outbox_target = OutboxStatus.FAILED_TERMINAL if terminal else OutboxStatus.UNKNOWN
        order_target = DurableOrderStatus.REJECTED if terminal else DurableOrderStatus.UNKNOWN
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM order_outbox WHERE command_id = ?", (command_id,)).fetchone()
            if row is None:
                raise LookupError(f"outbox command not found: {command_id}")
            record = _outbox_record(row)
            if record.status is not OutboxStatus.DISPATCHING:
                raise ValueError(f"cannot fail outbox command from {record.status.value}")
            order_row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (record.command.request.client_order_id,),
            ).fetchone()
            assert order_row is not None
            order = _order_record(order_row)
            require_order_transition(order.status, order_target)
            connection.execute(
                "UPDATE orders SET status = ?, updated_at = ?, reason = ? WHERE client_order_id = ?",
                (order_target.value, at.isoformat(), reason, order.request.client_order_id),
            )
            self._append_order_event(
                connection, order.request.client_order_id, order.status, order_target, at, reason,
            )
            connection.execute(
                "UPDATE order_outbox SET status = ?, updated_at = ?, last_error = ? WHERE command_id = ?",
                (outbox_target.value, at.isoformat(), reason, command_id),
            )
            updated = connection.execute("SELECT * FROM order_outbox WHERE command_id = ?", (command_id,)).fetchone()
        assert updated is not None
        return _outbox_record(updated)

    def outbox_commands(self, *statuses: OutboxStatus) -> tuple[OutboxRecord, ...]:
        query = "SELECT * FROM order_outbox"
        parameters: tuple[object, ...] = ()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            parameters = tuple(item.value for item in statuses)
        query += " ORDER BY created_at, command_id"
        with self.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_outbox_record(row) for row in rows)

    def order(self, client_order_id: str) -> DurableOrderRecord | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,),
            ).fetchone()
        return _order_record(row) if row is not None else None

    def transition_order(self, client_order_id: str, target: DurableOrderStatus, at: datetime, *,
                         ack: OrderAck | None = None, reason: str | None = None) -> DurableOrderRecord:
        _aware(at)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"durable order not found: {client_order_id}")
            current = _order_record(row)
            if current.status is target:
                if ack is not None and current.ack not in (None, ack):
                    raise ValueError("order transition supplied a conflicting acknowledgement")
                return current
            require_order_transition(current.status, target)
            final_ack = ack or current.ack
            if target is DurableOrderStatus.ACKNOWLEDGED and final_ack is None:
                raise ValueError("acknowledged order requires an acknowledgement")
            connection.execute(
                """UPDATE orders SET status = ?, updated_at = ?, ack_json = ?, reason = ?
                   WHERE client_order_id = ?""",
                (
                    target.value,
                    at.isoformat(),
                    _encode(final_ack) if final_ack is not None else None,
                    reason,
                    client_order_id,
                ),
            )
            self._append_order_event(connection, client_order_id, current.status, target, at, reason)
            self._sync_order_outbox(connection, client_order_id, target, at, reason)
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,),
            ).fetchone()
        assert row is not None
        return _order_record(row)

    def unresolved_orders(self) -> tuple[DurableOrderRecord, ...]:
        statuses = (
            DurableOrderStatus.SUBMITTING.value,
            DurableOrderStatus.UNKNOWN.value,
            DurableOrderStatus.CANCELLING.value,
        )
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM orders WHERE status IN (?, ?, ?) ORDER BY created_at, client_order_id", statuses,
            ).fetchall()
        return tuple(_order_record(row) for row in rows)

    def orders_requiring_venue_recovery(self) -> tuple[DurableOrderRecord, ...]:
        statuses = tuple(item.value for item in (
            DurableOrderStatus.SUBMITTING,
            DurableOrderStatus.UNKNOWN,
            DurableOrderStatus.CANCELLING,
            DurableOrderStatus.ACKNOWLEDGED,
            DurableOrderStatus.PARTIALLY_FILLED,
        ))
        placeholders = ",".join("?" for _ in statuses)
        with self.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY created_at, client_order_id",
                statuses,
            ).fetchall()
        return tuple(_order_record(row) for row in rows)

    def local_open_order_ids(self, account: AccountRef) -> tuple[str, ...]:
        statuses = (
            DurableOrderStatus.ACKNOWLEDGED.value,
            DurableOrderStatus.PARTIALLY_FILLED.value,
            DurableOrderStatus.CANCELLING.value,
        )
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT ack_json FROM orders
                   WHERE account_key = ? AND status IN (?, ?, ?) AND ack_json IS NOT NULL
                   ORDER BY client_order_id""",
                (account.value, *statuses),
            ).fetchall()
        return tuple(from_primitive(json.loads(row["ack_json"]), OrderAck).venue_order_id for row in rows)

    def load_strategy_position_book(self, account: AccountRef) -> StrategyPositionBook:
        """Rebuild virtual strategy ownership solely from committed execution facts."""
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT e.execution_json, o.request_json, o.order_kind
                   FROM execution_events e JOIN orders o ON o.client_order_id = e.client_order_id
                   WHERE o.account_key = ? ORDER BY e.occurred_at, e.external_key""",
                (account.value,),
            ).fetchall()
        book = StrategyPositionBook()
        for row in rows:
            execution = from_primitive(json.loads(row["execution_json"]), TradeExecution)
            request_type = ComboOrderRequest if row["order_kind"] == "combo" else OrderRequest
            request = from_primitive(json.loads(row["request_json"]), request_type)
            book.apply(request.strategy_id, execution.instrument_id, execution.quantity * execution.side.sign)
        return book

    def execution_records(self) -> tuple[DurableExecutionRecord, ...]:
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT e.external_key, e.execution_json, e.client_order_id, e.occurred_at, o.*
                   FROM execution_events e JOIN orders o ON o.client_order_id = e.client_order_id
                   ORDER BY e.occurred_at, e.external_key""",
            ).fetchall()
        return tuple(DurableExecutionRecord(
            row["external_key"],
            from_primitive(json.loads(row["execution_json"]), TradeExecution),
            row["client_order_id"],
            datetime.fromisoformat(row["occurred_at"]),
            _order_record(row),
        ) for row in rows)

    def resolve_unresolved_order(
        self, client_order_id: str, target: DurableOrderStatus, at: datetime, *,
        actor: str, reason: str, evidence: str,
    ) -> ManualOrderResolution:
        """Apply an explicit, audited terminal resolution without contacting the Venue."""
        _aware(at)
        if target not in {DurableOrderStatus.REJECTED, DurableOrderStatus.CANCELLED, DurableOrderStatus.EXPIRED}:
            raise ValueError("manual resolution target must be rejected, cancelled, or expired")
        if not actor.strip() or not reason.strip() or not evidence.strip():
            raise ValueError("manual order resolution requires actor, reason, and evidence")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"durable order not found: {client_order_id}")
            current = _order_record(row)
            if current.status not in {
                DurableOrderStatus.SUBMITTING, DurableOrderStatus.UNKNOWN, DurableOrderStatus.CANCELLING,
            }:
                raise ValueError(f"order is not unresolved: {current.status.value}")
            require_order_transition(current.status, target)
            connection.execute(
                "UPDATE orders SET status = ?, updated_at = ?, reason = ? WHERE client_order_id = ?",
                (target.value, at.isoformat(), reason, client_order_id),
            )
            self._append_order_event(connection, client_order_id, current.status, target, at, reason)
            self._sync_order_outbox(connection, client_order_id, target, at, reason)
            cursor = connection.execute(
                """INSERT INTO manual_order_resolutions(
                    client_order_id, previous_status, target_status, actor, reason, evidence, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (client_order_id, current.status.value, target.value, actor, reason, evidence, at.isoformat()),
            )
            resolution_id = int(cursor.lastrowid)
        return ManualOrderResolution(
            resolution_id, client_order_id, current.status, target, actor, reason, evidence, at,
        )

    def manual_order_resolutions(self, client_order_id: str | None = None) -> tuple[ManualOrderResolution, ...]:
        query = "SELECT * FROM manual_order_resolutions"
        parameters: tuple[object, ...] = ()
        if client_order_id is not None:
            query += " WHERE client_order_id = ?"
            parameters = (client_order_id,)
        query += " ORDER BY resolution_id"
        with self.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(ManualOrderResolution(
            int(row["resolution_id"]), row["client_order_id"],
            DurableOrderStatus(row["previous_status"]), DurableOrderStatus(row["target_status"]),
            row["actor"], row["reason"], row["evidence"], datetime.fromisoformat(row["resolved_at"]),
        ) for row in rows)

    def commit_execution(self, external_key: str, execution: TradeExecution, transaction: LedgerTransaction,
                         client_order_id: str, target: DurableOrderStatus, at: datetime, *,
                         cursor_name: str | None = None, cursor_value: str | None = None) -> bool:
        """Atomically persist a fill, Ledger transaction, order state, and optional cursor.

        Returns False for an idempotent replay of the exact same external event.
        """
        _aware(at)
        if not external_key.strip():
            raise ValueError("external execution key cannot be empty")
        if target not in {DurableOrderStatus.PARTIALLY_FILLED, DurableOrderStatus.FILLED}:
            raise ValueError("execution ingestion requires a partial or filled order target")
        if (cursor_name is None) != (cursor_value is None):
            raise ValueError("cursor name and value must be supplied together")
        execution_json = _encode(execution)
        transaction_json = _encode(transaction)
        with self.transaction() as connection:
            duplicate = connection.execute(
                "SELECT execution_json FROM execution_events WHERE external_key = ?", (external_key,),
            ).fetchone()
            if duplicate is not None:
                if duplicate["execution_json"] != execution_json:
                    raise ValueError("external execution key refers to conflicting content")
                if cursor_name is not None and cursor_value is not None:
                    connection.execute(
                        """INSERT INTO consumer_cursors(name, value, updated_at) VALUES (?, ?, ?)
                           ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                        (cursor_name, cursor_value, at.isoformat()),
                    )
                return False
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"durable order not found for execution: {client_order_id}")
            current = _order_record(row)
            require_order_transition(current.status, target)
            connection.execute(
                """INSERT INTO execution_events(
                    external_key, execution_id, client_order_id, execution_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (external_key, str(execution.execution_id), client_order_id, execution_json, at.isoformat()),
            )
            connection.execute(
                """INSERT INTO ledger_transactions(transaction_id, transaction_json, occurred_at)
                   VALUES (?, ?, ?)""",
                (str(transaction.transaction_id), transaction_json, transaction.timestamp.isoformat()),
            )
            inject(
                self.fault_injector, RuntimeFaultPoint.DURING_EXECUTION_TRANSACTION,
                external_key=external_key, client_order_id=client_order_id,
            )
            connection.execute(
                "UPDATE orders SET status = ?, updated_at = ?, reason = NULL WHERE client_order_id = ?",
                (target.value, at.isoformat(), client_order_id),
            )
            self._append_order_event(connection, client_order_id, current.status, target, at, None)
            self._sync_order_outbox(connection, client_order_id, target, at, None)
            if cursor_name is not None and cursor_value is not None:
                connection.execute(
                    """INSERT INTO consumer_cursors(name, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (cursor_name, cursor_value, at.isoformat()),
                )
        return True

    def commit_ledger_event(
        self,
        external_key: str,
        event_kind: str,
        event: object,
        transaction: LedgerTransaction,
        at: datetime,
        *,
        cursor_name: str | None = None,
        cursor_value: str | None = None,
    ) -> bool:
        """Atomically persist a non-order accounting event, Ledger transaction, and cursor."""
        _aware(at)
        if not external_key.strip() or not event_kind.strip():
            raise ValueError("ledger event key and kind cannot be empty")
        if (cursor_name is None) != (cursor_value is None):
            raise ValueError("cursor name and value must be supplied together")
        event_json = _encode(event)
        transaction_json = _encode(transaction)
        with self.transaction() as connection:
            duplicate = connection.execute(
                "SELECT event_kind, event_json, transaction_id FROM ledger_events WHERE external_key = ?",
                (external_key,),
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate["event_kind"] != event_kind
                    or duplicate["event_json"] != event_json
                    or duplicate["transaction_id"] != str(transaction.transaction_id)
                ):
                    raise ValueError("external ledger event key refers to conflicting content")
                if cursor_name is not None and cursor_value is not None:
                    connection.execute(
                        """INSERT INTO consumer_cursors(name, value, updated_at) VALUES (?, ?, ?)
                           ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                        (cursor_name, cursor_value, at.isoformat()),
                    )
                return False
            existing_transaction = connection.execute(
                "SELECT transaction_json FROM ledger_transactions WHERE transaction_id = ?",
                (str(transaction.transaction_id),),
            ).fetchone()
            if existing_transaction is not None:
                if existing_transaction["transaction_json"] != transaction_json:
                    raise ValueError("ledger transaction id refers to conflicting content")
            else:
                connection.execute(
                    """INSERT INTO ledger_transactions(transaction_id, transaction_json, occurred_at)
                       VALUES (?, ?, ?)""",
                    (str(transaction.transaction_id), transaction_json, transaction.timestamp.isoformat()),
                )
            inject(
                self.fault_injector, RuntimeFaultPoint.DURING_LEDGER_EVENT_TRANSACTION,
                external_key=external_key, event_kind=event_kind,
            )
            connection.execute(
                """INSERT INTO ledger_events(
                    external_key, event_kind, event_json, transaction_id, occurred_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (external_key, event_kind, event_json, str(transaction.transaction_id), at.isoformat()),
            )
            if cursor_name is not None and cursor_value is not None:
                connection.execute(
                    """INSERT INTO consumer_cursors(name, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (cursor_name, cursor_value, at.isoformat()),
                )
        return True

    def load_ledger(self) -> Ledger:
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT transaction_json FROM ledger_transactions ORDER BY occurred_at, transaction_id",
            ).fetchall()
        transactions = [from_primitive(json.loads(row["transaction_json"]), LedgerTransaction) for row in rows]
        transactions.sort(key=lambda item: (item.timestamp, str(item.transaction_id)))
        ledger = Ledger()
        for transaction in transactions:
            ledger.post(transaction)
        return ledger

    def import_ledger(self, ledger: Ledger) -> int:
        """Idempotently seed durable Ledger facts from a migration/export Ledger.

        Existing transaction IDs must contain identical content. The method is
        intentionally append-only so the transactional store remains authoritative.
        """
        imported = 0
        with self.transaction() as connection:
            for transaction in sorted(ledger.transactions, key=lambda item: (item.timestamp, str(item.transaction_id))):
                transaction_id = str(transaction.transaction_id)
                encoded = _encode(transaction)
                existing = connection.execute(
                    "SELECT transaction_json FROM ledger_transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                if existing is not None:
                    if existing["transaction_json"] != encoded:
                        raise ValueError(f"ledger transaction id refers to conflicting content: {transaction_id}")
                    continue
                connection.execute(
                    """INSERT INTO ledger_transactions(transaction_id, transaction_json, occurred_at)
                       VALUES (?, ?, ?)""",
                    (transaction_id, encoded, transaction.timestamp.isoformat()),
                )
                imported += 1
        return imported

    def cursor(self, name: str) -> str | None:
        with self.transaction() as connection:
            row = connection.execute("SELECT value FROM consumer_cursors WHERE name = ?", (name,)).fetchone()
        return str(row["value"]) if row is not None else None

    def acquire_account_lock(self, account: AccountRef, owner_id: str, at: datetime, *, lease_seconds: int = 30) -> None:
        _aware(at)
        if not owner_id.strip():
            raise ValueError("account lock owner cannot be empty")
        if lease_seconds <= 0:
            raise ValueError("account lock lease must be positive")
        expires_at = at + timedelta(seconds=lease_seconds)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, expires_at FROM account_locks WHERE account_key = ?", (account.value,),
            ).fetchone()
            active = row is not None and datetime.fromisoformat(row["expires_at"]) > at
            if active and row["owner_id"] != owner_id:
                raise RuntimeError(f"account is already controlled by runtime {row['owner_id']}")
            connection.execute(
                """INSERT INTO account_locks(account_key, owner_id, acquired_at, expires_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(account_key) DO UPDATE SET
                     owner_id=excluded.owner_id, acquired_at=excluded.acquired_at, expires_at=excluded.expires_at""",
                (account.value, owner_id, at.isoformat(), expires_at.isoformat()),
            )

    def heartbeat_account_lock(self, account: AccountRef, owner_id: str, at: datetime, *, lease_seconds: int = 30) -> None:
        _aware(at)
        if lease_seconds <= 0:
            raise ValueError("account lock lease must be positive")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, expires_at FROM account_locks WHERE account_key = ?", (account.value,),
            ).fetchone()
            if row is None or row["owner_id"] != owner_id:
                raise RuntimeError("runtime no longer owns the account lock")
            if datetime.fromisoformat(row["expires_at"]) <= at:
                raise RuntimeError("account lock lease expired before heartbeat")
            connection.execute(
                "UPDATE account_locks SET expires_at = ? WHERE account_key = ?",
                ((at + timedelta(seconds=lease_seconds)).isoformat(), account.value),
            )

    def release_account_lock(self, account: AccountRef, owner_id: str) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id FROM account_locks WHERE account_key = ?", (account.value,),
            ).fetchone()
            if row is None:
                return
            if row["owner_id"] != owner_id:
                raise RuntimeError("runtime cannot release an account lock owned by another runtime")
            connection.execute("DELETE FROM account_locks WHERE account_key = ?", (account.value,))

    def set_runtime_state(self, key: str, value: object, at: datetime) -> None:
        _aware(at)
        if not key.strip():
            raise ValueError("runtime state key cannot be empty")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO runtime_state(key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, _encode(value), at.isoformat()),
            )

    def runtime_state(self, key: str) -> object | None:
        with self.transaction() as connection:
            row = connection.execute("SELECT value_json FROM runtime_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def _migrate(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA journal_mode = WAL").close()
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info(
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders(
                    client_order_id TEXT PRIMARY KEY,
                    internal_order_id TEXT NOT NULL,
                    account_key TEXT NOT NULL,
                    order_kind TEXT NOT NULL DEFAULT 'single',
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ack_json TEXT,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS order_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
                    previous_status TEXT,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS account_locks(
                    account_key TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state(
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_events(
                    external_key TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL UNIQUE,
                    client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
                    execution_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_transactions(
                    transaction_id TEXT PRIMARY KEY,
                    transaction_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_events(
                    external_key TEXT PRIMARY KEY,
                    event_kind TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    transaction_id TEXT NOT NULL REFERENCES ledger_transactions(transaction_id),
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumer_cursors(
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_order_resolutions(
                    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
                    previous_status TEXT NOT NULL,
                    target_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    resolved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_outbox(
                    command_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE REFERENCES orders(client_order_id),
                    command_kind TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                """
            )
            order_columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)").fetchall()}
            if "order_kind" not in order_columns:
                connection.execute("ALTER TABLE orders ADD COLUMN order_kind TEXT NOT NULL DEFAULT 'single'")
            lock_columns = {row[1] for row in connection.execute("PRAGMA table_info(account_locks)").fetchall()}
            if "expires_at" not in lock_columns:
                connection.execute("ALTER TABLE account_locks ADD COLUMN expires_at TEXT")
                connection.execute("UPDATE account_locks SET expires_at = acquired_at WHERE expires_at IS NULL")
            row = connection.execute("SELECT version FROM schema_info").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(row[0]) in {1, 2, 3, 4, 5, 6}:
                connection.execute("UPDATE schema_info SET version = ?", (SCHEMA_VERSION,))
            elif int(row[0]) != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported runtime store schema version: {row[0]}")
            connection.commit()

    @staticmethod
    def _append_order_event(connection: sqlite3.Connection, client_order_id: str,
                            previous: DurableOrderStatus | None, target: DurableOrderStatus,
                            at: datetime, reason: str | None) -> None:
        connection.execute(
            """INSERT INTO order_events(client_order_id, previous_status, status, occurred_at, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (client_order_id, previous.value if previous else None, target.value, at.isoformat(), reason),
        )

    @staticmethod
    def _sync_order_outbox(connection: sqlite3.Connection, client_order_id: str,
                           target: DurableOrderStatus, at: datetime, reason: str | None) -> None:
        if target is DurableOrderStatus.UNKNOWN:
            status = OutboxStatus.UNKNOWN
        elif target is DurableOrderStatus.REJECTED:
            status = OutboxStatus.FAILED_TERMINAL
        elif target in {
            DurableOrderStatus.ACKNOWLEDGED,
            DurableOrderStatus.PARTIALLY_FILLED,
            DurableOrderStatus.FILLED,
            DurableOrderStatus.CANCELLED,
            DurableOrderStatus.EXPIRED,
        }:
            status = OutboxStatus.COMPLETED
        else:
            return
        connection.execute(
            """UPDATE order_outbox SET status = ?, updated_at = ?, last_error = ?
               WHERE client_order_id = ? AND status IN (?, ?)""",
            (status.value, at.isoformat(), reason, client_order_id,
             OutboxStatus.DISPATCHING.value, OutboxStatus.UNKNOWN.value),
        )


def _encode(value: object) -> str:
    return json.dumps(to_primitive(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _order_record(row: sqlite3.Row) -> DurableOrderRecord:
    request_type = ComboOrderRequest if row["order_kind"] == "combo" else OrderRequest
    request = from_primitive(json.loads(row["request_json"]), request_type)
    ack = from_primitive(json.loads(row["ack_json"]), OrderAck) if row["ack_json"] else None
    return DurableOrderRecord(
        request,
        DurableOrderStatus(row["status"]),
        datetime.fromisoformat(row["created_at"]),
        datetime.fromisoformat(row["updated_at"]),
        ack,
        row["reason"],
    )


def _outbox_record(row: sqlite3.Row) -> OutboxRecord:
    request_type = ComboOrderRequest if row["command_kind"] == "combo" else OrderRequest
    request = from_primitive(json.loads(row["command_json"]), request_type)
    created_at = datetime.fromisoformat(row["created_at"])
    return OutboxRecord(
        OrderCommand(row["command_id"], request, created_at),
        OutboxStatus(row["status"]),
        datetime.fromisoformat(row["updated_at"]),
        int(row["attempts"]),
        row["last_error"],
    )


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("runtime timestamps must be timezone-aware")
