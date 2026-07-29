from __future__ import annotations

import typer

from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.writer import TextRenderer, write_result


def write_cli_result(
    ctx: typer.Context,
    result: object,
    *,
    output_format: OutputFormat | None,
    default: OutputFormat = OutputFormat.text,
    text: TextRenderer | None = None,
) -> None:
    write_result(result, output=resolve_output(ctx, output_format, default=default), text=text)


__all__ = ["write_cli_result"]
