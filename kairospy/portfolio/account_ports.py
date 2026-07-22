from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.identity import AccountRef, AssetId, InstitutionId, InstrumentId, VenueId
from kairospy.environment import Environment


@dataclass(frozen=True, slots=True)
class VenueBalance:
    asset: AssetId
    total: Decimal
    available: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    borrowed: Decimal = Decimal("0")
    interest: Decimal = Decimal("0")
    collateral: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class AccountState:
    account: AccountRef
    balances: tuple[VenueBalance, ...]
    positions: tuple[tuple[InstrumentId, Decimal], ...]
    open_order_ids: tuple[str, ...]
    timestamp: datetime


class AccountPort(Protocol):
    institution_id: InstitutionId
    venue_id: VenueId
    environment: Environment

    def account_state(self, account: AccountRef) -> AccountState: ...


__all__ = ["AccountPort", "AccountState", "Environment", "VenueBalance"]
