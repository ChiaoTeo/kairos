from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class AccountId(TextValue):
    """Canonical Account identity."""


@dataclass(frozen=True, slots=True)
class SegmentKey(TextValue):
    """Stable Account segment identity shared across application boundaries."""


@dataclass(frozen=True, slots=True)
class BrokerId(TextValue):
    """Canonical broker identity."""


AccountIdRead = NewType("AccountIdRead", str)
BrokerIdRead = NewType("BrokerIdRead", str)
SegmentKeyRead = NewType("SegmentKeyRead", str)


__all__ = [
    "AccountId",
    "AccountIdRead",
    "BrokerId",
    "BrokerIdRead",
    "SegmentKey",
    "SegmentKeyRead",
]
