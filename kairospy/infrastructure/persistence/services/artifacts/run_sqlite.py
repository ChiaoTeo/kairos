"""SQLite persistence for one launch instance's durable run facts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import uuid4


class RunSqliteStore:
    """Canonical store for the durable output of one launch instance.

    The store deliberately keeps payloads as JSON while indexing the run and
    record dimensions needed by research queries. Domain modules own the
    meaning of their stream and payload; this class only owns persistence.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)

    def write_json(self, name: str, payload: Mapping[str, object]) -> None:
        key = _json_key(name)
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO run_metadata (name, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (key, _dump(payload), _now()),
            )

    def read_json(self, name: str) -> dict[str, object]:
        key = _json_key(name)
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM run_metadata WHERE name = ?", (key,)).fetchone()
        return _mapping(row[0]) if row else {}

    def append_record(self, stream: str, record: Mapping[str, object]) -> None:
        payload = dict(record)
        record_id = str(payload.get("record_id") or f"{stream}:{uuid4().hex}")
        observed_at = _first_value(payload, "observed_at", "time", "timestamp", "updated_at")
        sequence = payload.get("sequence")
        sequence_value = int(sequence) if isinstance(sequence, int) else None
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO run_records
                    (record_id, stream, observed_at, sequence, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, stream, observed_at, sequence_value, _dump(payload)),
            )

    def replace_records(self, stream: str, records: tuple[object, ...] | list[object]) -> None:
        with self._connect() as connection, connection:
            connection.execute("DELETE FROM run_records WHERE stream = ?", (stream,))
            for record in records:
                payload = dict(record) if isinstance(record, Mapping) else _object_mapping(record)
                record_id = str(payload.get("record_id") or f"{stream}:{uuid4().hex}")
                observed_at = _first_value(payload, "observed_at", "time", "timestamp", "updated_at")
                sequence = payload.get("sequence")
                sequence_value = int(sequence) if isinstance(sequence, int) else None
                connection.execute(
                    "INSERT INTO run_records (record_id, stream, observed_at, sequence, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (record_id, stream, observed_at, sequence_value, _dump(payload)),
                )

    def read_records(self, stream: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM run_records WHERE stream = ? ORDER BY COALESCE(observed_at, ''), COALESCE(sequence, -1), rowid",
                (stream,),
            ).fetchall()
        return [_mapping(row[0]) for row in rows]

    def update_current(self, namespace: str, payload: Mapping[str, object]) -> None:
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO run_current (namespace, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(namespace) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (namespace, _dump(payload), _now()),
            )

    def read_current(self, namespace: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM run_current WHERE namespace = ?", (namespace,)).fetchone()
        return _mapping(row[0]) if row else {}

    def exists(self, name: str) -> bool:
        if name in {"state.json", "live_state.json", "launch.log"}:
            return (self.path.parent / name).exists()
        if name.endswith(".jsonl"):
            return bool(self.read_records(_stream_name(name)))
        if name.startswith("account/") and name.endswith("current.json"):
            return bool(self.read_current("account"))
        return bool(self.read_json(name))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_metadata (
                name TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_records (
                record_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                observed_at TEXT,
                sequence INTEGER,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (stream, record_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_records_stream_time ON run_records(stream, observed_at, sequence)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_current (
                namespace TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _json_key(name: str) -> str:
    return name.removesuffix(".json").replace("/", ".")


def _stream_name(name: str) -> str:
    return name.removesuffix(".jsonl").split("/")[-1]


def _first_value(payload: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if value is None:
            continue
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return str(value)
    return None


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _mapping(payload: str) -> dict[str, object]:
    value = json.loads(payload)
    return dict(value) if isinstance(value, Mapping) else {}


def _object_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    slots = getattr(value, "__slots__", ())
    return {name: getattr(value, name) for name in slots if isinstance(name, str) and hasattr(value, name)}


def _json_default(value: object) -> object:
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int, float, bool)):
        return getattr(value, "value")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["RunSqliteStore"]
