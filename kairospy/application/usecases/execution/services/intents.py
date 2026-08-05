from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.domain.account import AccountRuntimeContext
from kairospy.domain.intent import IntentEvent, IntentEventKind, IntentKind, TradeIntent
from kairospy.domain.order import OrderRequest, OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class ExecutionIntentOrderPlan:
    request: OrderRequest
    delta: Decimal


class ExecutionIntentService:
    def plan_target_position_order(
        self,
        intent: TradeIntent,
        context: object,
        *,
        account: AccountRuntimeContext,
        current_quantity: Decimal,
        order_id: str,
        record_events: bool = True,
    ) -> ExecutionIntentOrderPlan | None:
        if context.now is None:
            return None
        if intent.kind is not IntentKind.TARGET_POSITION:
            if record_events:
                self.reject(context, intent, f"unsupported intent kind: {intent.kind}")
            return None
        if intent.target_quantity is None:
            if record_events:
                self.reject(context, intent, "target_position intent requires target_quantity")
            return None

        delta = intent.target_quantity - current_quantity
        if record_events:
            self.accept(context, intent)
        if delta == 0:
            if record_events:
                self.satisfy(context, intent)
            return None

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        order_type = OrderType.LIMIT if intent.limit_price is not None else OrderType.MARKET
        return ExecutionIntentOrderPlan(
            OrderRequest(
                order_id,
                account,
                intent.instrument_id,
                side,
                abs(delta),
                order_type=order_type,
                limit_price=intent.limit_price,
                market_id=intent.market_id,
            ),
            delta,
        )

    def accept(self, context: object, intent: TradeIntent) -> None:
        self._record(context, intent, IntentEventKind.ACCEPTED)

    def planned(self, context: object, intent: TradeIntent, *, order_ids: tuple[str, ...]) -> None:
        self._record(context, intent, IntentEventKind.PLANNED, order_ids=order_ids)

    def ordering(self, context: object, intent: TradeIntent, *, order_ids: tuple[str, ...] = ()) -> None:
        self._record(context, intent, IntentEventKind.ORDERING, order_ids=order_ids)

    def partially_filled(self, context: object, intent: TradeIntent, *, order_ids: tuple[str, ...] = ()) -> None:
        self._record(context, intent, IntentEventKind.PARTIALLY_FILLED, order_ids=order_ids)

    def satisfy(self, context: object, intent: TradeIntent, *, order_ids: tuple[str, ...] = ()) -> None:
        self._record(context, intent, IntentEventKind.SATISFIED, order_ids=order_ids)

    def reject(self, context: object, intent: TradeIntent, reason: str, *, order_ids: tuple[str, ...] = ()) -> None:
        self._record(context, intent, IntentEventKind.REJECTED, reason=reason, order_ids=order_ids)

    def fail(self, context: object, intent: TradeIntent, reason: str, *, order_ids: tuple[str, ...] = ()) -> None:
        self._record(context, intent, IntentEventKind.FAILED, reason=reason, order_ids=order_ids)

    def _record(
        self,
        context: object,
        intent: TradeIntent,
        kind: IntentEventKind,
        *,
        reason: str = "",
        order_ids: tuple[str, ...] = (),
    ) -> None:
        now: datetime | None = getattr(context, "now", None)
        intents = getattr(context, "intents", None)
        if now is None or intents is None:
            return
        intents.record(IntentEvent(intent.intent_id, kind, now, order_ids, reason))


__all__ = ["ExecutionIntentOrderPlan", "ExecutionIntentService"]
