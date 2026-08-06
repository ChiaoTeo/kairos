from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from kairospy.domain.account import (
    AccountBalance,
    AccountCurrentView,
    AccountDetailView,
    AccountMarketProfile,
    AccountSegment,
    AccountFeeSchedule,
    ExternalAccountReader,
    AccountViewReader,
    AccountViewSource,
    OpenOrderSnapshot,
    PositionSnapshot,
    AssetCode,
)
from kairospy.domain.order import OrderState
from kairospy.domain.reference import MarketRef


@dataclass(frozen=True, slots=True)
class AccountViewQueryService:
    """Semantic account queries backed by a read-only view source.

    The source may be owned by runtime, persistence, or a test fixture. The
    account use case owns the meaning of the query; runtime only owns the
    lifecycle of the underlying view store.
    """

    source: AccountViewSource

    @property
    def reader(self) -> AccountViewReader:
        return AccountViewReader(self.source)

    def account(self, key: str | int) -> ExternalAccountReader:
        return self.reader.account(key)

    def has_account(self, key: str | int) -> bool:
        return self.reader.has_account(key)

    def view(self, key: str | None = None) -> AccountCurrentView:
        if key is None:
            return self.reader.view()
        try:
            return self.reader.view(key)
        except KeyError:
            currents = tuple(item for item in self.source.envelopes() if str(item).startswith("account.current."))
            if len(currents) == 1:
                return cast(AccountCurrentView, self.source.require(currents[0]))
            raise

    def only(self) -> AccountCurrentView:
        return self.reader.only()

    def detail(self, key: str | None = None) -> AccountDetailView:
        return self.reader.detail(key)

    def segment(self, key: str) -> ExternalAccountReader:
        return self.reader.segment(key)

    def balances(self, *, account: str | None = None) -> tuple[AccountBalance, ...]:
        return self._selected_view(account).balances

    def balance(self, currency: AssetCode | str, *, account: str | None = None) -> AccountBalance | None:
        return next((item for item in self.balances(account=account) if item.currency == currency), None)

    def positions(self, *, account: str | None = None) -> tuple[PositionSnapshot, ...]:
        return self._selected_view(account).positions

    def position(self, instrument: str, *, account: str | None = None) -> PositionSnapshot | None:
        instrument_id = str(instrument)
        return next((item for item in self.positions(account=account) if str(item.instrument_id) == instrument_id), None)

    def open_orders(self, *, account: str | None = None) -> tuple[OpenOrderSnapshot, ...]:
        return self._selected_view(account).open_orders

    def pending_orders(self, *, account: str | None = None) -> tuple[OrderState, ...]:
        return self._selected_view(account).pending_orders

    def fees(self, *, account: str | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self.reader.fees(account=account)

    def market_profile(self, account: AccountSegment, market: MarketRef) -> AccountMarketProfile | None:
        return self.reader.market_profile(account, market)

    def _selected_view(self, account: str | None) -> AccountCurrentView:
        return self.view(account)


__all__ = ["AccountViewQueryService"]
