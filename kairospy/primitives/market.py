from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class SubscriptionId(TextValue):
    """Canonical Market subscription identity."""


@dataclass(frozen=True, slots=True)
class SubscriptionSymbol(TextValue):
    """Canonical symbol used to subscribe to Market data."""


SubscriptionIdRead = NewType("SubscriptionIdRead", str)
SubscriptionSymbolRead = NewType("SubscriptionSymbolRead", str)


__all__ = [
    "SubscriptionId",
    "SubscriptionIdRead",
    "SubscriptionSymbol",
    "SubscriptionSymbolRead",
]
