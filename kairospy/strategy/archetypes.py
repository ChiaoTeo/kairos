from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias
from uuid import UUID

from kairospy.identity import InstrumentId


@dataclass(frozen=True, slots=True)
class CoveredCallIntent:
    intent_id: UUID
    strategy_id: str
    equity_id: InstrumentId
    option_id: InstrumentId
    contracts: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class ProtectivePutIntent:
    intent_id: UUID
    strategy_id: str
    equity_id: InstrumentId
    option_id: InstrumentId
    contracts: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class CashAndCarryIntent:
    intent_id: UUID
    strategy_id: str
    spot_instrument_id: InstrumentId
    derivative_instrument_id: InstrumentId
    spot_quantity: Decimal
    derivative_quantity: Decimal
    reason: str


ArchetypeIntent: TypeAlias = CoveredCallIntent | ProtectivePutIntent | CashAndCarryIntent
