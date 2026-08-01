from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.support.launch.accounts import LaunchAccountDirectory
from kairospy.application.usecases.account.routing import AccountBookRoute, account_book_route
from kairospy.application.usecases.execution.live import OrderCancelRequest, OrderExecutionPort, OrderSubmissionRequest
from kairospy.application.support.runtime.contracts import ExecutionRuntimeEnvelope
from kairospy.core.account import AccountBookRef, AccountContext, AccountSnapshot
from kairospy.core.execution import ExecutionIntentContext
from kairospy.core.intent import IntentEvent, IntentEventKind, IntentKind, TradeIntent
from kairospy.core.order import OrderEvent, OrderEventKind, OrderRequest, OrderSide, OrderState, OrderType


class SymbolResolver(Protocol):
    def __call__(self, instrument_id: object) -> str:
        ...


class SnapshotProvider(Protocol):
    def __call__(self) -> AccountSnapshot | None:
        ...


class FlexibleSnapshotProvider(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> AccountSnapshot | None:
        ...


@dataclass(frozen=True, slots=True)
class LiveTradingSafetyPolicy:
    trading_enabled: bool = False
    require_limit_orders: bool = True
    max_order_notional: Decimal | str | None = None

    def __post_init__(self) -> None:
        if self.max_order_notional is None:
            return
        value = Decimal(str(self.max_order_notional))
        if value <= 0:
            raise ValueError("max_order_notional must be positive")
        object.__setattr__(self, "max_order_notional", value)

    def reject_reason(self, request: OrderRequest) -> str:
        if not self.trading_enabled:
            return "live trading is disabled"
        if self.require_limit_orders and request.order_type is not OrderType.LIMIT:
            return "live trading requires limit orders"
        if self.max_order_notional is None:
            return ""
        if request.limit_price is None:
            return "live max_order_notional requires a limit price"
        notional = request.quantity * request.limit_price
        if notional > self.max_order_notional:
            return f"order notional {notional} exceeds max_order_notional {self.max_order_notional}"
        return ""


class LiveExecutionAdapter:
    def __init__(
        self,
        *,
        account: AccountContext,
        coordinator: object,
        order_execution: OrderExecutionPort | None = None,
        symbol_resolver: SymbolResolver | None = None,
        snapshot_provider: SnapshotProvider | None = None,
        safety_policy: LiveTradingSafetyPolicy | None = None,
    ) -> None:
        self.account = account
        self.coordinator = coordinator
        self.order_execution = order_execution
        self.symbol_resolver = symbol_resolver or (lambda instrument_id: str(instrument_id))
        self.snapshot_provider = snapshot_provider
        self.safety_policy = safety_policy or LiveTradingSafetyPolicy()

    def submit_intent(
        self,
        intent: TradeIntent,
        context: ExecutionIntentContext,
        *,
        order_params: Mapping[str, object] | None = None,
    ) -> bool:
        if context.now is None:
            return False
        if intent.kind is not IntentKind.TARGET_POSITION:
            self._record_intent_event(context, intent, IntentEventKind.REJECTED, f"unsupported intent kind: {intent.kind}")
            return True
        if intent.target_quantity is None:
            self._record_intent_event(context, intent, IntentEventKind.REJECTED, "target_position intent requires target_quantity")
            return True

        current = self.coordinator.ledger.positions(self.account.book).get(str(intent.instrument_id), Decimal("0"))
        delta = intent.target_quantity - current
        if delta == 0:
            self._record_intent_event(context, intent, IntentEventKind.ACCEPTED, "")
            self._record_intent_event(context, intent, IntentEventKind.SATISFIED, "")
            return True

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        order_id = f"{intent.intent_id}-live-order"
        order_type = OrderType.LIMIT if intent.limit_price is not None else OrderType.MARKET
        request = OrderRequest(
            order_id,
            self.account,
            intent.instrument_id,
            side,
            abs(delta),
            order_type=order_type,
            limit_price=intent.limit_price,
            market_id=intent.market_id,
        )
        self._record_intent_event(context, intent, IntentEventKind.ACCEPTED, "")

        safety_reason = self.safety_policy.reject_reason(request)
        if safety_reason:
            self._record_intent_event(context, intent, IntentEventKind.REJECTED, safety_reason, order_ids=(order_id,))
            return True

        state = self.coordinator.plan_order(
            request,
            venue_snapshot=self._snapshot(),
            at=context.now,
            **_margin_plan_args(order_params, request.instrument_id),
        )
        self._record_intent_event(context, intent, IntentEventKind.PLANNED, "", order_ids=(state.order_id,))
        if state.status.value == "rejected":
            self._record_intent_event(context, intent, IntentEventKind.FAILED, state.reason, order_ids=(state.order_id,))
            return True

        state = self._submit_order_to_execution_port(
            state.request.order_id,
            at=context.now,
            params=_broker_order_params(order_params),
        )
        if state.status.value in {"rejected", "unknown"}:
            self._record_intent_event(context, intent, IntentEventKind.FAILED, state.reason, order_ids=(state.order_id,))
        else:
            self._record_intent_event(context, intent, IntentEventKind.ORDERING, "", order_ids=(state.order_id,))
        return True

    def _record_intent_event(
        self,
        context: ExecutionIntentContext,
        intent: TradeIntent,
        kind: IntentEventKind,
        reason: str,
        *,
        order_ids: tuple[str, ...] = (),
    ) -> None:
        if context.now is None:
            return
        context.intents.record(IntentEvent(intent.intent_id, kind, context.now, order_ids, reason))

    def _snapshot(self) -> AccountSnapshot | None:
        return None if self.snapshot_provider is None else self.snapshot_provider()

    def _submit_order_to_execution_port(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.submit_order(order_id, at=at)
        if self.order_execution is None:
            return state
        try:
            result = self.order_execution.submit(
                OrderSubmissionRequest(
                    account=state.request.context.book,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    side=state.request.side,
                    order_type=state.request.order_type,
                    quantity=state.request.quantity,
                    limit_price=state.request.limit_price,
                    integration_options=params,
                )
            )
        except Exception as error:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason=str(error))
        if not result.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id")
        return self.coordinator.acknowledge_order(order_id, order_venue_id=result.order_venue_id, at=at)

    def _symbol_for(self, instrument_id: object) -> str:
        symbol = str(self.symbol_resolver(instrument_id)).strip()
        if not symbol:
            raise ValueError(f"empty broker symbol for instrument: {instrument_id}")
        return symbol


@dataclass(frozen=True, slots=True)
class LiveExecutionService:
    coordinator: object
    account: AccountContext | None = None
    order_execution: OrderExecutionPort | None = None
    symbol_resolver: SymbolResolver | None = None
    snapshot_provider: FlexibleSnapshotProvider | None = None
    safety_policy: LiveTradingSafetyPolicy | None = None
    order_params: Mapping[str, object] | None = None
    directory: LaunchAccountDirectory | None = None
    routes: tuple[AccountBookRoute, ...] = ()

    async def events(self) -> AsyncIterator[ExecutionRuntimeEnvelope]:
        if False:
            yield

    def submit_intent(self, intent: TradeIntent, context: object) -> bool:
        account = self._resolve_account(intent)
        if account is not None and not self._route(account.book).can_trade:
            _reject_intent(context, intent, _not_tradable_reason(account.book))
            return True
        adapter = self._adapter(account)
        return adapter.submit_intent(intent, context, order_params=self._order_params(account.book))  # type: ignore[arg-type]

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
        if not self._route(request.context.book).can_trade:
            state = self.coordinator.orders.plan(request)
            self.coordinator.orders.record(OrderEvent(state.order_id, OrderEventKind.REJECTED, at, reason=_not_tradable_reason(request.context.book)))
            return self.coordinator.orders.get(state.order_id)
        return self.coordinator.plan_order(
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
        if not self._route(state.request.context.book).can_trade:
            return self.coordinator.orders.record(
                OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=_not_tradable_reason(state.request.context.book))
            )
        state = self.coordinator.submit_order(order_id, at=at)
        if self.order_execution is None:
            return state
        try:
            result = self.order_execution.submit(
                OrderSubmissionRequest(
                    account=state.request.context.book,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    side=state.request.side,
                    order_type=state.request.order_type,
                    quantity=state.request.quantity,
                    limit_price=state.request.limit_price,
                    integration_options=params,
                )
            )
        except Exception as error:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason=str(error))
        if not result.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id")
        return self.coordinator.acknowledge_order(order_id, order_venue_id=result.order_venue_id, at=at)

    def cancel_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.orders.get(order_id)
        if not self._route(state.request.context.book).can_trade:
            return self.coordinator.orders.record(
                OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=_not_tradable_reason(state.request.context.book))
            )
        state = self.coordinator.cancel_order(order_id, at=at)
        if self.order_execution is None:
            return state
        if not state.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id for cancel")
        try:
            result = self.order_execution.cancel(
                OrderCancelRequest(
                    account=state.request.context.book,
                    order_venue_id=state.order_venue_id,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    integration_options=params,
                )
            )
        except Exception as error:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason=str(error))
        if result.status.strip().lower() in {"canceled", "cancelled"}:
            return self.coordinator.cancel_confirmed(order_id, at=at)
        return state

    def _adapter(self, account: AccountContext | None = None) -> LiveExecutionAdapter:
        selected = account or self.account
        if selected is None:
            raise RuntimeError("live execution service requires an account before it can execute intents")
        return LiveExecutionAdapter(
            account=selected,
            coordinator=self.coordinator,
            order_execution=self.order_execution,
            symbol_resolver=self.symbol_resolver,
            snapshot_provider=lambda: self._snapshot(selected.book),
            safety_policy=self.safety_policy,
        )

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
        if self.snapshot_provider is None:
            return None
        try:
            return self.snapshot_provider(account)
        except TypeError:
            return self.snapshot_provider()

    def _order_params(self, account: AccountBookRef) -> Mapping[str, object] | None:
        route = self._route(account)
        values = {**dict(route.order_params), **dict(self.order_params or {})}
        return values or None

    def _route(self, account: AccountBookRef) -> AccountBookRoute:
        for route in self.routes:
            if route.book == account:
                return route
        return account_book_route(account)

    def _symbol_for(self, instrument_id: object) -> str:
        resolver = self.symbol_resolver or (lambda value: str(value))
        symbol = str(resolver(instrument_id)).strip()
        if not symbol:
            raise ValueError(f"empty broker symbol for instrument: {instrument_id}")
        return symbol


def _margin_plan_args(params: Mapping[str, object] | None, instrument_id: str) -> dict[str, object]:
    values = params or {}
    margin_currency = values.get("marginCurrency") or values.get("margin_currency")
    margin_notional = values.get("marginNotional") or values.get("margin_notional")
    if margin_currency is None and margin_notional is None:
        return {}
    if margin_currency is None or margin_notional is None:
        raise ValueError("marginCurrency and marginNotional must be supplied together")
    leverage = values.get("marginLeverage") or values.get("margin_leverage") or "1"
    margin_instrument = values.get("marginInstrumentId") or values.get("margin_instrument_id") or instrument_id
    return {
        "reserve_currency": str(margin_currency),
        "margin_notional": Decimal(str(margin_notional)),
        "margin_leverage": Decimal(str(leverage)),
        "margin_instrument_id": str(margin_instrument),
    }


def _reject_intent(context: object, intent: TradeIntent, reason: str) -> None:
    now = getattr(context, "now", None)
    intents = getattr(context, "intents", None)
    if now is None or intents is None:
        return
    intents.record(IntentEvent(intent.intent_id, IntentEventKind.REJECTED, now, reason=reason))


def _not_tradable_reason(account: AccountBookRef) -> str:
    return f"account {account.value} is not tradable with the selected credential"


def _broker_order_params(params: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if params is None:
        return None
    local_keys = {
        "marginCurrency",
        "margin_currency",
        "marginNotional",
        "margin_notional",
        "marginLeverage",
        "margin_leverage",
        "marginInstrumentId",
        "margin_instrument_id",
    }
    return {key: value for key, value in params.items() if key not in local_keys}


__all__ = ["LiveExecutionAdapter", "LiveExecutionService", "LiveTradingSafetyPolicy"]
