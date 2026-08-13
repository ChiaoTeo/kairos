from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import RiskStatus


@dataclass(frozen=True, slots=True)
class RiskStatusEvent(DataEvent[RiskStatus]):
    kind: Literal["status"] = field(init=False, default="status")


RiskEvent: TypeAlias = RiskStatusEvent
