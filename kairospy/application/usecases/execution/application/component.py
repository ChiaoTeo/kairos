from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.application.usecases.execution.services.orders import ExecutionOrderService, SymbolResolver
from kairospy.application.usecases.execution.services.projections import ExecutionProjectionService
from kairospy.application.usecases.execution.services.updates import ExecutionUpdateService
from kairospy.application.usecases.execution.services.intents import ExecutionIntentService
from kairospy.application.usecases.execution.services.intents import ExecutionIntentOrderPlan
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.application.usecases.risk.application.budget import RiskApplication
from kairospy.application.usecases.risk.application.budget import RiskAssessmentRequest
from kairospy.application.usecases.risk.domain import BudgetRef, RiskMetric, RiskUsage
from kairospy.infrastructure.integrations.application.execution import OrderConnection
from kairospy.domain.account import AccountBookRef, AccountSnapshot
from kairospy.domain.account import AccountContext
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
    risk_amount: Decimal | None = None
    risk_metric: RiskMetric = RiskMetric.NOTIONAL


@dataclass(frozen=True, slots=True)
class SubmitOrderCommand:
    order_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class CancelOrderCommand:
    order_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ExecuteIntentCommand:
    intent: TradeIntent
    context: object
    account: AccountContext
    current_quantity: Decimal
    account_snapshot: AccountSnapshot | None = None
    order_options: Mapping[str, object] | None = None
    safety_policy: ExecutionSafetyPolicy | None = None
    reserve_currency: str | None = None
    reserve_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ExecutionIntentPreparation:
    """Order and risk facts prepared before an external risk decision."""

    command: ExecuteIntentCommand
    plan: ExecutionIntentOrderPlan | None
    risk_request: RiskAssessmentRequest | None
    risk_reservation_id: str | None
    risk_amount: Decimal | None
    risk_metric: RiskMetric


@dataclass(frozen=True, slots=True)
class ExecutionApplication:
    """Stable application API for the execution bounded context."""

    _orders: ExecutionOrderService
    _updates: ExecutionUpdateService
    _projections: ExecutionProjectionService

    @classmethod
    def compose(
        cls,
        coordinator: ExecutionCoordinator,
        *,
        order_connection: OrderConnection | Mapping[AccountBookRef, OrderConnection] | None = None,
        symbol_resolver: SymbolResolver | None = None,
        intents: IntentJournal | None = None,
        fills_source: object | None = None,
        risk: RiskApplication | None = None,
    ) -> "ExecutionApplication":
        if risk is not None:
            coordinator.risk = risk
        return cls(
            ExecutionOrderService(
                coordinator,
                order_connection=order_connection,
                symbol_resolver=symbol_resolver,
            ),
            ExecutionUpdateService(coordinator, intents=intents),
            ExecutionProjectionService(coordinator, fills_source=fills_source),
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
            risk_amount=command.risk_amount if command.risk_amount is not None else _risk_amount(command.request, None),
            risk_metric=command.risk_metric,
            at=command.at,
        )

    def submit_order(self, command: SubmitOrderCommand) -> OrderState:
        return self._orders.submit(command.order_id, at=command.at)

    def cancel_order(self, command: CancelOrderCommand) -> OrderState:
        return self._orders.cancel(command.order_id, at=command.at)

    def execute_intent(self, command: ExecuteIntentCommand) -> OrderState | None:
        preparation = self.prepare_intent(command, record_events=True)
        if preparation.plan is None:
            return None
        return self.execute_prepared_intent(
            preparation,
            risk_reserved=False,
            events_already_recorded=True,
        )

    def prepare_intent(
        self,
        command: ExecuteIntentCommand,
        *,
        record_events: bool = False,
        check_safety: bool = True,
    ) -> ExecutionIntentPreparation:
        intents = ExecutionIntentService()
        now = getattr(command.context, "now", None)
        if now is None:
            return ExecutionIntentPreparation(command, None, None, None, None, RiskMetric.NOTIONAL)
        plan = intents.plan_target_position_order(
            command.intent,
            command.context,
            account=command.account,
            current_quantity=command.current_quantity,
            order_id=f"{command.intent.intent_id}-order",
            record_events=record_events,
        )
        if plan is None:
            return ExecutionIntentPreparation(command, None, None, None, None, RiskMetric.NOTIONAL)
        reason = "" if not check_safety else (command.safety_policy or ExecutionSafetyPolicy()).reject_reason(plan.request)
        if reason:
            if record_events:
                intents.reject(command.context, command.intent, reason, order_ids=(plan.request.order_id,))
            return ExecutionIntentPreparation(command, None, None, None, None, RiskMetric.NOTIONAL)
        risk_metric, risk_amount = _risk_facts(plan.request, command)
        return ExecutionIntentPreparation(
            command,
            plan,
            _risk_request(plan.request, risk_metric, risk_amount, at=now),
            plan.request.reservation_id or plan.request.order_id,
            risk_amount,
            risk_metric,
        )

    def execute_prepared_intent(
        self,
        preparation: ExecutionIntentPreparation,
        *,
        risk_reserved: bool,
        events_already_recorded: bool = False,
    ) -> OrderState | None:
        if preparation.plan is None:
            return None
        command = preparation.command
        intents = ExecutionIntentService()
        plan = preparation.plan
        if not events_already_recorded:
            intents.accept(command.context, command.intent)
        order = self._orders.plan(
            plan.request,
            reserve_currency=command.reserve_currency,
            reserve_amount=command.reserve_amount,
            venue_snapshot=command.account_snapshot,
            risk_amount=None if risk_reserved else preparation.risk_amount,
            risk_metric=preparation.risk_metric,
            at=command.context.now,
            **_margin_args(command.order_options, plan.request.instrument_id),
        )
        intents.planned(command.context, command.intent, order_ids=(order.order_id,))
        if order.status.value == "rejected":
            intents.fail(command.context, command.intent, order.reason, order_ids=(order.order_id,))
            return order
        submitted = self._orders.submit(
            order.order_id,
            at=command.context.now,
            params=command.order_options,
        )
        if submitted.status.value in {"rejected", "unknown"}:
            intents.fail(command.context, command.intent, submitted.reason, order_ids=(submitted.order_id,))
        elif submitted.status.terminal:
            intents.satisfy(command.context, command.intent, order_ids=(submitted.order_id,))
        else:
            intents.ordering(command.context, command.intent, order_ids=(submitted.order_id,))
        return submitted

    def reject_prepared_intent(self, preparation: ExecutionIntentPreparation, reason: str) -> None:
        if preparation.plan is None:
            return
        ExecutionIntentService().reject(
            preparation.command.context,
            preparation.command.intent,
            reason,
            order_ids=(preparation.plan.request.order_id,),
        )

    def apply_update(self, update: ExecutionUpdate) -> OrderState:
        return self._updates.apply(update)

    def current_view(self) -> ExecutionCurrentView:
        return self._projections.current_view()

    def fills_view(self) -> ExecutionFillsView:
        return self._projections.fills_view()

    def orders(self, account: AccountBookRef | None = None) -> tuple[OrderState, ...]:
        states = self._orders.coordinator.orders.states
        if account is None:
            return states
        return tuple(state for state in states if state.request.context.book == account)

    def current_quantity(self, account: AccountBookRef, instrument_id: object) -> Decimal:
        return self._orders.coordinator.ledger.positions(account).get(str(instrument_id), Decimal("0"))

    def runtime_adapters(self) -> tuple[ExecutionUpdateService, ExecutionProjectionService]:
        """Assembly-only access for the business runtime adapter."""
        return self._updates, self._projections

__all__ = [
    "CancelOrderCommand",
    "ExecutionApplication",
    "ExecutionIntentPreparation",
    "ExecuteIntentCommand",
    "PlanOrderCommand",
    "SubmitOrderCommand",
]


def _risk_facts(request: OrderRequest, command: ExecuteIntentCommand) -> tuple[RiskMetric, Decimal | None]:
    params = command.order_options or {}
    margin_notional = params.get("marginNotional") or params.get("margin_notional")
    if margin_notional is not None:
        leverage = Decimal(str(params.get("marginLeverage") or params.get("margin_leverage") or "1"))
        return RiskMetric.MARGIN, Decimal(str(margin_notional)) / leverage
    if command.reserve_amount is not None:
        return RiskMetric.NOTIONAL, command.reserve_amount
    return RiskMetric.NOTIONAL, _risk_amount(request, command.order_options)


def _risk_request(
    request: OrderRequest,
    metric: RiskMetric,
    amount: Decimal | None,
    *,
    at: datetime,
) -> RiskAssessmentRequest:
    return RiskAssessmentRequest(
        request.order_id,
        (
            RiskUsage(
                metric,
                amount or Decimal("0"),
                (
                    BudgetRef("account", request.context.book.value),
                    BudgetRef("instrument", str(request.instrument_id)),
                ),
            ),
        ),
        at,
    )


def _margin_args(params: Mapping[str, object] | None, instrument_id: object) -> dict[str, object]:
    values = params or {}
    currency = values.get("marginCurrency") or values.get("margin_currency")
    notional = values.get("marginNotional") or values.get("margin_notional")
    if currency is None and notional is None:
        return {}
    if currency is None or notional is None:
        raise ValueError("marginCurrency and marginNotional must be supplied together")
    return {
        "reserve_currency": str(currency),
        "margin_notional": Decimal(str(notional)),
        "margin_leverage": Decimal(str(values.get("marginLeverage") or values.get("margin_leverage") or "1")),
        "margin_instrument_id": str(values.get("marginInstrumentId") or values.get("margin_instrument_id") or instrument_id),
    }


def _risk_amount(request: OrderRequest, params: Mapping[str, object] | None) -> Decimal | None:
    values = params or {}
    explicit = values.get("riskNotional") or values.get("risk_notional")
    if explicit is not None:
        return Decimal(str(explicit))
    if request.limit_price is None:
        return None
    return request.quantity * request.limit_price
