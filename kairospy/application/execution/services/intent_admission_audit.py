from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Mapping

from ..admission import IntentAdmissionEvidence


class IntentAdmissionAudit:
    """Execution-owned original/effective admission evidence store."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = RLock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_admission_audit (
                    decision_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    original_intent_json TEXT NOT NULL,
                    effective_intent_json TEXT NOT NULL,
                    original_hash TEXT NOT NULL,
                    effective_hash TEXT NOT NULL,
                    submission_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def record(self, evidence: IntentAdmissionEvidence) -> None:
        original = _canonical(evidence.original_intent)
        effective = _canonical(evidence.effective_intent)
        values = (
            evidence.decision_id,
            evidence.request_id,
            evidence.intent_id,
            evidence.source,
            evidence.outcome,
            original,
            effective,
            _hash(original),
            _hash(effective),
            evidence.submission_status,
            datetime.now(timezone.utc).isoformat(),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO intent_admission_audit (
                    decision_id, request_id, intent_id, source, outcome,
                    original_intent_json, effective_intent_json,
                    original_hash, effective_hash, submission_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    submission_status = excluded.submission_status
                WHERE original_hash = excluded.original_hash
                  AND effective_hash = excluded.effective_hash
                  AND intent_id = excluded.intent_id
                """,
                values,
            )
            row = self._connection.execute(
                """
                SELECT intent_id, original_hash, effective_hash
                FROM intent_admission_audit WHERE decision_id = ?
                """,
                (evidence.decision_id,),
            ).fetchone()
            if row != (evidence.intent_id, _hash(original), _hash(effective)):
                raise ValueError(
                    "Decision id was reused for different Intent admission evidence"
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _canonical(value: object) -> str:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Intent admission audit cannot encode {type(value).__name__}")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["IntentAdmissionAudit"]
