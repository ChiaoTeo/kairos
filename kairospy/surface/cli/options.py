from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import typer

from kairospy.application.usecases.workspace.application.context import profile_cli_format, workspace_cli_format


class OutputFormat(StrEnum):
    auto = "auto"
    text = "text"
    json = "json"
    jsonl = "jsonl"


@dataclass(frozen=True, slots=True)
class RootOptions:
    cwd: Path | None = None
    profile: str | None = None
    output: OutputFormat | None = None
    verbose: bool = False
    debug: bool = False


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
        profile_default = _profile_output()
        workspace_default = _workspace_output()
        return profile_default or workspace_default or default
    return output


def _profile_output() -> OutputFormat | None:
    try:
        value = profile_cli_format()
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if value is None:
        return None
    try:
        output = OutputFormat(value)
    except ValueError as error:
        raise typer.BadParameter(f"unsupported profile cli.format: {value}") from error
    if output is OutputFormat.auto:
        return None
    return output


def _workspace_output() -> OutputFormat | None:
    try:
        value = workspace_cli_format()
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if value is None:
        return None
    try:
        output = OutputFormat(value)
    except ValueError as error:
        raise typer.BadParameter(f"unsupported workspace cli.format: {value}") from error
    if output is OutputFormat.auto:
        return None
    return output


__all__ = ["OutputFormat", "RootOptions", "resolve_output", "root_options"]
