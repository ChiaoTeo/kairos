from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import AccountSnapshot, Balance, Position


@dataclass(frozen=True, slots=True)
class AccountSnapshotEvent(DataEvent[AccountSnapshot]):
    kind: Literal["snapshot"] = field(init=False, default="snapshot")


@dataclass(frozen=True, slots=True)
class BalanceEvent(DataEvent[Balance]):
    kind: Literal["balance"] = field(init=False, default="balance")


@dataclass(frozen=True, slots=True)
class PositionEvent(DataEvent[Position]):
    kind: Literal["position"] = field(init=False, default="position")


AccountEvent: TypeAlias = AccountSnapshotEvent | BalanceEvent | PositionEvent
