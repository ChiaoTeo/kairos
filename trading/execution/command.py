from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trading.adapters.base import ComboOrderRequest, OrderRequest


OrderCommandRequest = OrderRequest | ComboOrderRequest


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    COMPLETED = "completed"
    UNKNOWN = "unknown"
    FAILED_TERMINAL = "failed_terminal"


@dataclass(frozen=True, slots=True)
class OrderCommand:
    command_id: str
    request: OrderCommandRequest
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    command: OrderCommand
    status: OutboxStatus
    updated_at: datetime
    attempts: int = 0
    last_error: str | None = None
