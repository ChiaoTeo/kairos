from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class StopSignalBindable(Protocol):
    """Runtime capability required by a launch-managed event source."""

    def set_stop_signal(self, stop_requested: Callable[[], bool] | None) -> None: ...


__all__ = ["StopSignalBindable"]
