"""RichLog that mirrors displayed output into the Workbench transcript."""

from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console
from textual.widgets import RichLog


class WorkbenchLog(RichLog):
    """Keep the visual log and the agent-readable log in sync."""

    @property
    def plain_text(self) -> str:
        """Return the complete log as copyable text, including scrolled content."""

        return "\n".join(line.text.rstrip() for line in self.lines).strip()

    def write(
        self,
        content: Any,
        width: int | None = None,
        expand: bool = False,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
    ) -> "WorkbenchLog":
        size_was_known = self._size_known
        result = super().write(
            content,
            width=width,
            expand=expand,
            shrink=shrink,
            scroll_end=scroll_end,
            animate=animate,
        )
        transcript = getattr(self.app, "transcript", None)
        if size_was_known and transcript is not None:
            transcript.record_output(
                screen=type(self.screen).__name__,
                widget=self.id,
                text=_plain_text(content, width or max(40, self.size.width)),
            )
        return result


def _plain_text(content: Any, width: int) -> str:
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


__all__ = ["WorkbenchLog"]
