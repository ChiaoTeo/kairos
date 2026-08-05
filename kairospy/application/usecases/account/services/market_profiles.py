"""ExternalAccount market profile usecase implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kairospy.application.usecases.account.protocol import (
    AccountMarketProfilePort,
    AccountMarketProfileRequest,
)
from kairospy.domain.account import AccountSegment, AccountRuntimeContext, AccountMarketProfile
from kairospy.domain.reference import MarketRef


@dataclass(frozen=True, slots=True)
class AccountMarketProfileService:
    """Orchestrates one account-market metadata read; it owns no cache."""

    port: AccountMarketProfilePort

    def read(
        self,
        account: AccountRuntimeContext,
        market: MarketRef,
        *,
        at: datetime | None = None,
    ) -> AccountMarketProfile:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("account market profile timestamp must be timezone-aware")
        profile = self.port.read_market_profile(
            AccountMarketProfileRequest(account, market, observed_at)
        )
        if profile.account != account or profile.market != market:
            raise ValueError("account market profile response does not match request")
        return profile


def account_context_for(
    accounts: tuple[AccountRuntimeContext, ...],
    account: AccountSegment,
) -> AccountRuntimeContext:
    for context in accounts:
        if context.segment == account:
            return context
    raise KeyError(f"unknown account segment: {account.value}")


__all__ = ["AccountMarketProfileService", "account_context_for"]
