from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    """A strategy's market-data requirement."""

    subject: str
    selectors: tuple[str, ...] = ()
    exchange: str | None = None
    market_type: str | None = None
    asset_type: str | None = None
    identity: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    dynamic: bool = False

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("subscription subject is required")
        if any(not isinstance(selector, str) or not selector.strip() for selector in self.selectors):
            raise ValueError("subscription selectors must be non-empty strings")
        object.__setattr__(self, "selectors", tuple(self.selectors))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class TargetPositionRequest:
    """A strategy target translated into an Execution-owned Intent."""

    instrument_id: str
    quantity: Decimal
    account_id: str | None = None
    account_ids: tuple[str, ...] = ()
    limit_price: Decimal | None = None
    reason: str = ""
    intent_id: str | None = None
    source_snapshot_id: str | None = None
    source_event_sequence: int | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if self.account_id is not None and not self.account_id.strip():
            raise ValueError("account_id cannot be blank")
        if any(not isinstance(account, str) or not account.strip() for account in self.account_ids):
            raise ValueError("account_ids must contain non-empty strings")
        object.__setattr__(self, "account_ids", tuple(self.account_ids))
        if self.intent_id is not None and not self.intent_id.strip():
            raise ValueError("intent_id cannot be blank")
        if self.source_snapshot_id is not None and not self.source_snapshot_id.strip():
            raise ValueError("source_snapshot_id cannot be blank")
