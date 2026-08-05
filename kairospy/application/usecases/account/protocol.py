"""Business-side ports consumed by the account usecase.

These ports describe account capabilities, not physical integration
connections.  Composition adapts integration connections to them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountMarketProfile, AccountSnapshot
from kairospy.domain.reference import MarketRef


@dataclass(frozen=True, slots=True)
class AccountSession:
    session_id: str
    account: AccountBookRef
    connection_ids: tuple[str, ...] = ()
    capabilities: frozenset[AccountCapability] = frozenset()
    logged_in_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("account session id is required")
        if self.logged_in_at is not None and self.logged_in_at.tzinfo is None:
            raise ValueError("account session timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountLoginRequest:
    context: AccountContext
    credential_ref: str | None = None
    connection_ids: tuple[str, ...] = ()
    observed_at: datetime | None = None


class AccountLoginPort(Protocol):
    def login(self, request: AccountLoginRequest) -> "AccountLoginResult": ...

    def logout(self, session: AccountSession) -> None: ...


@dataclass(frozen=True, slots=True)
class AccountLoginResult:
    session: AccountSession
    snapshot: AccountSnapshot | None = None


@dataclass(frozen=True, slots=True)
class AccountReadRequest:
    context: AccountContext
    observed_at: datetime
    symbol: str | None = None
    options: Mapping[str, object] | None = None
    fetch_orders: bool = True


class AccountReadPort(Protocol):
    def read_account(self, request: AccountReadRequest) -> AccountSnapshot: ...


@dataclass(frozen=True, slots=True)
class AccountMarketProfileRequest:
    context: AccountContext
    market: MarketRef
    observed_at: datetime


class AccountMarketProfilePort(Protocol):
    """Minimal venue port consumed by the account usecase."""

    def read_market_profile(self, request: AccountMarketProfileRequest) -> AccountMarketProfile: ...


class AccountEventPort(Protocol):
    def account_snapshots(
        self,
        context: AccountContext,
        *,
        open_orders: tuple[object, ...] = (),
    ) -> AsyncIterator[AccountSnapshot]: ...


class AccountSnapshotStore(Protocol):
    def update_snapshot(self, snapshot: AccountSnapshot) -> None: ...


__all__ = [
    "AccountEventPort",
    "AccountLoginPort",
    "AccountLoginRequest",
    "AccountLoginResult",
    "AccountReadPort",
    "AccountReadRequest",
    "AccountMarketProfilePort",
    "AccountMarketProfileRequest",
    "AccountSession",
    "AccountSnapshotStore",
]
