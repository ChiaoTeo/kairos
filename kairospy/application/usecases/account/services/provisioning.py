from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.account import AccountBookKind, AccountBookRef, AccountCapability, AccountFeeSchedule

from .authorization import AccountAuthorizationService, AccountTradeAuthorizationRequest


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
        authorization = AccountAuthorizationService(str(book.broker)).authorize_trade(
            AccountTradeAuthorizationRequest(
                book,
                trade_enabled=trade_enabled,
                has_trade_credential=has_trade_credential,
            )
        )
        return AccountCapability(
            book,
            can_trade=authorization.allowed,
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
