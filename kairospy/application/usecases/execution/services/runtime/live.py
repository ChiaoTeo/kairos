from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.execution.application.component import ExecutionApplication, ExecuteIntentCommand
from kairospy.application.usecases.execution.protocol import ExecutionUpdateSource
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.application.usecases.execution.services.orders import ExecutionOrderService, SymbolResolver, margin_plan_args
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.application.support.messaging import Message
from kairospy.domain.account import AccountBookRef, AccountContext, AccountSnapshot
from kairospy.domain.intent import TradeIntent
from kairospy.domain.order import OrderEvent, OrderEventKind, OrderRequest, OrderState


@dataclass(frozen=True, slots=True)
class LiveExecutionService:
    coordinator: ExecutionCoordinator
    account: AccountContext | None = None
    order_connection: object | None = None
    symbol_resolver: SymbolResolver | None = None
    account_state: object | None = None
    safety_policy: ExecutionSafetyPolicy | None = None
    order_params: Mapping[str, object] | None = None
    directory: AccountDirectory | None = None
    routes: tuple[object, ...] = ()
    update_source: ExecutionUpdateSource | None = None
    update_symbol: str | None = None
    _event_sequence: int = 0

    async def events(self) -> AsyncIterator[Message]:
        if self.update_source is None or self.account is None:
            return
        async for update in self.update_source.events(self.account, symbol=self.update_symbol):
            self._event_sequence += 1
            yield Message(topic="execution.update", payload=update, published_at=update.observed_at, producer="execution.service", producer_sequence=self._event_sequence)

    def execute_intent(self, intent: TradeIntent, context: object) -> bool:
        if getattr(context, "now", None) is None:
            return False
        account = self._resolve_account(intent)
        selected = self._require_account(account)
        order_params = self._order_params(selected.book)
        current = self.coordinator.ledger.positions(selected.book).get(str(intent.instrument_id), Decimal("0"))
        ExecutionApplication.compose(
            self.coordinator,
            order_connection=self.order_connection,
            symbol_resolver=self.symbol_resolver,
        ).execute_intent(
            ExecuteIntentCommand(
                intent=intent,
                context=context,
                account=selected,
                current_quantity=current,
                account_snapshot=self._snapshot(selected.book),
                order_options=order_params,
                safety_policy=self.safety_policy,
            )
        )
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
        return self._orders().submit(order_id, at=at, params=params)

    def cancel_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        return self._orders().cancel(order_id, at=at, params=params)

    def _orders(self) -> ExecutionOrderService:
        return ExecutionOrderService(
            self.coordinator,
            order_connection=self.order_connection,
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

__all__ = ["LiveExecutionService"]
