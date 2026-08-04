from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping

from kairospy.application.support.launch.application.commands import SystemCommand, SystemCommandResult


class SystemCommandFileQueue:
    """Filesystem transport for launch-control system commands."""

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


__all__ = ["SystemCommandFileQueue"]
