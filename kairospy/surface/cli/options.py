from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import typer


class OutputFormat(StrEnum):
    auto = "auto"
    text = "text"
    json = "json"
    jsonl = "jsonl"


@dataclass(frozen=True, slots=True)
class RootOptions:
    workspace: Path | None = None
    profile: str | None = None
    output: OutputFormat | None = None
    verbose: bool = False


def root_options(ctx: typer.Context) -> RootOptions:
    if isinstance(ctx.obj, RootOptions):
        return ctx.obj
    return RootOptions()


def resolve_output(
    ctx: typer.Context,
    explicit: OutputFormat | None = None,
    *,
    default: OutputFormat = OutputFormat.text,
) -> OutputFormat:
    if explicit is not None:
        return explicit
    output = root_options(ctx).output
    if output is None or output is OutputFormat.auto:
        return default
    return output


__all__ = ["OutputFormat", "RootOptions", "resolve_output", "root_options"]
