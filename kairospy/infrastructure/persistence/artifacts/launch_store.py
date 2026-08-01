from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping

from kairospy.infrastructure.persistence.storage.json import read_json_object, write_json
from kairospy.infrastructure.persistence.storage.jsonl import append_jsonl, read_jsonl_objects, replace_jsonl
from kairospy.infrastructure.persistence.storage.paths import safe_name, safe_relative_path


class LaunchInstanceStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def namespace(self, name: str) -> "DataNamespace":
        return DataNamespace(self._path_for_namespace(name))

    def json(self, name: str) -> "JsonResource":
        return JsonResource(self._path_for_name(name, suffix=".json"))

    def jsonl(self, name: str) -> "JsonlResource":
        return JsonlResource(self._path_for_name(name, suffix=".jsonl"))

    def path_for(self, relative_path: str | Path) -> Path:
        path = self.directory / safe_relative_path(relative_path)
        resolved_root = self.directory.resolve()
        resolved_path = path.resolve()
        if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
            raise ValueError(f"path escapes launch instance directory: {relative_path}")
        return path

    def _path_for_namespace(self, name: str) -> Path:
        return self.path_for(safe_name(name))

    def _path_for_name(self, name: str, *, suffix: str) -> Path:
        return self.path_for(f"{safe_name(name)}{suffix}")


class DataNamespace:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def namespace(self, name: str) -> "DataNamespace":
        return DataNamespace(self.directory / safe_name(name))

    def json(self, name: str) -> "JsonResource":
        return JsonResource(self.directory / f"{safe_name(name)}.json")

    def jsonl(self, name: str) -> "JsonlResource":
        return JsonlResource(self.directory / f"{safe_name(name)}.jsonl")


class JsonResource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, payload: object) -> None:
        write_json(self.path, jsonable(payload))

    def read(self) -> dict[str, object]:
        return read_json_object(self.path)


class JsonlResource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, row: Mapping[str, object]) -> None:
        payload = jsonable(row)
        if not isinstance(payload, Mapping):
            raise ValueError("jsonl row must serialize to a mapping")
        append_jsonl(self.path, payload)

    def replace(self, rows: Iterable[object]) -> None:
        replace_jsonl(self.path, (jsonable(row) for row in rows))

    def read(self, *, limit: int | None = None) -> list[dict[str, object]]:
        return read_jsonl_objects(self.path, limit=limit)


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


__all__ = ["DataNamespace", "JsonResource", "JsonlResource", "LaunchInstanceStore", "jsonable"]
