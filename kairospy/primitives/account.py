from __future__ import annotations

from dataclasses import dataclass

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class AccountId(TextValue):
    """Canonical Account identity."""


@dataclass(frozen=True, slots=True)
class SegmentKey(TextValue):
    """Stable Account segment identity shared across application boundaries."""


__all__ = ["AccountId", "SegmentKey"]
