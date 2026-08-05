"""Public account read-side application queries."""

from __future__ import annotations

from kairospy.application.usecases.account.services.queries import AccountQueryService as _AccountQueryService
from kairospy.domain.account import AccountScopeReader, AccountViewReader


class AccountQueryService:
    def __init__(self, source: object) -> None:
        self._service = _AccountQueryService(source)  # type: ignore[arg-type]

    @property
    def reader(self) -> AccountViewReader:
        return self._service.reader

    def __getitem__(self, key: str | int) -> AccountScopeReader:
        return self._service.account(key)

    def account(self, key: str | int) -> AccountScopeReader:
        return self._service.account(key)

    def has_account(self, key: str | int) -> bool:
        return self._service.has_account(key)

    def current(self, key: str | None = None) -> object:
        return self._service.current(key)

    def detail(self, key: str | None = None) -> object:
        return self._service.detail(key)

    def book(self, key: str) -> object:
        return self._service.book(key)

    def balances(self, *, account: str | None = None) -> tuple[object, ...]:
        return self._service.balances(account=account)

    def balance(self, currency: str, *, account: str | None = None) -> object | None:
        return self._service.balance(currency, account=account)

    def positions(self, *, account: str | None = None) -> tuple[object, ...]:
        return self._service.positions(account=account)

    def position(self, instrument: object, *, account: str | None = None) -> object | None:
        return self._service.position(instrument, account=account)

    def open_orders(self, *, account: str | None = None) -> tuple[object, ...]:
        return self._service.open_orders(account=account)

    def pending_orders(self, *, account: str | None = None) -> tuple[object, ...]:
        return self._service.pending_orders(account=account)

    def fees(self, *, account: str | None = None) -> tuple[object, ...]:
        return self._service.fees(account=account)

    def market_profile(self, account: object, market: object) -> object | None:
        return self._service.market_profile(account, market)  # type: ignore[arg-type]


__all__ = ["AccountQueryService"]
