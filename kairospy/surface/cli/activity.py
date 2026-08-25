"""Reusable terminal activity feedback for synchronous CLI work."""

from __future__ import annotations

from dataclasses import dataclass, field
import shutil
import threading
import time
from typing import TextIO


_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(slots=True)
class TerminalActivity:
    """Render one in-place spinner with elapsed time on an interactive TTY."""

    label: str
    stream: TextIO
    delay_seconds: float = 0.2
    interval_seconds: float = 0.1
    _started_at: float = field(init=False, default=0.0)
    _rendered: bool = field(init=False, default=False)
    _stop: threading.Event = field(init=False, default_factory=threading.Event)
    _thread: threading.Thread | None = field(init=False, default=None)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def start(self) -> None:
        if not self.enabled:
            return
        self._started_at = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def finish(self, *, succeeded: bool) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_seconds * 2, 0.2))
        elapsed = max(time.monotonic() - self._started_at, 0.0)
        if not self._rendered:
            return
        marker = "✓" if succeeded else "✗"
        self.stream.write(f"\r\x1b[2K{marker} {self.label} · {elapsed:.1f}s\n")
        self.stream.flush()

    def _run(self) -> None:
        if self._stop.wait(self.delay_seconds):
            return
        frame_index = 0
        while not self._stop.is_set():
            elapsed = max(time.monotonic() - self._started_at, 0.0)
            self.stream.write(f"\r\x1b[2K{self._line(_FRAMES[frame_index], elapsed)}")
            self.stream.flush()
            self._rendered = True
            frame_index = (frame_index + 1) % len(_FRAMES)
            self._stop.wait(self.interval_seconds)

    def _line(self, frame: str, elapsed: float) -> str:
        text = f"{frame} {self.label} · {elapsed:.1f}s"
        width = max(shutil.get_terminal_size(fallback=(80, 24)).columns - 1, 1)
        if len(text) <= width:
            return text
        if width == 1:
            return "…"
        return f"{text[: width - 1]}…"


__all__ = ["TerminalActivity"]
