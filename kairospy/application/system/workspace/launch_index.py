from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping

from kairospy.config import ConfigError


@dataclass(frozen=True, slots=True)
class LaunchIndexEntry:
    name: str
    config_path: Path
    registered_at: str | None = None
    last_instance: str | None = None

    def to_dict(self, *, root: Path | None = None) -> dict[str, object]:
        path = self.config_path
        value = _display_path(path, root) if root is not None else str(path)
        payload: dict[str, object] = {"config": value}
        if self.registered_at is not None:
            payload["registered_at"] = self.registered_at
        if self.last_instance is not None:
            payload["last_instance"] = self.last_instance
        return payload


class LaunchIndex:
    def __init__(self, path: str | Path, *, root: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.root = Path(root).expanduser().resolve()
        self._entries = _read_entries(self.path, root=self.root)

    def list(self) -> tuple[LaunchIndexEntry, ...]:
        return tuple(self._entries.values())

    def get(self, name: str) -> LaunchIndexEntry:
        try:
            return self._entries[name]
        except KeyError as error:
            raise ConfigError(f"unknown registered launch: {name}") from error

    def resolve_config_path(self, name_or_path: str | Path) -> Path:
        raw = Path(name_or_path).expanduser()
        if str(name_or_path) in self._entries:
            return self.get(str(name_or_path)).config_path
        if raw.is_absolute():
            return raw
        candidate = self.root / raw
        return candidate if candidate.exists() else raw

    def register(self, name: str, config_path: str | Path) -> LaunchIndexEntry:
        if not name.strip():
            raise ConfigError("launch name must be a non-empty string")
        path = Path(config_path).expanduser()
        resolved = path if path.is_absolute() else (self.root / path).resolve()
        entry = LaunchIndexEntry(name=name.strip(), config_path=resolved, registered_at=datetime.now(timezone.utc).isoformat())
        self._entries[entry.name] = entry
        self.save()
        return entry

    def unregister(self, name: str) -> LaunchIndexEntry:
        entry = self.get(name)
        del self._entries[name]
        self.save()
        return entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "launches": {entry.name: entry.to_dict(root=self.root) for entry in self.list()},
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "launches": {entry.name: entry.to_dict(root=self.root) for entry in self.list()},
            "count": len(self._entries),
        }


def _read_entries(path: Path, *, root: Path) -> dict[str, LaunchIndexEntry]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise ConfigError(f"invalid launch index JSON {path}: {error}") from error
    if not isinstance(payload, Mapping):
        raise ConfigError(f"launch index must be a JSON object: {path}")
    launches = payload.get("launches", {})
    if not isinstance(launches, Mapping):
        raise ConfigError(f"launch index launches must be a JSON object: {path}")
    entries: dict[str, LaunchIndexEntry] = {}
    for name, raw in launches.items():
        if not isinstance(raw, Mapping):
            raise ConfigError(f"launch index entry must be an object: {name}")
        config = raw.get("config")
        if not isinstance(config, str) or not config.strip():
            raise ConfigError(f"launch index entry {name} requires config")
        config_path = Path(config).expanduser()
        if not config_path.is_absolute():
            config_path = (root / config_path).resolve()
        entries[str(name)] = LaunchIndexEntry(
            name=str(name),
            config_path=config_path,
            registered_at=raw.get("registered_at") if isinstance(raw.get("registered_at"), str) else None,
            last_instance=raw.get("last_instance") if isinstance(raw.get("last_instance"), str) else None,
        )
    return dict(sorted(entries.items()))


def _display_path(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)
