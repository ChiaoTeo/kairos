from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypeAlias
from uuid import UUID, uuid4

from kairospy.identity import AccountRef, AssetId, InstrumentId
from kairospy.execution.orders import TimeInForce
from kairospy.execution.events import TradeSide


@dataclass(frozen=True, slots=True)
class LegIntent:
    instrument_id: InstrumentId
    side: TradeSide
    ratio: int = 1

    def __post_init__(self) -> None:
        if self.ratio < 1:
            raise ValueError("leg ratio must be positive")


@dataclass(frozen=True, slots=True)
class OpenStructureIntent:
    strategy_id: str
    legs: tuple[LegIntent, ...]
    quantity: int
    limit_price: Decimal | None
    time_in_force: TimeInForce
    reason: str
    intent_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _validate_structure(self.legs, self.quantity)


@dataclass(frozen=True, slots=True)
class CloseStructureIntent:
    strategy_id: str
    structure_id: UUID
    legs: tuple[LegIntent, ...]
    quantity: int
    limit_price: Decimal | None
    time_in_force: TimeInForce
    reason: str
    intent_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _validate_structure(self.legs, self.quantity)


@dataclass(frozen=True, slots=True)
class TargetPositionIntent:
    intent_id: UUID
    strategy_id: str
    instrument_id: InstrumentId
    target_quantity: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class TargetExposureIntent:
    """Strategy-level target expressed independently of account capital and price."""

    intent_id: UUID
    strategy_id: str
    instrument_id: InstrumentId
    target_fraction: Decimal
    reason: str

    def __post_init__(self) -> None:
        if not Decimal("-1") <= self.target_fraction <= Decimal("1"):
            raise ValueError("target exposure fraction must be in [-1, 1]")


@dataclass(frozen=True, slots=True)
class HedgeIntent:
    intent_id: UUID
    strategy_id: str
    hedge_instrument_id: InstrumentId
    target_delta: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class TransferIntent:
    intent_id: UUID
    strategy_id: str
    source_account: AccountRef
    destination_account: AccountRef
    asset: AssetId
    amount: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class CancelIntent:
    intent_id: UUID
    strategy_id: str
    client_order_id: str
    reason: str


def _validate_structure(legs: tuple[LegIntent, ...], quantity: int) -> None:
    if quantity < 1:
        raise ValueError("quantity must be positive")
    if not legs:
        raise ValueError("at least one leg is required")


StructureIntent: TypeAlias = OpenStructureIntent | CloseStructureIntent
Intent: TypeAlias = (
    StructureIntent
    | TargetExposureIntent
    | TargetPositionIntent
    | HedgeIntent
    | TransferIntent
    | CancelIntent
)
