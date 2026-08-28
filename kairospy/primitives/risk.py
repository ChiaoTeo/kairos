from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class PolicyId(TextValue):
    """Canonical Risk policy identity."""


@dataclass(frozen=True, slots=True)
class ReservationId(TextValue):
    """Canonical Risk reservation identity."""


@dataclass(frozen=True, slots=True)
class DecisionId(TextValue):
    """Canonical Risk decision identity."""


@dataclass(frozen=True, slots=True)
class MarginRuleCode(TextValue):
    """Canonical Risk margin-rule code."""


PolicyIdRead = NewType("PolicyIdRead", str)
ReservationIdRead = NewType("ReservationIdRead", str)
DecisionIdRead = NewType("DecisionIdRead", str)
MarginRuleCodeRead = NewType("MarginRuleCodeRead", str)


__all__ = [
    "DecisionId",
    "DecisionIdRead",
    "MarginRuleCode",
    "MarginRuleCodeRead",
    "PolicyId",
    "PolicyIdRead",
    "ReservationId",
    "ReservationIdRead",
]
