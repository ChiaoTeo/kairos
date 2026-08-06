"""SQLite-backed order receipt and transition journal."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Mapping


class SqliteOrderAuditStore:
    def __init__(self, path: str | Path, *, instance_id: str) -> None:
        self.path = Path(path)
        self.instance_id = instance_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)

    def record_receipt(self, record: Mapping[str, object]) -> None:
        payload = _row(record, instance_id=self.instance_id, record_type="receipt")
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO execution_audit
                (record_id, record_type, instance_id, order_id, order_venue_id,
                 event_id, account, account_segment, broker, exchange, product_type, symbol,
                 event_kind, outcome, before_status,
                 after_status, observed_at, received_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _values(payload),
            )

    def record_transition(self, record: Mapping[str, object]) -> None:
        payload = _row(record, instance_id=self.instance_id, record_type="transition")
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO execution_audit
                (record_id, record_type, instance_id, order_id, order_venue_id,
                 event_id, account, account_segment, broker, exchange, product_type, symbol,
                 event_kind, outcome, before_status,
                 after_status, observed_at, received_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _values(payload),
            )

    def events(
        self,
        *,
        order_id: str | None = None,
        venue_order_id: str | None = None,
        instance_id: str | None = None,
        account: str | None = None,
        broker: str | None = None,
        exchange: str | None = None,
        product_type: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
        event_kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        clauses = []
        values: list[object] = []
        for column, value in (
            ("order_id", order_id), ("order_venue_id", venue_order_id), ("instance_id", instance_id),
            ("account", account), ("broker", broker), ("exchange", exchange),
            ("product_type", product_type), ("symbol", symbol), ("event_kind", event_kind),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if since is not None:
            clauses.append("COALESCE(observed_at, received_at) >= ?")
            values.append(since)
        if until is not None:
            clauses.append("COALESCE(observed_at, received_at) <= ?")
            values.append(until)
        if status is not None:
            clauses.append("(before_status = ? OR after_status = ?)")
            values.extend((status, status))
        query = "SELECT payload_json FROM execution_audit"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(observed_at, received_at), rowid"
        if limit is not None:
            query += " LIMIT ?"
            values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def trace(self, order_id: str, *, instance_id: str | None = None) -> tuple[Mapping[str, object], ...]:
        return tuple(
            row
            for row in self.events(order_id=order_id, instance_id=instance_id)
            if row.get("record_type") in {"transition", "reservation"}
        )

    def export_jsonl(self, path: str | Path, **filters: object) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = self.events(**filters)  # type: ignore[arg-type]
        target.write_text("".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_audit (
                record_id TEXT PRIMARY KEY,
                record_type TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                order_id TEXT,
                order_venue_id TEXT,
                event_id TEXT,
                account TEXT,
                account_segment TEXT,
                broker TEXT,
                exchange TEXT,
                product_type TEXT,
                symbol TEXT,
                event_kind TEXT,
                outcome TEXT,
                before_status TEXT,
                after_status TEXT,
                observed_at TEXT,
                received_at TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(execution_audit)")}
        for column in ("account_segment", "broker", "exchange", "product_type", "symbol"):
            if column not in columns:
                connection.execute(f"ALTER TABLE execution_audit ADD COLUMN {column} TEXT")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_audit_order_time ON execution_audit(order_id, observed_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_audit_instance_time ON execution_audit(instance_id, observed_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_audit_event ON execution_audit(event_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_execution_audit_dimensions ON execution_audit(account, broker, exchange, product_type, symbol, observed_at)")


class SqliteOrderAuditDirectory:
    """Read-only federation over the canonical launch instance databases."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def events(self, **filters: object) -> tuple[Mapping[str, object], ...]:
        rows: list[Mapping[str, object]] = []
        for path in sorted(self.root.rglob("run.sqlite")):
            instance = _read_instance_id(path)
            store = SqliteOrderAuditStore(path, instance_id=instance)
            rows.extend(store.events(**filters))  # type: ignore[arg-type]
        rows.sort(key=lambda row: (str(row.get("observed_at") or row.get("received_at") or ""), str(row.get("record_id") or "")))
        limit = filters.get("limit")
        return tuple(rows[: int(limit)]) if limit is not None else tuple(rows)

    def trace(self, order_id: str, *, instance_id: str | None = None) -> tuple[Mapping[str, object], ...]:
        return tuple(row for row in self.events(order_id=order_id, instance_id=instance_id) if row.get("record_type") in {"transition", "reservation"})


def _read_instance_id(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT instance_id FROM execution_audit WHERE instance_id IS NOT NULL ORDER BY rowid LIMIT 1"
        ).fetchone()
    return str(row[0]) if row else path.parent.name


def _row(record: Mapping[str, object], *, instance_id: str, record_type: str) -> dict[str, object]:
    payload = dict(record)
    payload.setdefault("record_type", record_type)
    payload.setdefault("instance_id", instance_id)
    payload.setdefault("record_id", _record_id(payload))
    return payload


def _record_id(record: Mapping[str, object]) -> str:
    explicit = record.get("record_id")
    if explicit:
        return str(explicit)
    parts = (record.get("record_type"), record.get("instance_id"), record.get("event_id"), record.get("order_id"), record.get("observed_at"))
    return ":".join("" if value is None else str(value) for value in parts)


def _values(record: Mapping[str, object]) -> tuple[object, ...]:
    return (
        record["record_id"], record.get("record_type", ""), record.get("instance_id", ""),
        record.get("order_id"), record.get("order_venue_id"), record.get("event_id"),
        record.get("account"), record.get("account_segment"), record.get("broker"), record.get("exchange"), record.get("product_type"), record.get("symbol"),
        record.get("event_kind"), record.get("outcome"),
        record.get("before_status"), record.get("after_status"), record.get("observed_at"),
        record.get("received_at"), json.dumps(dict(record), sort_keys=True, default=str),
    )


__all__ = ["SqliteOrderAuditDirectory", "SqliteOrderAuditStore"]
