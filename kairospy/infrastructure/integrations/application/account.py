from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kairospy.domain.account import AccountContext, AccountMarketProfile, AccountSnapshot, OpenOrderSnapshot
from kairospy.domain.reference import MarketRef
from kairospy.domain.execution import ExecutionUpdate
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection


@dataclass(frozen=True, slots=True)
class ConnectionAccountReadRequest:
    context: AccountContext
    observed_at: datetime
    symbol: str | None = None
    fetch_orders: bool = True


@dataclass(frozen=True, slots=True)
class ConnectionAccountReadData:
    snapshot: AccountSnapshot


@dataclass(frozen=True, slots=True)
class ConnectionAccountMarketProfileRequest:
    context: AccountContext
    market: MarketRef
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ConnectionAccountMarketProfileData:
    profile: AccountMarketProfile


@dataclass(frozen=True, slots=True)
class ConnectionAccountStreamRequest:
    context: AccountContext
    symbol: str | None = None
    open_orders: tuple[OpenOrderSnapshot, ...] = ()


class AccountConnection(IntegrationConnection, Protocol):
    def read_account(self, request: ConnectionAccountReadRequest) -> ConnectionAccountReadData: ...

    def read_market_profile(self, request: ConnectionAccountMarketProfileRequest) -> ConnectionAccountMarketProfileData: ...


class AccountMarketProfileConnection(IntegrationConnection, Protocol):
    def read_market_profile(self, request: ConnectionAccountMarketProfileRequest) -> ConnectionAccountMarketProfileData: ...


class AccountStreamConnection(IntegrationConnection, Protocol):
    def account_snapshots(self, request: ConnectionAccountStreamRequest) -> AsyncIterator[AccountSnapshot]: ...


class OrderUpdateConnection(IntegrationConnection, Protocol):
    def execution_updates(self, request: ConnectionAccountStreamRequest, *, trades_only: bool = False) -> AsyncIterator[ExecutionUpdate]: ...


__all__ = [
    "ConnectionAccountReadRequest",
    "ConnectionAccountReadData",
    "ConnectionAccountMarketProfileData",
    "ConnectionAccountMarketProfileRequest",
    "ConnectionAccountStreamRequest",
    "AccountConnection",
    "AccountMarketProfileConnection",
    "AccountStreamConnection",
    "OrderUpdateConnection",
]
