"""Public account read-side application queries."""

from __future__ import annotations

from kairospy.application.usecases.account.services.queries import AccountViewQueryService as _AccountViewQueryService
from kairospy.domain.account import AccountBalance, AccountCurrentView, AccountDetailView, AccountFeeSchedule, AccountMarketProfile, AccountSegment, ExternalAccountReader, AccountViewReader, AccountViewSource, OpenOrderSnapshot, PositionSnapshot, AssetCode
from kairospy.domain.reference import MarketRef
from kairospy.domain.order import OrderState


class AccountViewQueryService:
    def __init__(self, source: AccountViewSource) -> None:
        self._service = _AccountViewQueryService(source)

    @property
    def reader(self) -> AccountViewReader:
        return self._service.reader

    def __getitem__(self, key: str | int) -> ExternalAccountReader:
        return self._service.account(key)

    def account(self, key: str | int) -> ExternalAccountReader:
        return self._service.account(key)

    def has_account(self, key: str | int) -> bool:
        return self._service.has_account(key)

    def current(self, key: str | None = None) -> AccountCurrentView:
        return self._service.current(key)

    def detail(self, key: str | None = None) -> AccountDetailView:
        return self._service.detail(key)

    def segment(self, key: str) -> ExternalAccountReader:
        return self._service.segment(key)

    def balances(self, *, account: str | None = None) -> tuple[AccountBalance, ...]:
        return self._service.balances(account=account)

    def balance(self, currency: AssetCode | str, *, account: str | None = None) -> AccountBalance | None:
        return self._service.balance(currency, account=account)

    def positions(self, *, account: str | None = None) -> tuple[PositionSnapshot, ...]:
        return self._service.positions(account=account)

    def position(self, instrument: str, *, account: str | None = None) -> PositionSnapshot | None:
        return self._service.position(instrument, account=account)

    def open_orders(self, *, account: str | None = None) -> tuple[OpenOrderSnapshot, ...]:
        return self._service.open_orders(account=account)

    def pending_orders(self, *, account: str | None = None) -> tuple[OrderState, ...]:
        return self._service.pending_orders(account=account)

    def fees(self, *, account: str | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self._service.fees(account=account)

    def market_profile(self, account: AccountSegment, market: MarketRef) -> AccountMarketProfile | None:
        return self._service.market_profile(account, market)


__all__ = ["AccountViewQueryService"]
