from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class BrokerConnected:
    broker: str


@dataclass(frozen=True, slots=True)
class BrokerDisconnected:
    broker: str
    reason: str | None = None


IntegrationPayload: TypeAlias = BrokerConnected | BrokerDisconnected
