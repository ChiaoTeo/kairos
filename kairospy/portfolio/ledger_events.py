from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from kairospy.identity import AccountRef, AssetId, InstrumentId


@dataclass(frozen=True, slots=True)
class FundingPayment:
    payment_id: UUID
    timestamp: datetime
    account: AccountRef
    instrument_id: InstrumentId
    settlement_asset: AssetId
    amount: Decimal
    funding_rate: Decimal
    position_notional: Decimal


@dataclass(frozen=True, slots=True)
class DividendPayment:
    payment_id: UUID
    timestamp: datetime
    account: AccountRef
    instrument_id: InstrumentId
    cash_asset: AssetId
    gross_amount: Decimal
    withholding_tax: Decimal = Decimal("0")
