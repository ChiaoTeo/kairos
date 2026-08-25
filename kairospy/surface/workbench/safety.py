"""Last-mile redaction for renderables shown or copied by the Workbench."""

from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console, RenderableType
from rich.text import Text

from kairospy.surface.presentation import redact_text


def renderable_plain_text(content: Any, width: int = 100) -> str:
    """Render a Rich value to stable plain text without terminal styling."""

    output = StringIO()
    console = Console(
        file=output,
        record=True,
        width=width,
        color_system=None,
        force_terminal=False,
    )
    console.print(content, markup=False, highlight=False)
    return output.getvalue()


def redact_renderable(content: RenderableType, *, width: int = 100) -> RenderableType:
    """Preserve safe Rich output and degrade sensitive output to redacted text."""

    rendered = renderable_plain_text(content, width=width)
    redacted = redact_text(rendered)
    if redacted == rendered:
        return content
    return Text(redacted.rstrip())


__all__ = ["redact_renderable", "renderable_plain_text"]
