"""Execution application resource ports."""

from __future__ import annotations

from typing import Protocol

from kairospy.domain.account import AccountBookRef


class OrderCommandResources(Protocol):
    def account_query_access(self, book: AccountBookRef, driver_name: object, *, credential: str | None = None) -> object: ...
    def execution_access(self, book: AccountBookRef, driver_name: object, *, credential: str | None = None) -> object: ...


__all__ = ["OrderCommandResources"]
