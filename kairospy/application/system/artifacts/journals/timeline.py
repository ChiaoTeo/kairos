from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping


class RunTimelineJournal:
    def __init__(self, run_directory: str | Path) -> None:
        self.path = Path(run_directory) / "timeline.jsonl"

    def append(self, record: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(record), sort_keys=True) + "\n")


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    slots = getattr(value, "__slots__", None)
    if isinstance(slots, (tuple, list)):
        return {str(name): _jsonable(getattr(value, name)) for name in slots if isinstance(name, str) and hasattr(value, name)}
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return value


__all__ = ["RunTimelineJournal"]
