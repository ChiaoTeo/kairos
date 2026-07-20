from __future__ import annotations

from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
from uuid import UUID

from kairos.domain.identity import AssetId
from kairos.reference.identity import LocationId

from .transfer_contracts import FeePolicy, TransferOperation, TransferOperationEvent, TransferStatus


class SQLiteTreasuryRepository:
    """Crash-safe operation/event persistence with provider-event deduplication."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS treasury_operations (
                    transfer_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    instruction_id TEXT NOT NULL,
                    source_location_id TEXT NOT NULL,
                    destination_location_id TEXT,
                    asset_id TEXT NOT NULL,
                    requested_amount TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    fee_policy TEXT NOT NULL DEFAULT 'add_to_amount',
                    debited_amount TEXT,
                    credited_amount TEXT,
                    fee_amount TEXT,
                    fee_asset TEXT,
                    provider_reference TEXT,
                    transaction_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS treasury_events (
                    event_id TEXT PRIMARY KEY,
                    transfer_id TEXT NOT NULL REFERENCES treasury_operations(transfer_id),
                    previous_status TEXT,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    provider_event_id TEXT UNIQUE,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS treasury_events_transfer
                    ON treasury_events(transfer_id, occurred_at, event_id);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(treasury_operations)")}
            if "fee_policy" not in columns:
                connection.execute("ALTER TABLE treasury_operations ADD COLUMN fee_policy TEXT NOT NULL DEFAULT 'add_to_amount'")
            connection.commit()

    def append(self, operation: TransferOperation, event: TransferOperationEvent) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT transfer_id FROM treasury_operations WHERE transfer_id = ?", (operation.transfer_id,)).fetchone()
            values = _operation_values(operation)
            if existing is None:
                connection.execute(
                    """INSERT INTO treasury_operations VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""", values,
                )
            else:
                connection.execute(
                    """UPDATE treasury_operations SET
                        intent_id=?, instruction_id=?, source_location_id=?, destination_location_id=?,
                        asset_id=?, requested_amount=?, status=?, created_at=?, updated_at=?, fee_policy=?, debited_amount=?,
                        credited_amount=?, fee_amount=?, fee_asset=?, provider_reference=?, transaction_hash=?
                       WHERE transfer_id=?""",
                    (*values[1:], values[0]),
                )
            connection.execute(
                "INSERT INTO treasury_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.event_id, event.transfer_id, event.previous_status.value if event.previous_status else None,
                 event.status.value, event.occurred_at.isoformat(), event.provider_event_id, event.detail),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load(self) -> tuple[tuple[TransferOperation, ...], tuple[TransferOperationEvent, ...]]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            operations = tuple(_operation(row) for row in connection.execute("SELECT * FROM treasury_operations ORDER BY created_at, transfer_id"))
            events = tuple(_event(row) for row in connection.execute("SELECT * FROM treasury_events ORDER BY occurred_at, event_id"))
            return operations, events
        finally:
            connection.close()


def _operation_values(item: TransferOperation) -> tuple:
    return (
        item.transfer_id, str(item.intent_id), item.instruction_id, item.source_location_id.value,
        item.destination_location_id.value if item.destination_location_id else None,
        item.asset_id.value, str(item.requested_amount), item.status.value,
        item.created_at.isoformat(), item.updated_at.isoformat(),
        item.fee_policy.value,
        str(item.debited_amount) if item.debited_amount is not None else None,
        str(item.credited_amount) if item.credited_amount is not None else None,
        str(item.fee_amount) if item.fee_amount is not None else None,
        item.fee_asset.value if item.fee_asset else None, item.provider_reference, item.transaction_hash,
    )


def _operation(row) -> TransferOperation:
    return TransferOperation(
        row["transfer_id"], UUID(row["intent_id"]), row["instruction_id"], LocationId(row["source_location_id"]),
        LocationId(row["destination_location_id"]) if row["destination_location_id"] else None,
        AssetId(row["asset_id"]), Decimal(row["requested_amount"]), TransferStatus(row["status"]),
        datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["updated_at"]),
        FeePolicy(row["fee_policy"]),
        Decimal(row["debited_amount"]) if row["debited_amount"] else None,
        Decimal(row["credited_amount"]) if row["credited_amount"] else None,
        Decimal(row["fee_amount"]) if row["fee_amount"] else None,
        AssetId(row["fee_asset"]) if row["fee_asset"] else None,
        row["provider_reference"], row["transaction_hash"],
    )


def _event(row) -> TransferOperationEvent:
    return TransferOperationEvent(
        row["event_id"], row["transfer_id"], TransferStatus(row["previous_status"]) if row["previous_status"] else None,
        TransferStatus(row["status"]), datetime.fromisoformat(row["occurred_at"]), row["provider_event_id"], row["detail"],
    )
