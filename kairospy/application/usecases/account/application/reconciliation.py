"""Public account reconciliation request/result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.account.application.read import AccountReadResult
from kairospy.domain.account import AccountSnapshot
from kairospy.application.support.messaging import Message


class AccountEventFactory(Protocol):
    def __call__(self, at: datetime, snapshot: AccountSnapshot) -> Message: ...


@dataclass(frozen=True, slots=True)
class AccountDifference:
    kind: str
    key: str
    local: Decimal
    external: Decimal


@dataclass(frozen=True, slots=True)
class AccountReconciliationResult:
    read: AccountReadResult
    differences: tuple[AccountDifference, ...]
    event: Message


__all__ = ["AccountDifference", "AccountEventFactory", "AccountReconciliationResult"]
