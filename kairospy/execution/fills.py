from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from kairospy.identity import InstrumentId
from kairospy.execution.events import TradeSide


@dataclass(frozen=True, slots=True)
class LegFill:
    instrument_id: InstrumentId
    side: TradeSide
    ratio: int
    price: Decimal


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: UUID
    order_id: UUID
    intent_id: UUID
    strategy_id: str
    structure_id: UUID
    timestamp: datetime
    legs: tuple[LegFill, ...]
    net_price: Decimal
    quantity: int
    commission: Decimal
    slippage: Decimal
    is_closing: bool


@dataclass(frozen=True, slots=True)
class Settlement:
    settlement_id: UUID
    structure_id: UUID
    instrument_id: InstrumentId
    timestamp: datetime
    settlement_price: Decimal
    intrinsic_value: Decimal
    position_quantity: Decimal
    cash_delta: Decimal
