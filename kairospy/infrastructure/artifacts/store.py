from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
import re
from typing import Iterable, Mapping


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RunInstanceStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def namespace(self, name: str) -> "DataNamespace":
        return DataNamespace(self._path_for_namespace(name))

    def json(self, name: str) -> "JsonResource":
        return JsonResource(self._path_for_name(name, suffix=".json"))

    def jsonl(self, name: str) -> "JsonlResource":
        return JsonlResource(self._path_for_name(name, suffix=".jsonl"))

    def path_for(self, relative_path: str | Path) -> Path:
        path = self.directory / _safe_relative_path(relative_path)
        resolved_root = self.directory.resolve()
        resolved_path = path.resolve()
        if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
            raise ValueError(f"path escapes run instance directory: {relative_path}")
        return path

    def _path_for_namespace(self, name: str) -> Path:
        return self.path_for(_safe_name(name))

    def _path_for_name(self, name: str, *, suffix: str) -> Path:
        return self.path_for(f"{_safe_name(name)}{suffix}")


class DataNamespace:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def namespace(self, name: str) -> "DataNamespace":
        return DataNamespace(self.directory / _safe_name(name))

    def json(self, name: str) -> "JsonResource":
        return JsonResource(self.directory / f"{_safe_name(name)}.json")

    def jsonl(self, name: str) -> "JsonlResource":
        return JsonlResource(self.directory / f"{_safe_name(name)}.jsonl")


class JsonResource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, payload: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(jsonable(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def read(self) -> dict[str, object]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


class JsonlResource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, row: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(jsonable(row), sort_keys=True) + "\n")

    def replace(self, rows: Iterable[object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(jsonable(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    def read(self, *, limit: int | None = None) -> list[dict[str, object]]:
        try:
            rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except FileNotFoundError:
            rows = []
        return rows[-limit:] if limit is not None else rows


def _safe_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"run instance path must be relative: {value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid run instance path: {value}")
    return path


def _safe_name(value: str) -> str:
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError(f"invalid run instance data name: {value!r}")
    return value


def jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    slots = getattr(value, "__slots__", None)
    if isinstance(slots, (tuple, list)):
        return {str(name): jsonable(getattr(value, name)) for name in slots if isinstance(name, str) and hasattr(value, name)}
    if hasattr(value, "__dict__"):
        return {str(key): jsonable(item) for key, item in vars(value).items()}
    return value


__all__ = ["DataNamespace", "JsonResource", "JsonlResource", "RunInstanceStore", "jsonable"]
