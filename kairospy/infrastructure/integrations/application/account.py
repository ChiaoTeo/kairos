from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kairospy.domain.account import AccountContext, AccountSnapshot, OpenOrderSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection


@dataclass(frozen=True, slots=True)
class ConnectionAccountBootstrapRequest:
    context: AccountContext
    observed_at: datetime
    symbol: str | None = None
    fetch_orders: bool = True


@dataclass(frozen=True, slots=True)
class ConnectionAccountBootstrapData:
    snapshot: AccountSnapshot


@dataclass(frozen=True, slots=True)
class ConnectionAccountStreamRequest:
    context: AccountContext
    symbol: str | None = None
    open_orders: tuple[OpenOrderSnapshot, ...] = ()


class AccountConnection(IntegrationConnection, Protocol):
    def bootstrap(self, request: ConnectionAccountBootstrapRequest) -> ConnectionAccountBootstrapData: ...


class AccountStreamConnection(IntegrationConnection, Protocol):
    def account_snapshots(self, request: ConnectionAccountStreamRequest) -> AsyncIterator[AccountSnapshot]: ...


class OrderUpdateConnection(IntegrationConnection, Protocol):
    def execution_updates(self, request: ConnectionAccountStreamRequest, *, trades_only: bool = False) -> AsyncIterator[ExecutionUpdate]: ...


__all__ = [
    "ConnectionAccountBootstrapRequest",
    "ConnectionAccountBootstrapData",
    "ConnectionAccountStreamRequest",
    "AccountConnection",
    "AccountStreamConnection",
    "OrderUpdateConnection",
]
