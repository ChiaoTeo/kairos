"""Business-side ports consumed by the account usecase.

These ports describe account capabilities, not physical integration
connections.  Composition adapts integration connections to them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from kairospy.domain.account import AccountBalance, AccountSegment, AccountCapability, AccountRuntimeContext, AccountFeeSchedule, AccountMarketProfile, AccountSnapshot, AccountState, OpenOrderSnapshot, PositionSnapshot, AssetCode
from kairospy.domain.reference import MarketRef
from kairospy.domain.order import OrderRequest, OrderState


@dataclass(frozen=True, slots=True)
class AccountSession:
    session_id: str
    account: AccountSegment
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
    context: AccountRuntimeContext
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
    context: AccountRuntimeContext
    observed_at: datetime
    symbol: str | None = None
    fetch_orders: bool = True


class AccountReadPort(Protocol):
    def read_account(self, request: AccountReadRequest) -> AccountSnapshot: ...


class AccountAssetReader(Protocol):
    def read_assets(self, request: AccountReadRequest) -> tuple[AccountBalance, ...]: ...


class AccountPositionReader(Protocol):
    def read_positions(self, request: AccountReadRequest) -> tuple[PositionSnapshot, ...]: ...


class AccountOrderExecutor(Protocol):
    """Minimal order capability for a single account segment."""

    def submit_order(self, request: OrderRequest) -> OrderState: ...

    def cancel_order(self, order_id: str, *, segment: AccountSegment) -> OrderState: ...


@dataclass(frozen=True, slots=True)
class AccountTransferRequest:
    source: AccountSegment
    asset: AssetCode | str
    amount: Decimal
    destination: AccountSegment | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", self.asset if isinstance(self.asset, AssetCode) else AssetCode(self.asset))
        if self.amount <= 0:
            raise ValueError("transfer amount must be positive")


@dataclass(frozen=True, slots=True)
class AccountTransferResult:
    request: AccountTransferRequest
    accepted: bool
    reference_id: str | None = None
    reason: str = ""


class AccountTransferService(Protocol):
    def transfer(self, request: AccountTransferRequest) -> AccountTransferResult: ...


class AccountReadMode(StrEnum):
    """How an account query obtains its state."""

    CACHED = "cached"
    REFRESH = "refresh"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class AccountQueryRequest:
    """Query the state owned by the account actor.

    The request deliberately contains no broker/vendor parameters.  Broker
    routing and refresh options belong to composition and the account port.
    """

    account: AccountSegment | None = None
    mode: AccountReadMode = AccountReadMode.CACHED
    max_age_seconds: int | None = None
    now: datetime | None = None

    def __post_init__(self) -> None:
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise ValueError("account query max_age_seconds cannot be negative")
        if self.now is not None and self.now.tzinfo is None:
            raise ValueError("account query timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountRefreshRequest:
    """Request a broker refresh through the account actor."""

    account: AccountSegment | None = None
    symbol: str | None = None
    fetch_orders: bool = True
    at: datetime | None = None

    def __post_init__(self) -> None:
        if self.at is not None and self.at.tzinfo is None:
            raise ValueError("account refresh timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountMarketProfileRequest:
    context: AccountRuntimeContext
    market: MarketRef
    observed_at: datetime


class AccountMarketProfilePort(Protocol):
    """Minimal venue port consumed by the account usecase."""

    def read_market_profile(self, request: AccountMarketProfileRequest) -> AccountMarketProfile: ...


class AccountEventPort(Protocol):
    def account_snapshots(
        self,
        context: AccountRuntimeContext,
        *,
        open_orders: tuple[OpenOrderSnapshot, ...] = (),
    ) -> AsyncIterator[AccountSnapshot]: ...


class AccountSnapshotStore(Protocol):
    def update_snapshot(self, snapshot: AccountSnapshot) -> None: ...


class AccountRuntimeStateReader(Protocol):
    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None: ...
    def state(self, account: AccountSegment | None = None, *, max_snapshot_age_seconds: int | None = None, now: datetime | None = None) -> AccountState | None: ...


class AccountCatalogReader(Protocol):
    def accounts(self) -> tuple[AccountRuntimeContext, ...]: ...
    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]: ...
    def fees(self, account: AccountSegment | None = None) -> tuple[AccountFeeSchedule, ...]: ...
    def market_profiles(self, account: AccountSegment | None = None) -> tuple[AccountMarketProfile, ...]: ...


__all__ = [
    "AccountEventPort",
    "AccountLoginPort",
    "AccountLoginRequest",
    "AccountLoginResult",
    "AccountReadPort",
    "AccountAssetReader",
    "AccountOrderExecutor",
    "AccountPositionReader",
    "AccountTransferService",
    "AccountTransferRequest",
    "AccountTransferResult",
    "AccountReadRequest",
    "AccountReadMode",
    "AccountQueryRequest",
    "AccountRefreshRequest",
    "AccountMarketProfilePort",
    "AccountMarketProfileRequest",
    "AccountSession",
    "AccountSnapshotStore",
    "AccountRuntimeStateReader",
    "AccountCatalogReader",
]
