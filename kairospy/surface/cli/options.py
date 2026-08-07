from __future__ import annotations

from enum import StrEnum
import json
import os


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


def render(value: object, output: OutputFormat) -> str:
    configured = os.environ.get("KAIROS_CLI_FORMAT")
    if configured in {item.value for item in OutputFormat}:
        output = OutputFormat(configured)
    if output is OutputFormat.JSON:
        return json.dumps(value, default=str, sort_keys=True)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {json.dumps(item, default=str, sort_keys=True)}" for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return "\n".join(json.dumps(item, default=str, sort_keys=True) for item in value)
    return str(value)
