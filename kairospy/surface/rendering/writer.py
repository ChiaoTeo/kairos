from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Callable, Iterable, Mapping, TextIO

from kairospy.surface.cli.options import OutputFormat


TextRenderer = Callable[[object], str]


def write_result(
    result: object,
    *,
    output: OutputFormat,
    text: TextRenderer | None = None,
    stdout: TextIO | None = None,
) -> None:
    stream = stdout or sys.stdout
    if output is OutputFormat.json:
        stream.write(json.dumps(jsonable(result), ensure_ascii=False, sort_keys=True) + "\n")
        return
    if output is OutputFormat.jsonl:
        write_jsonl(_rows(result), stream)
        return
    if text is not None:
        stream.write(text(result) + "\n")
        return
    stream.write(json.dumps(jsonable(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_jsonl(rows: Iterable[Mapping[str, object]], stream: TextIO) -> int:
    count = 0
    for row in rows:
        stream.write(json.dumps(jsonable(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        count += 1
    return count


def jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, OutputFormat):
        return value.value
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int, float, bool)):
        return getattr(value, "value")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "list"):
        return jsonable(value.list())
    return value


def _rows(result: object) -> Iterable[Mapping[str, object]]:
    if isinstance(result, Mapping):
        rows = result.get("rows")
        if isinstance(rows, list):
            return (row for row in rows if isinstance(row, Mapping))
        for value in result.values():
            if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
                return value
        return (result,)
    if isinstance(result, list):
        return (row for row in result if isinstance(row, Mapping))
    return ()


__all__ = ["jsonable", "write_jsonl", "write_result"]
