from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from uuid import uuid4

from kairospy.infrastructure.storage.codec import to_primitive


class OperatorCommandType(StrEnum):
    STOP = "stop"
    PAUSE_NEW_ORDERS = "pause_new_orders"
    RESUME = "resume"
    SET_REDUCE_ONLY = "set_reduce_only"
    CLEAR_REDUCE_ONLY = "clear_reduce_only"
    KILL_SWITCH = "kill_switch"
    RESET_KILL_SWITCH = "reset_kill_switch"
    CANCEL_ALL = "cancel_all"
    RELOAD_RISK_LIMITS = "reload_risk_limits"
    REQUEST_STATUS_SNAPSHOT = "request_status_snapshot"
    REQUEST_RECONCILIATION = "request_reconciliation"
    TARGET_POSITION = "target_position"


class OperatorCommandStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_COMMAND_STATUSES = frozenset({
    OperatorCommandStatus.SUCCEEDED,
    OperatorCommandStatus.FAILED,
    OperatorCommandStatus.REJECTED,
    OperatorCommandStatus.EXPIRED,
})


@dataclass(frozen=True, slots=True)
class OperatorCommandRecord:
    command_id: str
    run_id: str
    command_type: OperatorCommandType
    payload: dict[str, object]
    idempotency_key: str
    actor: str
    reason: str
    status: OperatorCommandStatus
    created_at: datetime
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    result: dict[str, object] | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_COMMAND_STATUSES

    def manifest(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "run_id": self.run_id,
            "command_type": self.command_type.value,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
            "actor": self.actor,
            "reason": self.reason,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at.isoformat() if self.claimed_at is not None else None,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at is not None else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at is not None else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
            "result": self.result,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


class OperatorCommandBus:
    """Durable operator control plane for one runtime store."""

    def __init__(self, store: object) -> None:
        if not hasattr(store, "transaction"):
            raise ValueError("operator command bus requires a transactional runtime store")
        self.store = store

    def submit(
        self,
        *,
        run_id: str,
        command_type: OperatorCommandType | str,
        payload: dict[str, object] | None,
        actor: str,
        reason: str,
        idempotency_key: str | None,
        at: datetime,
        expires_at: datetime | None = None,
        command_id: str | None = None,
    ) -> OperatorCommandRecord:
        _aware(at)
        if expires_at is not None:
            _aware(expires_at)
        command_type = OperatorCommandType(command_type)
        run_id = _required_text(run_id, "operator command run_id")
        actor = _required_text(actor, "operator command actor")
        reason = _required_text(reason, "operator command reason")
        payload = dict(payload or {})
        idempotency_key = idempotency_key or _default_idempotency_key(run_id, command_type, payload, reason)
        command_id = command_id or f"operator:{uuid4()}"
        payload_json = _encode(payload)
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM operator_commands WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                record = _operator_command_record(existing)
                if (
                    record.command_type is not command_type
                    or _encode(record.payload) != payload_json
                    or record.actor != actor
                    or record.reason != reason
                ):
                    raise ValueError("operator command idempotency key was already used for different content")
                return record
            connection.execute(
                """INSERT INTO operator_commands(
                    command_id, run_id, command_type, payload_json, idempotency_key,
                    actor, reason, status, created_at, claimed_by, claimed_at,
                    accepted_at, completed_at, expires_at, result_json, error_type, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL)""",
                (
                    command_id,
                    run_id,
                    command_type.value,
                    payload_json,
                    idempotency_key,
                    actor,
                    reason,
                    OperatorCommandStatus.PENDING.value,
                    at.isoformat(),
                    expires_at.isoformat() if expires_at is not None else None,
                ),
            )
            row = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
        assert row is not None
        return _operator_command_record(row)

    def pending(
        self,
        run_id: str,
        *command_types: OperatorCommandType | str,
    ) -> tuple[OperatorCommandRecord, ...]:
        run_id = _required_text(run_id, "operator command run_id")
        query = "SELECT * FROM operator_commands WHERE run_id = ? AND status = ?"
        parameters: list[object] = [run_id, OperatorCommandStatus.PENDING.value]
        if command_types:
            types = tuple(OperatorCommandType(item).value for item in command_types)
            query += " AND command_type IN (" + ",".join("?" for _ in types) + ")"
            parameters.extend(types)
        query += " ORDER BY created_at, command_id"
        with self.store.transaction() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return tuple(_operator_command_record(row) for row in rows)

    def commands(self, run_id: str, *, limit: int | None = None) -> tuple[OperatorCommandRecord, ...]:
        run_id = _required_text(run_id, "operator command run_id")
        query = "SELECT * FROM operator_commands WHERE run_id = ? ORDER BY created_at, command_id"
        parameters: tuple[object, ...] = (run_id,)
        if limit is not None:
            if limit <= 0:
                raise ValueError("operator command limit must be positive")
            query += " LIMIT ?"
            parameters = (run_id, limit)
        with self.store.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_operator_command_record(row) for row in rows)

    def claim_next(
        self,
        *,
        run_id: str,
        claimed_by: str,
        at: datetime,
        command_types: tuple[OperatorCommandType | str, ...] = (),
    ) -> OperatorCommandRecord | None:
        _aware(at)
        run_id = _required_text(run_id, "operator command run_id")
        claimed_by = _required_text(claimed_by, "operator command claimer")
        statuses = (OperatorCommandStatus.PENDING.value,)
        query = "SELECT * FROM operator_commands WHERE run_id = ? AND status = ?"
        parameters: list[object] = [run_id, *statuses]
        if command_types:
            types = tuple(OperatorCommandType(item).value for item in command_types)
            query += " AND command_type IN (" + ",".join("?" for _ in types) + ")"
            parameters.extend(types)
        query += " ORDER BY created_at, command_id LIMIT 1"
        with self.store.transaction() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
            if row is None:
                return None
            expires_at = row["expires_at"]
            if expires_at is not None and datetime.fromisoformat(expires_at) <= at:
                connection.execute(
                    """UPDATE operator_commands
                       SET status = ?, completed_at = ?, error_type = ?, error_message = ?
                       WHERE command_id = ?""",
                    (
                        OperatorCommandStatus.EXPIRED.value,
                        at.isoformat(),
                        "Expired",
                        "operator command expired before claim",
                        row["command_id"],
                    ),
                )
                return None
            connection.execute(
                """UPDATE operator_commands
                   SET status = ?, claimed_by = ?, claimed_at = ?
                   WHERE command_id = ? AND status = ?""",
                (
                    OperatorCommandStatus.CLAIMED.value,
                    claimed_by,
                    at.isoformat(),
                    row["command_id"],
                    OperatorCommandStatus.PENDING.value,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM operator_commands WHERE command_id = ?",
                (row["command_id"],),
            ).fetchone()
        assert updated is not None
        return _operator_command_record(updated)

    def accept(self, command_id: str, at: datetime) -> OperatorCommandRecord:
        return self._transition(command_id, at, OperatorCommandStatus.ACCEPTED, allowed=(OperatorCommandStatus.CLAIMED,))

    def start(self, command_id: str, at: datetime) -> OperatorCommandRecord:
        return self._transition(
            command_id,
            at,
            OperatorCommandStatus.RUNNING,
            allowed=(OperatorCommandStatus.CLAIMED, OperatorCommandStatus.ACCEPTED),
        )

    def complete(self, command_id: str, result: dict[str, object] | None, at: datetime) -> OperatorCommandRecord:
        _aware(at)
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
            if row is None:
                raise LookupError(f"operator command not found: {command_id}")
            record = _operator_command_record(row)
            if record.status is OperatorCommandStatus.SUCCEEDED:
                return record
            if record.status not in {
                OperatorCommandStatus.CLAIMED,
                OperatorCommandStatus.ACCEPTED,
                OperatorCommandStatus.RUNNING,
            }:
                raise ValueError(f"cannot complete operator command from {record.status.value}")
            connection.execute(
                """UPDATE operator_commands
                   SET status = ?, completed_at = ?, result_json = ?, error_type = NULL, error_message = NULL
                   WHERE command_id = ?""",
                (OperatorCommandStatus.SUCCEEDED.value, at.isoformat(), _encode(result or {}), command_id),
            )
            updated = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
        assert updated is not None
        return _operator_command_record(updated)

    def fail(self, command_id: str, error: Exception | str, at: datetime) -> OperatorCommandRecord:
        _aware(at)
        error_type = type(error).__name__ if isinstance(error, Exception) else "Error"
        error_message = str(error)
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
            if row is None:
                raise LookupError(f"operator command not found: {command_id}")
            record = _operator_command_record(row)
            if record.status in TERMINAL_COMMAND_STATUSES:
                return record
            connection.execute(
                """UPDATE operator_commands
                   SET status = ?, completed_at = ?, error_type = ?, error_message = ?
                   WHERE command_id = ?""",
                (OperatorCommandStatus.FAILED.value, at.isoformat(), error_type, error_message, command_id),
            )
            updated = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
        assert updated is not None
        return _operator_command_record(updated)

    def _transition(
        self,
        command_id: str,
        at: datetime,
        target: OperatorCommandStatus,
        *,
        allowed: tuple[OperatorCommandStatus, ...],
    ) -> OperatorCommandRecord:
        _aware(at)
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
            if row is None:
                raise LookupError(f"operator command not found: {command_id}")
            record = _operator_command_record(row)
            if record.status is target:
                return record
            if record.status not in allowed:
                raise ValueError(f"cannot move operator command from {record.status.value} to {target.value}")
            fields = "status = ?"
            values: list[object] = [target.value]
            if target is OperatorCommandStatus.ACCEPTED:
                fields += ", accepted_at = ?"
                values.append(at.isoformat())
            values.append(command_id)
            connection.execute(f"UPDATE operator_commands SET {fields} WHERE command_id = ?", tuple(values))
            updated = connection.execute("SELECT * FROM operator_commands WHERE command_id = ?", (command_id,)).fetchone()
        assert updated is not None
        return _operator_command_record(updated)


def _operator_command_record(row: object) -> OperatorCommandRecord:
    payload = json.loads(row["payload_json"])
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return OperatorCommandRecord(
        str(row["command_id"]),
        str(row["run_id"]),
        OperatorCommandType(row["command_type"]),
        payload if isinstance(payload, dict) else {"value": payload},
        str(row["idempotency_key"]),
        str(row["actor"]),
        str(row["reason"]),
        OperatorCommandStatus(row["status"]),
        datetime.fromisoformat(row["created_at"]),
        row["claimed_by"],
        datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None,
        datetime.fromisoformat(row["accepted_at"]) if row["accepted_at"] else None,
        datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        result if isinstance(result, dict) or result is None else {"value": result},
        row["error_type"],
        row["error_message"],
    )


def _default_idempotency_key(
    run_id: str,
    command_type: OperatorCommandType,
    payload: dict[str, object],
    reason: str,
) -> str:
    material = _encode({"run_id": run_id, "type": command_type.value, "payload": payload, "reason": reason})
    return f"{command_type.value}:{sha256(material.encode()).hexdigest()[:16]}"


def _required_text(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text


def _encode(value: object) -> str:
    return json.dumps(to_primitive(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("operator command timestamps must be timezone-aware")


__all__ = [
    "OperatorCommandBus",
    "OperatorCommandRecord",
    "OperatorCommandStatus",
    "OperatorCommandType",
    "TERMINAL_COMMAND_STATUSES",
]
