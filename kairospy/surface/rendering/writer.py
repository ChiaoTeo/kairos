from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Iterable, Mapping, Protocol, Sequence, TextIO

from kairospy.surface.cli.options import OutputFormat


class TextRenderer(Protocol):
    def __call__(self, value: object) -> str:
        ...


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
    stream.write(render_text(result) + "\n")


def write_jsonl(rows: Iterable[Mapping[str, object]], stream: TextIO) -> int:
    count = 0
    for row in rows:
        stream.write(json.dumps(jsonable(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        count += 1
    return count


def render_text(result: object) -> str:
    return _render_value(jsonable(result), title="Result")


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


def _render_value(value: object, *, title: str, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, Mapping):
        return _render_mapping(value, title=title, indent=indent)
    if isinstance(value, list):
        return _render_sequence(value, title=title, indent=indent)
    return f"{prefix}{title}  {_format_scalar(value)}"


def _render_mapping(value: Mapping[str, object], *, title: str, indent: int) -> str:
    prefix = " " * indent
    if not value:
        return f"{prefix}{title}\n{prefix}  none"
    lines = [f"{prefix}{title}"]
    key_width = max((len(str(key)) for key in value), default=0)
    for key, item in value.items():
        label = str(key)
        if _is_scalar(item):
            lines.append(f"{prefix}  {label:<{key_width}}  {_format_scalar(item)}")
            continue
        child_title = f"{label}"
        lines.append(_render_value(item, title=child_title, indent=indent + 2))
    return "\n".join(lines)


def _render_sequence(value: Sequence[object], *, title: str, indent: int) -> str:
    prefix = " " * indent
    if not value:
        return f"{prefix}{title}\n{prefix}  none"
    if all(isinstance(item, Mapping) for item in value):
        rows = [item for item in value if isinstance(item, Mapping)]
        if rows and _table_columns(rows):
            return _render_table(rows, title=title, indent=indent)
    lines = [f"{prefix}{title}"]
    for index, item in enumerate(value, start=1):
        if _is_scalar(item):
            lines.append(f"{prefix}  {index:<2} {_format_scalar(item)}")
            continue
        lines.append(_render_value(item, title=str(index), indent=indent + 2))
    return "\n".join(lines)


def _render_table(rows: Sequence[Mapping[str, object]], *, title: str, indent: int) -> str:
    prefix = " " * indent
    columns = _table_columns(rows)
    widths = {
        column: max(len(column), *(len(_format_scalar(row.get(column))) for row in rows))
        for column in columns
    }
    header = "  ".join(f"{column:<{widths[column]}}" for column in columns)
    rule = "  ".join("-" * widths[column] for column in columns)
    lines = [f"{prefix}{title}", f"{prefix}  {header}", f"{prefix}  {rule}"]
    for row in rows:
        lines.append(f"{prefix}  " + "  ".join(f"{_format_scalar(row.get(column)):<{widths[column]}}" for column in columns))
    return "\n".join(lines)


def _table_columns(rows: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    columns: list[str] = []
    for row in rows:
        for key, value in row.items():
            name = str(key)
            if name not in columns and _is_scalar(value):
                columns.append(name)
    return tuple(columns)


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _format_scalar(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


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


__all__ = ["jsonable", "render_text", "write_jsonl", "write_result"]
