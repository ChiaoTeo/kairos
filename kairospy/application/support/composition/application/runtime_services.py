from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.runtime.domain.accounts import RuntimeAccountDirectory
from kairospy.application.usecases.account.application.runtime import RuntimeAccountService, RuntimeAccountViewProjectionService, account_projection
from kairospy.application.usecases.execution.application.runtime import RuntimeExecutionService, TradingRuntimeExecutionService, execution_runtime_adapters
from kairospy.application.usecases.market.application.runtime import RuntimeMarketProjectionService, RuntimeMarketService
from kairospy.application.usecases.reference.application.runtime import RuntimeReferenceProjectionService, RuntimeReferenceService
from kairospy.application.usecases.account.application.snapshots import AccountSnapshotService, AccountSnapshotStore
from kairospy.application.usecases.execution.application.component import ExecutionApplication
from kairospy.domain.intent import IntentJournal
from kairospy.application.support.runtime.application.launch.resources import RuntimeAssembly


@dataclass(frozen=True, slots=True)
class RuntimeServiceDependencies:
    """Already-selected usecase capabilities supplied by composition.

    The runtime assembly deliberately does not expose or type these values as
    business runtime protocols.  Live/paper/backtest selection is resolved
    before this object is built; this object is only the private hand-off into
    usecase-owned application services and projectors.
    """

    intents: IntentJournal
    data: object | None = None
    account_snapshot_store: AccountSnapshotStore | None = None
    account: object | None = None
    account_catalog: object | None = None
    account_directory: RuntimeAccountDirectory | None = None
    reference: object | None = None
    trading_execution: object | None = None
    execution_coordinator: object | None = None
    fills_source: object | None = None


@dataclass(frozen=True, slots=True)
class RuntimeApplicationServices:
    account: RuntimeAccountService | None = None
    execution: RuntimeExecutionService | None = None
    market: RuntimeMarketService | None = None
    reference: RuntimeReferenceService | None = None

    def projectors(self, *, strategy_id: str, intents: IntentJournal) -> object:
        from kairospy.application.support.composition.projectors.runtime import runtime_projectors

        return runtime_projectors(strategy_id=strategy_id, intents=intents, services=self)

    @classmethod
    def from_dependencies(cls, dependencies: RuntimeServiceDependencies) -> "RuntimeApplicationServices":
        execution_application = None if dependencies.execution_coordinator is None else ExecutionApplication.compose(dependencies.execution_coordinator, intents=dependencies.intents, fills_source=dependencies.fills_source)
        execution_updates, execution_projection = (None, None) if execution_application is None else execution_runtime_adapters(execution_application)
        account_views = None if dependencies.account is None else RuntimeAccountViewProjectionService(dependencies.account, dependencies.account_catalog, dependencies.account_directory)
        account_projector = account_projection(dependencies.account, dependencies.account_catalog, dependencies.execution_coordinator)
        account_snapshots = AccountSnapshotService.from_store(dependencies.account_snapshot_store)
        account = None if account_views is None and account_projector is None and account_snapshots is None else RuntimeAccountService(snapshots=account_snapshots, views=account_views, projection=account_projector)
        trading = None if execution_projection is None and execution_updates is None and dependencies.trading_execution is None else TradingRuntimeExecutionService(port=dependencies.trading_execution, updates=execution_updates, projection=execution_projection)
        return cls(
            account=account,
            execution=None if trading is None else RuntimeExecutionService(trading=trading),
            market=None if dependencies.data is None else RuntimeMarketService(RuntimeMarketProjectionService(dependencies.data)),
            reference=None if dependencies.reference is None else RuntimeReferenceService(RuntimeReferenceProjectionService(dependencies.reference)),
        )


def compose_runtime_assembly() -> RuntimeAssembly:
    """Build the runtime implementation bundle for a composed system."""
    from kairospy.application.support.composition.application.artifacts import launch_output
    from kairospy.application.support.composition.projectors import runtime_projectors, runtime_services_for

    return RuntimeAssembly(
        services=runtime_services_for,
        projectors=lambda strategy_id, intents, services: runtime_projectors(
            strategy_id=strategy_id,
            intents=intents,
            services=services,
        ),
        output=launch_output,
    )


__all__ = ["RuntimeApplicationServices", "RuntimeServiceDependencies", "compose_runtime_assembly"]
