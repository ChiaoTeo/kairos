from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Nasdaq:
    name: str = "nasdaq"


__all__ = ["Nasdaq"]
