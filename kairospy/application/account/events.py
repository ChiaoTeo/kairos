from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from kairospy.domain_types import DataEvent

from .models import AccountStatusChange, Balance, EquityChange, ObservedOrder, Position


@dataclass(frozen=True, slots=True)
class AccountChangeRecord:
    kind: str
    segment_key: str
    payload: object


@dataclass(frozen=True, slots=True)
class AccountFactProvenanceRecord:
    source_id: str
    provider_event_id: str | None = None
    provider_sequence: int | None = None
    provider_occurred_at_unix_nanos: int | None = None
    provider_received_at_unix_nanos: int | None = None


@dataclass(frozen=True, slots=True)
class AccountEventRecord:
    stream_id: str
    sequence: int
    producer: str
    account_id: str
    changes: tuple[AccountChangeRecord, ...]
    occurred_at_unix_nanos: int
    launch_id: str | None = None
    instance_id: str | None = None
    provenance: AccountFactProvenanceRecord | None = None

    def __post_init__(self) -> None:
        if not self.stream_id.strip() or self.sequence <= 0:
            raise ValueError("Account event stream and positive sequence are required")
        if not self.account_id.strip():
            raise ValueError("Account event account_id is required")


@dataclass(frozen=True, slots=True)
class BalanceChangedEvent(DataEvent[Balance]):
    kind: Literal["balance_changed"] = field(init=False, default="balance_changed")


@dataclass(frozen=True, slots=True)
class PositionChangedEvent(DataEvent[Position]):
    kind: Literal["position_changed"] = field(init=False, default="position_changed")


@dataclass(frozen=True, slots=True)
class EquityChangedEvent(DataEvent[EquityChange]):
    kind: Literal["equity_changed"] = field(init=False, default="equity_changed")


@dataclass(frozen=True, slots=True)
class AccountStatusChangedEvent(DataEvent[AccountStatusChange]):
    kind: Literal["status_changed"] = field(init=False, default="status_changed")


@dataclass(frozen=True, slots=True)
class ObservedOrderChangedEvent(DataEvent[ObservedOrder]):
    kind: Literal["observed_order_changed"] = field(init=False, default="observed_order_changed")


AccountEvent: TypeAlias = (
    BalanceChangedEvent
    | PositionChangedEvent
    | EquityChangedEvent
    | AccountStatusChangedEvent
    | ObservedOrderChangedEvent
)
