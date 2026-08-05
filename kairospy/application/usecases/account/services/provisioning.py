from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.account import AccountModel, AccountSegment, AccountCapability, AccountFeeSchedule

from kairospy.application.usecases.account.domain.routing import AccountCapabilityPolicy


@dataclass(frozen=True, slots=True)
class AccountProvisioningService:
    def capability(
        self,
        segment: AccountSegment,
        *,
        trade_enabled: bool = True,
        has_trade_credential: bool = True,
    ) -> AccountCapability:
        can_trade = trade_enabled and has_trade_credential and AccountCapabilityPolicy(str(segment.broker)).can_trade(segment)
        return AccountCapability(
            segment,
            can_trade=can_trade,
            can_hold_assets=True,
            can_hold_position=segment.product_family is not None,
            can_borrow=segment.model in {AccountModel.MARGIN, AccountModel.CONTRACT, AccountModel.CONTRACT_UNIFIED, AccountModel.UNIFIED, AccountModel.PORTFOLIO_MARGIN},
        )

    def fee_schedule(
        self,
        segment: AccountSegment,
        *,
        fee_rate: Decimal,
    ) -> AccountFeeSchedule:
        return AccountFeeSchedule(segment, maker=fee_rate, taker=fee_rate)


__all__ = ["AccountProvisioningService"]
