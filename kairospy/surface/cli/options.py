from __future__ import annotations

from enum import StrEnum
from contextvars import ContextVar
import json

from prettytable import PrettyTable

from kairospy.surface.presentation import redact_value


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    TABLE = "table"


_command_output: ContextVar[OutputFormat | None] = ContextVar(
    "kairos_command_output", default=None
)


def set_command_output(output: OutputFormat) -> object:
    """Set the effective format for one CLI invocation."""
    return _command_output.set(output)


def reset_command_output(token: object) -> None:
    _command_output.reset(token)  # type: ignore[arg-type]


def effective_output(output: OutputFormat) -> OutputFormat:
    return _command_output.get() or output


def render(value: object, output: OutputFormat) -> str:
    output = effective_output(output)
    value = redact_value(value, placeholder="[REDACTED]")
    if output is OutputFormat.JSON:
        return json.dumps(value, default=str, sort_keys=True)
    if output is OutputFormat.TABLE:
        return _render_table(value)
    if isinstance(value, dict):
        return "\n".join(
            f"{key}: {json.dumps(item, default=str, sort_keys=True)}"
            for key, item in sorted(value.items())
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(
            json.dumps(item, default=str, sort_keys=True) for item in value
        )
    return str(value)


def _render_table(value: object) -> str:
    if isinstance(value, dict):
        table = PrettyTable(["key", "value"])
        table.align = "l"
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            table.add_row(
                [
                    key,
                    item
                    if isinstance(item, (str, int, float, bool))
                    else json.dumps(item, default=str, sort_keys=True),
                ]
            )
        return str(table)
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        if not all(isinstance(item, dict) for item in value):
            table = PrettyTable(["value"])
            table.align = "l"
            for item in value:
                table.add_row([item])
            return str(table)
        keys = sorted({key for item in value for key in item})
        table = PrettyTable(keys)
        table.align = "l"
        for item in value:
            table.add_row(
                [
                    item.get(key)
                    if isinstance(item.get(key), (str, int, float, bool))
                    else json.dumps(item.get(key), default=str, sort_keys=True)
                    for key in keys
                ]
            )
        return str(table)
    table = PrettyTable(["value"])
    table.align = "l"
    table.add_row([value])
    return str(table)
