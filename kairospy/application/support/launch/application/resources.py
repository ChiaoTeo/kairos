from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class LaunchAssembly:
    """Concrete launch output assembly supplied by composition."""

    output: Callable[..., object]


__all__ = ["LaunchAssembly"]
