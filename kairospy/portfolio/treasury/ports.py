from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kairospy.identity import AccountRef, VenueId


class FundingSettlementPort(Protocol):
    venue_id: VenueId

    def funding_history(self, account: AccountRef, start: datetime, end: datetime) -> tuple[object, ...]: ...

    def settlement_history(self, account: AccountRef, start: datetime, end: datetime) -> tuple[object, ...]: ...


__all__ = ["FundingSettlementPort"]
