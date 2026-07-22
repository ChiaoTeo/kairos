from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from kairospy.execution.ingestion import DurableExecutionIngestionService
from kairospy.execution.outbox import DurableOrderCommandService, DurableOrderDispatcher
from kairospy.execution.recovery import VenueOrderRecoveryService
from kairospy.execution.router import ExecutionRouter
from kairospy.execution.ports import ComboOrderRequest, Environment, ExecutionPort, OrderRecoveryPort, OrderRequest
from kairospy.identity import AccountRef
from kairospy.portfolio.account_ports import AccountPort
from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.portfolio.ledger import Ledger
from kairospy.reference.catalog import ReferenceCatalog
from kairospy.runtime.application import KairosApplication
from kairospy.runtime.bindings import (
    CompositeRecoveryBinding,
    DurableOutboxCommandSubmitter,
    EventSourceRunEventProvider,
    ExecutionRecoveryBinding,
)
from kairospy.runtime.clock import Clock
from kairospy.runtime.kernel import BoundRunProfile
from kairospy.runtime.live_config import LiveRuntimeBindingConfig
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

if TYPE_CHECKING:
    from kairospy.governance.kill_switch import KillSwitch


@dataclass(frozen=True, slots=True)
class LiveRuntimeComponents:
    """Concrete live runtime components wired at the runtime owner boundary."""

    config: LiveRuntimeBindingConfig
    application: KairosApplication
    store: SQLiteRuntimeStore
    reference_catalog: ReferenceCatalog
    execution_gateway: ExecutionPort
    account_gateway: AccountPort
    accounts: tuple[AccountRef, ...] = ()
    market_event_source: object | None = None
    order_recovery_gateway: OrderRecoveryPort | None = None
    ledger: Ledger | None = None
    clock: Clock | None = None
    kill_switch: KillSwitch | None = None
    validate_order: Callable[[OrderRequest | ComboOrderRequest], None] | None = None
    dispatch_immediately: bool = True
    max_market_events: int | None = None

    def __post_init__(self) -> None:
        _require_live_environment("execution_gateway", self.execution_gateway)
        _require_live_environment("account_gateway", self.account_gateway)
        if self.order_recovery_gateway is not None:
            _require_live_environment("order_recovery_gateway", self.order_recovery_gateway)
        if self.order_recovery_gateway is not None and not self._accounts:
            raise ValueError("live order recovery binding requires at least one account")

    @property
    def _accounts(self) -> tuple[AccountRef, ...]:
        return self.accounts or self.application.accounts

    def runtime_recovery_service(self) -> object:
        return self.config.runtime_recovery_service()

    def order_recovery_service(self) -> VenueOrderRecoveryService | None:
        if self.order_recovery_gateway is None:
            return None
        return VenueOrderRecoveryService(
            self.store,
            {account: self.order_recovery_gateway for account in self._accounts},
            DurableExecutionIngestionService(
                LedgerService(self.ledger or self.store.load_ledger(), self.reference_catalog),
                self.store,
            ),
        )

    def bind(self) -> BoundRunProfile:
        from kairospy.governance.kill_switch import KillSwitch

        router = ExecutionRouter(self.reference_catalog, (self.execution_gateway,))
        kill_switch = self.kill_switch or KillSwitch((self.execution_gateway,), self.clock, self.store)
        command_service = DurableOrderCommandService(
            self.store,
            self.application,
            kill_switch,
            self.validate_order or _validate_order_shape,
            clock=self.clock,
        )
        dispatcher = DurableOrderDispatcher(self.store, router, clock=self.clock)
        recovery_handlers = [self.config.runtime_recovery_handler()]
        order_recovery = self.order_recovery_service()
        if order_recovery is not None:
            recovery_handlers.append(ExecutionRecoveryBinding(
                order_recovery,
                f"{self.config.binding_id}:order-recovery",
            ))
        return BoundRunProfile(
            self.config.to_live_profile(),
            self.config.binding_id,
            market_event_provider=(
                EventSourceRunEventProvider(
                    self.market_event_source,
                    f"{self.config.binding_id}:market-events",
                    max_events=self.max_market_events,
                )
                if self.market_event_source is not None
                else None
            ),
            command_submitter=DurableOutboxCommandSubmitter(
                command_service,
                dispatcher,
                f"{self.config.binding_id}:outbox",
                dispatch_immediately=self.dispatch_immediately,
            ),
            recovery_handler=(
                recovery_handlers[0]
                if len(recovery_handlers) == 1
                else CompositeRecoveryBinding(tuple(recovery_handlers), f"{self.config.binding_id}:recovery")
            ),
        )


def bind_live_runtime_components(components: LiveRuntimeComponents) -> BoundRunProfile:
    return components.bind()


def _validate_order_shape(request: OrderRequest | ComboOrderRequest) -> None:
    if not str(request.client_order_id).strip():
        raise ValueError("order request requires client_order_id")


def _require_live_environment(name: str, gateway: object) -> None:
    environment = getattr(gateway, "environment", None)
    if Environment(environment) is not Environment.LIVE:
        raise ValueError(f"{name} must use live environment for LiveRuntimeComponents")
