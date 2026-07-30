from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4


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


class SystemCommandFileQueue:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.commands = self.directory / "commands"
        self.responses = self.directory / "responses"

    def submit(
        self,
        kind: str,
        payload: Mapping[str, object] | None = None,
        *,
        actor: str = "cli",
        command_id: str | None = None,
    ) -> SystemCommand:
        command = SystemCommand.create(kind, payload, actor=actor, command_id=command_id)
        self.write(command)
        return command

    def write(self, command: SystemCommand) -> Path:
        self.commands.mkdir(parents=True, exist_ok=True)
        path = self.command_path(command.command_id)
        _write_json(path, command.to_dict())
        return path

    def pending(self) -> tuple[SystemCommand, ...]:
        if not self.commands.exists():
            return ()
        items: list[SystemCommand] = []
        for path in sorted(self.commands.glob("*.json")):
            if self.response_path(path.stem).exists():
                continue
            try:
                items.append(SystemCommand.from_dict(_read_json(path)))
            except ValueError:
                continue
        return tuple(items)

    def respond(self, result: SystemCommandResult) -> Path:
        self.responses.mkdir(parents=True, exist_ok=True)
        path = self.response_path(result.command_id)
        _write_json(path, result.to_dict())
        return path

    def command_path(self, command_id: str) -> Path:
        return self.commands / f"{command_id}.json"

    def response_path(self, command_id: str) -> Path:
        return self.responses / f"{command_id}.json"


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"system command file must contain a JSON object: {path}")
    return value


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    return datetime.fromisoformat(value)


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["SystemCommand", "SystemCommandFileQueue", "SystemCommandResult"]
