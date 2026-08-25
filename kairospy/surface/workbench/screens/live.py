"""Bounded presentation state for a continuing Workbench log source."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class LiveBuffer:
    """Keep only the visible tail; the source or artifact owns complete logs."""

    source: str
    capacity: int = 500
    following: bool = True
    unseen_lines: int = 0
    dropped_lines: int = 0
    full_log_path: Path | None = None
    lines: deque[str] = field(init=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("LiveBuffer capacity must be positive")
        self.lines = deque(maxlen=self.capacity)

    def append(self, line: str) -> None:
        if len(self.lines) == self.capacity:
            self.dropped_lines += 1
        self.lines.append(line)
        if not self.following:
            self.unseen_lines += 1

    def extend(self, lines: tuple[str, ...]) -> None:
        for line in lines:
            self.append(line)

    def pause(self) -> None:
        self.following = False

    def resume(self) -> None:
        self.following = True
        self.unseen_lines = 0

    def clear_visible(self) -> None:
        self.lines.clear()
        self.unseen_lines = 0

    def copy_text(self) -> str:
        return "\n".join(self.lines)


__all__ = ["LiveBuffer"]
