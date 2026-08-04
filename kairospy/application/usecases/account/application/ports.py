"""Minimal resource ports consumed by system-facing facades."""

from __future__ import annotations

from typing import Protocol

from kairospy.domain.account import AccountBookRef


class AccountCommandResources(Protocol):
    def private_account_access(self, book: AccountBookRef, driver_name: object, *, credential: str | None = None) -> object: ...
    def account_read_access(self, book: AccountBookRef, driver_name: object, *, credential: str | None = None) -> object: ...
    def account_query_access(self, book: AccountBookRef, driver_name: object, *, credential: str | None = None) -> object: ...


__all__ = ["AccountCommandResources"]
