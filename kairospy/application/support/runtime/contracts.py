from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, TypeAlias

from kairospy.application.support.runtime.events import RuntimeEnvelope, RuntimeIncident
from kairospy.application.usecases.execution import SimulatedFill
from kairospy.core.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountState
from kairospy.core.execution import ExecutionIntentContext, ExecutionUpdate
from kairospy.core.intent import TradeIntent
from kairospy.core.market import MarketEvent
from kairospy.core.reference import LifecycleEvent, MarketResolver, ReferenceCatalog

from kairospy.application.usecases.market.subscriptions import DataSubscription, MarketDataSubscriptionSpec


AccountRuntimePayload: TypeAlias = AccountSnapshot | AccountState | RuntimeIncident
AccountRuntimeEnvelope: TypeAlias = RuntimeEnvelope[AccountRuntimePayload]
ExecutionRuntimePayload: TypeAlias = ExecutionUpdate
ExecutionRuntimeEnvelope: TypeAlias = RuntimeEnvelope[ExecutionRuntimePayload]
MarketRuntimePayload: TypeAlias = MarketEvent
MarketRuntimeEnvelope: TypeAlias = RuntimeEnvelope[MarketRuntimePayload]
ReferenceRuntimePayload: TypeAlias = LifecycleEvent | ReferenceCatalog
ReferenceRuntimeEnvelope: TypeAlias = RuntimeEnvelope[ReferenceRuntimePayload]


class MarketRuntime(Protocol):
    def events(self) -> AsyncIterator[MarketRuntimeEnvelope]: ...
    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription: ...
    def unsubscribe(self, subscription: DataSubscription | str) -> None: ...
    def subscriptions(self) -> tuple[DataSubscription, ...]: ...


class AccountRuntime(Protocol):
    def events(self) -> AsyncIterator[AccountRuntimeEnvelope]: ...
    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None: ...
    def state(self, account: AccountBookRef | None = None) -> AccountState | None: ...


class AccountCatalog(Protocol):
    def accounts(self) -> tuple[AccountContext, ...]: ...
    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]: ...
    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]: ...


class ExecutionRuntime(Protocol):
    def events(self) -> AsyncIterator[ExecutionRuntimeEnvelope]: ...
    def submit_intent(self, intent: TradeIntent, context: ExecutionIntentContext) -> bool | SimulatedFill | None: ...


class ReferenceRuntime(Protocol):
    def events(self) -> AsyncIterator[ReferenceRuntimeEnvelope]: ...
    def catalog(self) -> ReferenceCatalog: ...
    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver: ...
    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]: ...


__all__ = [
    "AccountCatalog", "AccountRuntime", "AccountRuntimeEnvelope", "AccountRuntimePayload",
    "ExecutionRuntime", "ExecutionRuntimeEnvelope", "ExecutionRuntimePayload",
    "MarketRuntime", "MarketRuntimeEnvelope", "MarketRuntimePayload",
    "ReferenceRuntime", "ReferenceRuntimeEnvelope", "ReferenceRuntimePayload",
]
