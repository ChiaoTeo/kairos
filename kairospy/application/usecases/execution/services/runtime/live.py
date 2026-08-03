from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.application.support.runtime.domain.accounts import RuntimeAccountDirectory
from kairospy.application.usecases.execution.services.intents import ExecutionIntentService
from kairospy.application.usecases.execution.services.orders import ExecutionOrderService, SymbolResolver, margin_plan_args
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.domain.account import AccountBookRef, AccountContext, AccountSnapshot
from kairospy.domain.intent import TradeIntent
from kairospy.domain.order import OrderEvent, OrderEventKind, OrderRequest, OrderState


@dataclass(frozen=True, slots=True)
class LiveExecutionService:
    coordinator: object
    account: AccountContext | None = None
    order_execution: object | None = None
    symbol_resolver: SymbolResolver | None = None
    account_state: object | None = None
    safety_policy: ExecutionSafetyPolicy | None = None
    order_params: Mapping[str, object] | None = None
    directory: RuntimeAccountDirectory | None = None
    routes: tuple[object, ...] = ()

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def submit_intent(self, intent: TradeIntent, context: object) -> bool:
        if getattr(context, "now", None) is None:
            return False
        account = self._resolve_account(intent)
        if account is not None and not self._can_trade(account.book):
            ExecutionIntentService().reject(context, intent, _not_tradable_reason(account.book))  # type: ignore[arg-type]
            return True
        selected = self._require_account(account)
        order_params = self._order_params(selected.book)
        intents = ExecutionIntentService()
        current = self.coordinator.ledger.positions(selected.book).get(str(intent.instrument_id), Decimal("0"))
        plan = intents.plan_target_position_order(
            intent,
            context,  # type: ignore[arg-type]
            account=selected,
            current_quantity=current,
            order_id=f"{intent.intent_id}-live-order",
        )
        if plan is None:
            return True

        request = plan.request
        safety_reason = (self.safety_policy or ExecutionSafetyPolicy()).reject_reason(request)
        if safety_reason:
            intents.reject(context, intent, safety_reason, order_ids=(request.order_id,))  # type: ignore[arg-type]
            return True

        orders = self._orders()
        state = orders.plan(
            request,
            venue_snapshot=self._snapshot(selected.book),
            at=context.now,  # type: ignore[attr-defined]
            **margin_plan_args(order_params, request.instrument_id),
        )
        intents.planned(context, intent, order_ids=(state.order_id,))  # type: ignore[arg-type]
        if state.status.value == "rejected":
            intents.fail(context, intent, state.reason, order_ids=(state.order_id,))  # type: ignore[arg-type]
            return True

        state = orders.submit(
            state.request.order_id,
            at=context.now,  # type: ignore[attr-defined]
            params=order_params,
        )
        if state.status.value in {"rejected", "unknown"}:
            intents.fail(context, intent, state.reason, order_ids=(state.order_id,))  # type: ignore[arg-type]
        else:
            intents.ordering(context, intent, order_ids=(state.order_id,))  # type: ignore[arg-type]
        return True

    def plan_order(
        self,
        request: OrderRequest,
        *,
        reserve_currency: str | None = None,
        reserve_amount: Decimal | None = None,
        margin_notional: Decimal | None = None,
        margin_leverage: Decimal = Decimal("1"),
        margin_instrument_id: str | None = None,
        venue_snapshot: AccountSnapshot | None = None,
        at: datetime,
    ) -> OrderState:
        if not self._can_trade(request.context.book):
            state = self.coordinator.orders.plan(request)
            self.coordinator.orders.record(OrderEvent(state.order_id, OrderEventKind.REJECTED, at, reason=_not_tradable_reason(request.context.book)))
            return self.coordinator.orders.get(state.order_id)
        return self._orders().plan(
            request,
            reserve_currency=reserve_currency,
            reserve_amount=reserve_amount,
            margin_notional=margin_notional,
            margin_leverage=margin_leverage,
            margin_instrument_id=margin_instrument_id,
            venue_snapshot=venue_snapshot,
            at=at,
        )

    def submit_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.orders.get(order_id)
        if not self._can_trade(state.request.context.book):
            return self.coordinator.orders.record(
                OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=_not_tradable_reason(state.request.context.book))
            )
        return self._orders().submit(order_id, at=at, params=params)

    def cancel_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.orders.get(order_id)
        if not self._can_trade(state.request.context.book):
            return self.coordinator.orders.record(
                OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=_not_tradable_reason(state.request.context.book))
            )
        return self._orders().cancel(order_id, at=at, params=params)

    def _orders(self) -> ExecutionOrderService:
        return ExecutionOrderService(
            self.coordinator,
            order_execution=self.order_execution,
            symbol_resolver=self.symbol_resolver,
        )

    def _require_account(self, account: AccountContext | None = None) -> AccountContext:
        selected = account or self.account
        if selected is None:
            raise RuntimeError("live execution service requires an account before it can execute intents")
        return selected

    def _resolve_account(self, intent: TradeIntent) -> AccountContext | None:
        if self.directory is None:
            return self.account
        return self.directory.resolve_context(
            account_id=getattr(intent, "account_id", None),
            account_index=getattr(intent, "account_index", None),
            book=getattr(intent, "account_book", None),
            default=self.account,
        )

    def _snapshot(self, account: AccountBookRef) -> AccountSnapshot | None:
        if self.account_state is None:
            return None
        return self.account_state.snapshot(account)

    def _order_params(self, account: AccountBookRef) -> Mapping[str, object] | None:
        route = self._route(account)
        values = {**dict(route.order_params if route is not None else {}), **dict(self.order_params or {})}
        return values or None

    def _route(self, account: AccountBookRef) -> object | None:
        for route in self.routes:
            if route.book == account:
                return route
        return None

    def _can_trade(self, account: AccountBookRef) -> bool:
        route = self._route(account)
        return True if route is None else route.can_trade

def _not_tradable_reason(account: AccountBookRef) -> str:
    return f"account {account.value} is not tradable with the selected credential"


__all__ = ["LiveExecutionService"]
