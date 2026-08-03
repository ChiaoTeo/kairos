from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.application.usecases.execution.services.orders import ExecutionOrderService, SymbolResolver
from kairospy.application.usecases.execution.services.projections import ExecutionProjectionService
from kairospy.application.usecases.execution.services.updates import ExecutionUpdateService
from kairospy.domain.account import AccountSnapshot
from kairospy.domain.execution import ExecutionCurrentView, ExecutionFillsView, ExecutionUpdate
from kairospy.domain.intent import IntentJournal, TradeIntent
from kairospy.domain.order import OrderRequest, OrderState


@dataclass(frozen=True, slots=True)
class PlanOrderCommand:
    request: OrderRequest
    at: datetime
    reserve_currency: str | None = None
    reserve_amount: Decimal | None = None
    margin_notional: Decimal | None = None
    margin_leverage: Decimal = Decimal("1")
    margin_instrument_id: str | None = None
    venue_snapshot: AccountSnapshot | None = None


@dataclass(frozen=True, slots=True)
class SubmitOrderCommand:
    order_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class CancelOrderCommand:
    order_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionApplication:
    """Stable application API for the execution bounded context."""

    _orders: ExecutionOrderService
    _updates: ExecutionUpdateService
    _projections: ExecutionProjectionService
    _intent_execution: object | None = None

    @classmethod
    def compose(
        cls,
        coordinator: object,
        *,
        order_execution: object | None = None,
        symbol_resolver: SymbolResolver | None = None,
        intents: IntentJournal | None = None,
        fills_source: object | None = None,
        intent_execution: object | None = None,
    ) -> "ExecutionApplication":
        return cls(
            ExecutionOrderService(
                coordinator,
                order_execution=order_execution,
                symbol_resolver=symbol_resolver,
            ),
            ExecutionUpdateService(coordinator, intents=intents),
            ExecutionProjectionService(coordinator, fills_source=fills_source),
            intent_execution,
        )

    def plan_order(self, command: PlanOrderCommand) -> OrderState:
        return self._orders.plan(
            command.request,
            reserve_currency=command.reserve_currency,
            reserve_amount=command.reserve_amount,
            margin_notional=command.margin_notional,
            margin_leverage=command.margin_leverage,
            margin_instrument_id=command.margin_instrument_id,
            venue_snapshot=command.venue_snapshot,
            at=command.at,
        )

    def submit_order(self, command: SubmitOrderCommand) -> OrderState:
        return self._orders.submit(command.order_id, at=command.at)

    def cancel_order(self, command: CancelOrderCommand) -> OrderState:
        return self._orders.cancel(command.order_id, at=command.at)

    def apply_update(self, update: ExecutionUpdate) -> OrderState:
        return self._updates.apply(update)

    def current_view(self) -> ExecutionCurrentView:
        return self._projections.current_view()

    def fills_view(self) -> ExecutionFillsView:
        return self._projections.fills_view()

    def submit_intent(self, intent: TradeIntent, context: object) -> object:
        if self._intent_execution is None:
            raise RuntimeError("execution application has no intent execution port")
        return self._intent_execution.submit_intent(intent, context)

    def runtime_adapters(self) -> tuple[ExecutionUpdateService, ExecutionProjectionService]:
        """Assembly-only access for the business runtime adapter."""
        return self._updates, self._projections

__all__ = [
    "CancelOrderCommand",
    "ExecutionApplication",
    "PlanOrderCommand",
    "SubmitOrderCommand",
]
