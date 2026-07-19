from __future__ import annotations

from dataclasses import dataclass, field
import sys
import time
from typing import TextIO


@dataclass
class TerminalProgressMatrix:
    """Reusable terminal matrix that redraws in place on a TTY.

    Non-interactive streams cannot move the cursor, so they receive throttled
    snapshots instead. Callers own the meaning of rows, columns and cell text.
    """

    title: str
    rows: tuple[str, ...]
    columns: tuple[str, ...]
    stream: TextIO = sys.stderr
    refresh_interval_seconds: float = 0.1
    snapshot_interval_seconds: float = 10.0
    cells: dict[tuple[str, str], str] = field(default_factory=dict)
    footer: tuple[str, ...] = ()
    _rendered_lines: int = 0
    _last_snapshot_at: float = 0.0

    def set_cell(self, row: str, column: str, value: str) -> None:
        if row not in self.rows or column not in self.columns:
            raise KeyError(f"unknown matrix cell outside declared axes: {row!r}, {column!r}")
        self.cells[(row, column)] = value

    def set_footer(self, *lines: str) -> None:
        self.footer = tuple(lines)

    def render(self, *, force: bool = False, final: bool = False) -> None:
        now = time.monotonic()
        interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        if interactive and not force and not final and now - self._last_snapshot_at < self.refresh_interval_seconds:
            return
        if not interactive and not force and not final and now - self._last_snapshot_at < self.snapshot_interval_seconds:
            return
        lines = self.lines()
        if interactive and self._rendered_lines:
            self.stream.write(f"\x1b[{self._rendered_lines}F")
        for line in lines:
            if interactive:
                self.stream.write("\x1b[2K")
            self.stream.write(line + "\n")
        self.stream.flush()
        self._rendered_lines = len(lines)
        self._last_snapshot_at = now

    def lines(self) -> tuple[str, ...]:
        width = max(7, max((len(value) for value in self.cells.values()), default=0))
        label_width = max(4, max((len(row) for row in self.rows), default=4))
        header = " " * (label_width + 3) + " ".join(f"{column:>{width}}" for column in self.columns)
        separator = "-" * len(header)
        body = []
        for row in self.rows:
            values = [self.cells.get((row, column), "-") for column in self.columns]
            body.append(f"{row:>{label_width}} | " + " ".join(f"{value:>{width}}" for value in values))
        return (self.title, header, separator, *body, *self.footer)
