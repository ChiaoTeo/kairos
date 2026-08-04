from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.account import AccountBookKind, AccountBookRef, AccountCapability, AccountFeeSchedule

from kairospy.application.usecases.account.domain.routing import AccountCapabilityPolicy


@dataclass(frozen=True, slots=True)
class AccountProvisioningService:
    def capability(
        self,
        book: AccountBookRef,
        *,
        trade_enabled: bool = True,
        has_trade_credential: bool = True,
    ) -> AccountCapability:
        kind = str(book.book)
        can_trade = trade_enabled and has_trade_credential and AccountCapabilityPolicy(str(book.broker)).can_trade(book)
        return AccountCapability(
            book,
            can_trade=can_trade,
            can_hold_cash=True,
            can_hold_position=kind not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value},
            can_borrow=kind in {AccountBookKind.CROSS_MARGIN.value, AccountBookKind.ISOLATED_MARGIN.value},
        )

    def fee_schedule(
        self,
        book: AccountBookRef,
        *,
        fee_rate: Decimal,
    ) -> AccountFeeSchedule:
        return AccountFeeSchedule(book, maker=fee_rate, taker=fee_rate)


__all__ = ["AccountProvisioningService"]
