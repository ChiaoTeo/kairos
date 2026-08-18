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

from ..models import DecisionReceipt, DecisionStatus, IntentCandidate


class DecisionRecordStore:
    """Instance-scoped Decision audit with stable admission dedupe."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._closed = False
        self._migrate()

    def admit(self, candidate: IntentCandidate) -> tuple[DecisionReceipt, bool]:
        payload = _canonical_json(candidate.request)
        candidate_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        snapshot = candidate.snapshot
        now = _now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO decision_records (
                    decision_id, request_id, intent_id, strategy_id, launch_id,
                    instance_id, operation, exposure_effect, mode, mode_revision,
                    context_watermark, context_snapshot_hash, profile_hash, candidate_hash,
                    candidate_json, submitted_at, deadline, status, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.decision_id,
                    candidate.request_id,
                    candidate.intent_id,
                    candidate.strategy_id,
                    candidate.launch_id,
                    candidate.instance_id,
                    candidate.operation,
                    candidate.exposure_effect,
                    snapshot.mode.value,
                    snapshot.mode_revision,
                    snapshot.context_watermark,
                    snapshot.context_snapshot_hash,
                    candidate.profile_hash,
                    candidate_hash,
                    payload,
                    candidate.submitted_at.isoformat(),
                    candidate.deadline.isoformat(),
                    DecisionStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            row = self._require_row(candidate.decision_id)
            if not created and row["candidate_hash"] != candidate_hash:
                raise ValueError(
                    "Decision id was reused for a different Intent candidate"
                )
            return _receipt(row), created

    def mark_running(self, decision_id: str) -> DecisionReceipt:
        return self._transition(
            decision_id,
            DecisionStatus.RUNNING,
            allowed=(DecisionStatus.PENDING,),
            started_at=_now(),
        )

    def finish(
        self,
        decision_id: str,
        status: DecisionStatus,
        *,
        result: object | None = None,
        effective_request: object | None = None,
        final_submission_status: str | None = None,
        delivery_certainty: str | None = None,
        reason: str | None = None,
    ) -> DecisionReceipt:
        if status in {
            DecisionStatus.PENDING,
            DecisionStatus.RUNNING,
            DecisionStatus.SUBMITTING,
        }:
            raise ValueError("Decision finish requires a terminal status")
        return self._transition(
            decision_id,
            status,
            allowed=(
                DecisionStatus.PENDING,
                DecisionStatus.RUNNING,
                DecisionStatus.SUBMITTING,
            ),
            completed_at=_now(),
            result_json=None if result is None else _canonical_json(result),
            effective_request_json=(
                None
                if effective_request is None
                else _canonical_json(effective_request)
            ),
            final_submission_status=final_submission_status,
            delivery_certainty=delivery_certainty,
            reason=reason,
        )

    def decision(self, decision_id: str) -> DecisionReceipt | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM decision_records WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return None if row is None else _receipt(row)

    def recent(self, *, limit: int = 100) -> tuple[DecisionReceipt, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("Decision query limit must be between 1 and 1000")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM decision_records
                ORDER BY created_at DESC, decision_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(_receipt(row) for row in rows)

    def interrupt_nonterminal(self) -> int:
        now = _now()
        with self._lock, self._connection:
            uncertain = self._connection.execute(
                """
                UPDATE decision_records
                SET status = ?, delivery_certainty = ?, reason = ?,
                    completed_at = ?, updated_at = ?
                WHERE status = ?
                """,
                (
                    DecisionStatus.SUBMISSION_INDETERMINATE.value,
                    "indeterminate",
                    "Strategy process restarted during final submission",
                    now,
                    now,
                    DecisionStatus.SUBMITTING.value,
                ),
            )
            cursor = self._connection.execute(
                """
                UPDATE decision_records
                SET status = ?, delivery_certainty = ?, reason = ?,
                    completed_at = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    DecisionStatus.INTERRUPTED.value,
                    "not_sent",
                    "Strategy process restarted before final submission",
                    now,
                    now,
                    DecisionStatus.PENDING.value,
                    DecisionStatus.RUNNING.value,
                ),
            )
            return cursor.rowcount + uncertain.rowcount

    def mark_submitting(self, decision_id: str) -> DecisionReceipt:
        return self._transition(
            decision_id,
            DecisionStatus.SUBMITTING,
            allowed=(DecisionStatus.RUNNING,),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _transition(
        self,
        decision_id: str,
        status: DecisionStatus,
        *,
        allowed: tuple[DecisionStatus, ...],
        **values: object,
    ) -> DecisionReceipt:
        columns = {"status": status.value, "updated_at": _now(), **values}
        assignments = ", ".join(f"{name} = ?" for name in columns)
        parameters = [
            *columns.values(),
            decision_id,
            *(value.value for value in allowed),
        ]
        placeholders = ", ".join("?" for _ in allowed)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"""
                UPDATE decision_records SET {assignments}
                WHERE decision_id = ? AND status IN ({placeholders})
                """,
                parameters,
            )
            if cursor.rowcount != 1:
                row = self._require_row(decision_id)
                raise RuntimeError(
                    f"Decision {decision_id} cannot transition from {row['status']} "
                    f"to {status.value}"
                )
            return _receipt(self._require_row(decision_id))

    def _require_row(self, decision_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM decision_records WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Decision record not found: {decision_id}")
        return row

    def _migrate(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_records (
                    decision_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    launch_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    exposure_effect TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    mode_revision INTEGER NOT NULL,
                    context_watermark INTEGER NOT NULL,
                    context_snapshot_hash TEXT NOT NULL,
                    profile_hash TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    result_json TEXT,
                    effective_request_json TEXT,
                    submitted_at TEXT NOT NULL,
                    deadline TEXT NOT NULL,
                    status TEXT NOT NULL,
                    final_submission_status TEXT,
                    delivery_certainty TEXT,
                    reason TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS decision_records_created_at
                ON decision_records(created_at DESC)
                """
            )


def _receipt(row: sqlite3.Row) -> DecisionReceipt:
    return DecisionReceipt(
        decision_id=str(row["decision_id"]),
        request_id=str(row["request_id"]),
        intent_id=str(row["intent_id"]),
        status=DecisionStatus(str(row["status"])),
        final_submission_status=row["final_submission_status"],
        delivery_certainty=row["delivery_certainty"],
        reason=row["reason"],
    )


def _canonical_json(value: object) -> str:
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
    if isinstance(value, (Decimal, datetime, Enum)):
        return value.value if isinstance(value, Enum) else str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Decision record cannot encode {type(value).__name__}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["DecisionRecordStore"]
