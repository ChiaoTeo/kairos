from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from kairospy.domain.account import (
    AccountBalance,
    AccountBookRef,
    AccountFeeSchedule,
    AccountScopeReader,
    AccountViewReader,
    AccountViewSource,
    PositionSnapshot,
)
from kairospy.domain.reference import MarketRef


@dataclass(frozen=True, slots=True)
class AccountQueryService:
    """Semantic account queries backed by a read-only view source.

    The source may be owned by runtime, persistence, or a test fixture. The
    account use case owns the meaning of the query; runtime only owns the
    lifecycle of the underlying view store.
    """

    source: AccountViewSource

    @property
    def reader(self) -> AccountViewReader:
        return AccountViewReader(self.source)

    def account(self, key: str | int) -> AccountScopeReader:
        return self.reader.account(key)

    def has_account(self, key: str | int) -> bool:
        return self.reader.has_account(key)

    def current(self, key: str | None = None) -> object:
        if key is None:
            return self.reader.current()
        try:
            return self.reader.current(key)
        except KeyError:
            currents = tuple(item for item in self.source.envelopes() if str(item).startswith("account.current."))
            if len(currents) == 1:
                return self.source.require(currents[0])
            raise

    def detail(self, key: str | None = None) -> object:
        return self.reader.detail(key)

    def book(self, key: str) -> object:
        return self.reader.book(key)

    def balances(self, *, account: str | None = None) -> tuple[AccountBalance, ...]:
        return tuple(_field(self._selected_current(account), "balances") or ())

    def balance(self, currency: str, *, account: str | None = None) -> AccountBalance | None:
        return next((item for item in self.balances(account=account) if _field(item, "currency") == currency), None)

    def positions(self, *, account: str | None = None) -> tuple[PositionSnapshot, ...]:
        return tuple(_field(self._selected_current(account), "positions") or ())

    def position(self, instrument: object, *, account: str | None = None) -> PositionSnapshot | None:
        instrument_id = str(instrument)
        return next((item for item in self.positions(account=account) if str(_field(item, "instrument_id")) == instrument_id), None)

    def open_orders(self, *, account: str | None = None) -> tuple[object, ...]:
        return tuple(_field(self._selected_current(account), "open_orders") or ())

    def pending_orders(self, *, account: str | None = None) -> tuple[object, ...]:
        return tuple(_field(self._selected_current(account), "pending_orders") or ())

    def fees(self, *, account: str | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self.reader.fees(account=account)

    def market_profile(self, account: AccountBookRef, market: MarketRef) -> object | None:
        return self.reader.market_profile(account, market)

    def _selected_current(self, account: str | None) -> object:
        return self.current(account)


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = ["AccountQueryService"]
