from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from kairospy.execution.events import TradeExecution, TradeSide
from kairospy.execution.orders import ExecutionCapabilities, ExecutionInstructions
from kairospy.identity import AccountRef, InstitutionId, InstrumentId, VenueId
from kairospy.environment import Environment


class VenueOrderStatus(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    internal_order_id: str
    client_order_id: str
    strategy_id: str
    intent_id: str
    correlation_id: str
    account: AccountRef
    instrument_id: InstrumentId
    side: TradeSide
    quantity: Decimal
    instructions: ExecutionInstructions


@dataclass(frozen=True, slots=True)
class OrderAck:
    internal_order_id: str
    client_order_id: str
    strategy_id: str
    intent_id: str
    correlation_id: str
    venue_order_id: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class ComboLegRequest:
    instrument_id: InstrumentId
    side: TradeSide
    ratio: int


@dataclass(frozen=True, slots=True)
class ComboOrderRequest:
    internal_order_id: str
    client_order_id: str
    strategy_id: str
    intent_id: str
    correlation_id: str
    account: AccountRef
    legs: tuple[ComboLegRequest, ...]
    quantity: Decimal
    instructions: ExecutionInstructions


@dataclass(frozen=True, slots=True)
class RecoveredExecution:
    external_key: str
    execution: TradeExecution
    fully_filled: bool
    cursor_name: str | None = None
    cursor_value: str | None = None


@dataclass(frozen=True, slots=True)
class VenueOrderRecovery:
    status: VenueOrderStatus
    proof: str
    acknowledgement: OrderAck | None = None
    executions: tuple[RecoveredExecution, ...] = ()


class ExecutionPort(Protocol):
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment
    capabilities: ExecutionCapabilities

    def place_order(self, request: OrderRequest) -> OrderAck: ...

    def cancel_order(self, account: AccountRef, venue_order_id: str) -> None: ...

    def open_orders(self, account: AccountRef) -> tuple[str, ...]: ...


class ComboExecutionPort(Protocol):
    venue_id: VenueId
    capabilities: ExecutionCapabilities

    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck: ...


class OrderRecoveryPort(Protocol):
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment

    def recover_order(
        self,
        account: AccountRef,
        request: OrderRequest | ComboOrderRequest,
        venue_order_id: str | None,
    ) -> VenueOrderRecovery: ...


__all__ = [
    "ComboExecutionPort",
    "ComboLegRequest",
    "ComboOrderRequest",
    "Environment",
    "ExecutionPort",
    "OrderAck",
    "OrderRecoveryPort",
    "OrderRequest",
    "RecoveredExecution",
    "VenueOrderRecovery",
    "VenueOrderStatus",
]
