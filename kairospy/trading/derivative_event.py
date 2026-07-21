from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .identity import AccountKey, AssetId, InstrumentId


class DerivativeEventType(StrEnum):
    CONTRACT_EXPIRED = "contract_expired"
    CASH_SETTLED = "cash_settled"
    POSITION_LIQUIDATED = "position_liquidated"
    AUTO_DELEVERAGED = "auto_deleveraged"


@dataclass(frozen=True, slots=True)
class DerivativePositionEvent:
    event_id: UUID
    event_type: DerivativeEventType
    account: AccountKey
    instrument_id: InstrumentId
    quantity: Decimal
    price: Decimal
    settlement_asset: AssetId
    timestamp: datetime
    reason: str
