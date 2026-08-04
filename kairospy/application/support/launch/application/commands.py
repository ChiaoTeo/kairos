from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol
from uuid import uuid4

from kairospy.application.support.messaging import Message


@dataclass(frozen=True, slots=True)
class SystemCommand:
    command_id: str
    kind: str
    requested_at: datetime
    payload: Mapping[str, object]
    actor: str = "cli"

    @classmethod
    def create(
        cls,
        kind: str,
        payload: Mapping[str, object] | None = None,
        *,
        actor: str = "cli",
        command_id: str | None = None,
        requested_at: datetime | None = None,
    ) -> "SystemCommand":
        label = kind.strip().lower()
        if not label:
            raise ValueError("system command kind is required")
        return cls(
            command_id=command_id or str(uuid4()),
            kind=label,
            requested_at=requested_at or datetime.now(timezone.utc),
            payload=dict(payload or {}),
            actor=actor,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SystemCommand":
        command_id = str(value.get("command_id") or "").strip()
        kind = str(value.get("kind") or "").strip().lower()
        if not command_id:
            raise ValueError("system command command_id is required")
        if not kind:
            raise ValueError("system command kind is required")
        payload = value.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise ValueError("system command payload must be a JSON object")
        requested_at = _datetime(value.get("requested_at")) or datetime.now(timezone.utc)
        return cls(
            command_id=command_id,
            kind=kind,
            requested_at=requested_at,
            payload=dict(payload),
            actor=str(value.get("actor") or "cli"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "kind": self.kind,
            "requested_at": self.requested_at,
            "payload": dict(self.payload),
            "actor": self.actor,
        }


@dataclass(frozen=True, slots=True)
class SystemCommandResult:
    command_id: str
    kind: str
    status: str
    handled_at: datetime
    result: Mapping[str, object]
    error: str | None = None

    @classmethod
    def accepted(cls, command: SystemCommand, result: Mapping[str, object] | None = None) -> "SystemCommandResult":
        return cls(command.command_id, command.kind, "accepted", datetime.now(timezone.utc), dict(result or {}))

    @classmethod
    def rejected(cls, command: SystemCommand, error: str) -> "SystemCommandResult":
        return cls(command.command_id, command.kind, "rejected", datetime.now(timezone.utc), {}, error)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "command_id": self.command_id,
            "kind": self.kind,
            "status": self.status,
            "handled_at": self.handled_at,
            "result": dict(self.result),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class SystemCommandHandler(Protocol):
    """Application command dispatcher used by launch control."""

    def dispatch(self, command: SystemCommand) -> SystemCommandResult: ...


def cli_command_message(
    command: str,
    args: Mapping[str, object] | None = None,
    *,
    time: datetime | None = None,
    sequence: int = 1,
) -> Message:
    return Message(
        topic="system.cli.command",
        payload={"command": command, "args": dict(args or {})},
        published_at=time or datetime.now(timezone.utc),
        producer="runtime.system",
        producer_sequence=sequence,
    )


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.fromisoformat(value)


__all__ = ["SystemCommand", "SystemCommandHandler", "SystemCommandResult", "cli_command_message"]
