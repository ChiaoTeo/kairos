"""ExternalAccount Actor assembly for account, execution and intent capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.application.runtime import (
    RuntimeAccountService,
    RuntimeAccountViewProjectionService,
    account_projection,
)
from kairospy.application.usecases.account.application.runtime_capability import AccountRuntimeApplication
from kairospy.application.usecases.account.application.runtime_capability import AccountRuntimeCapability
from kairospy.application.usecases.account.application.snapshots import AccountSnapshotService, AccountSnapshotStore
from kairospy.application.usecases.execution.application.component import ExecutionApplication
from kairospy.application.usecases.execution.application.runtime import (
    RuntimeExecutionService,
    TradingRuntimeExecutionService,
    execution_runtime_adapters,
)
from kairospy.application.usecases.execution.application.runtime import ExecutionCoordinator
from kairospy.application.actor.account.application.ports import ExecutionEventSource
from kairospy.application.usecases.risk.application.budget import RiskApplication
from kairospy.domain.intent import IntentJournal
from kairospy.domain.order import OrderSide


class AccountFillObservation(Protocol):
    order_id: str
    intent_id: str
    instrument_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    occurred_at: datetime


class AccountFillSource(Protocol):
    fills: tuple[AccountFillObservation, ...]


class ExecutionComponentSource(Protocol):
    coordinator: ExecutionCoordinator | None
    execution_coordinator: ExecutionCoordinator | None


class AccountAssemblyComponentSource(Protocol):
    account_catalog: AccountRuntimeCapability | None
    account: AccountRuntimeCapability | None
    execution: ExecutionComponentSource | None


@dataclass(frozen=True, slots=True)
class AccountActorDependencies:
    intents: IntentJournal
    account_service: AccountRuntimeApplication | None = None
    account_snapshot_store: AccountSnapshotStore | None = None
    account: AccountRuntimeCapability | None = None
    account_catalog: AccountRuntimeCapability | None = None
    account_directory: AccountDirectory | None = None
    trading_execution: ExecutionEventSource | None = None
    execution_coordinator: ExecutionCoordinator | None = None
    fills_source: AccountFillSource | None = None
    risk: RiskApplication | None = None


@dataclass(frozen=True, slots=True)
class AccountActorCapabilities:
    account_application: AccountRuntimeApplication | None = None
    execution_application: ExecutionApplication | None = None
    account: RuntimeAccountService | None = None
    execution: RuntimeExecutionService | None = None


def compose_account_capabilities(dependencies: AccountActorDependencies) -> AccountActorCapabilities:
    """Compose the usecases held by the ExternalAccount Actor."""
    execution_application = (
        None
        if dependencies.execution_coordinator is None
        else ExecutionApplication.compose(
            dependencies.execution_coordinator,
            intents=dependencies.intents,
            fills_source=dependencies.fills_source,
            risk=dependencies.risk,
        )
    )
    execution_updates, execution_projection = (
        (None, None)
        if execution_application is None
        else execution_runtime_adapters(execution_application)
    )
    account_catalog = dependencies.account_service or dependencies.account_catalog
    account_directory_value = dependencies.account_directory
    if account_directory_value is None and dependencies.account_service is not None:
        account_directory_value = dependencies.account_service.directory()
    account_views = (
        None
        if dependencies.account is None
        else RuntimeAccountViewProjectionService(
            dependencies.account,
            account_catalog,
            account_directory_value,
        )
    )
    account_projector = account_projection(
        dependencies.account,
        account_catalog,
        getattr(dependencies.execution_coordinator, "ledger", None),
    )
    account_snapshots = AccountSnapshotService.from_store(dependencies.account_snapshot_store)
    account = (
        None
        if account_views is None and account_projector is None and account_snapshots is None
        else RuntimeAccountService(
            snapshots=account_snapshots,
            views=account_views,
            projection=account_projector,
        )
    )
    trading = (
        None
        if execution_projection is None
        and execution_updates is None
        and dependencies.trading_execution is None
        else TradingRuntimeExecutionService(
            port=dependencies.trading_execution,
            updates=execution_updates,
            projection=execution_projection,
        )
    )
    return AccountActorCapabilities(
        account_application=dependencies.account_service,
        execution_application=execution_application,
        account=account,
        execution=None if trading is None else RuntimeExecutionService(trading=trading),
    )


def build_account_application(source: AccountRuntimeCapability | None) -> AccountRuntimeApplication | None:
    return None if source is None else AccountRuntimeApplication(runtime=source)


def account_directory(components: AccountAssemblyComponentSource) -> AccountDirectory | None:
    for candidate in (
        getattr(components, "account_catalog", None),
        getattr(components, "account", None),
    ):
        directory = getattr(candidate, "directory", None)
        if callable(directory):
            return directory()
    return None


def execution_coordinator(components: AccountAssemblyComponentSource) -> ExecutionCoordinator | None:
    execution = getattr(components, "execution", None)
    for name in ("coordinator", "execution_coordinator"):
        value = getattr(execution, name, None)
        if value is not None:
            return value
    return None


__all__ = [
    "AccountActorCapabilities",
    "AccountActorDependencies",
    "AccountAssemblyComponentSource",
    "AccountFillObservation",
    "AccountFillSource",
    "ExecutionComponentSource",
    "account_directory",
    "build_account_application",
    "compose_account_capabilities",
    "execution_coordinator",
]
