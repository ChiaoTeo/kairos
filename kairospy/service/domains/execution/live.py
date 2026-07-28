from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from kairospy.core.account import AccountContext, AccountSnapshot
from kairospy.core.execution import ExecutionCoordinator, ExecutionIntentContext
from kairospy.core.intent import IntentEvent, IntentEventKind, IntentKind, TradeIntent
from kairospy.core.order import OrderRequest, OrderSide, OrderType


class LiveExecutionAdapter:
    def __init__(
        self,
        *,
        account: AccountContext,
        coordinator: ExecutionCoordinator,
        snapshot_provider,
    ) -> None:
        self.account = account
        self.coordinator = coordinator
        self.snapshot_provider = snapshot_provider

    def execute_intent(
        self,
        intent: TradeIntent,
        context: ExecutionIntentContext,
        *,
        order_params: Mapping[str, object] | None = None,
    ) -> bool:
        if context.now is None:
            self._record_intent_event(context, intent, IntentEventKind.REJECTED, "intent has no executable timestamp")
            return False
        if intent.kind is not IntentKind.TARGET_POSITION:
            self._record_intent_event(context, intent, IntentEventKind.REJECTED, f"unsupported intent kind: {intent.kind}")
            return True
        if intent.target_quantity is None:
            self._record_intent_event(context, intent, IntentEventKind.REJECTED, "target_position intent requires target_quantity")
            return True

        current = self.coordinator.ledger.positions(self.account.account).get(intent.instrument_id, Decimal("0"))
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
        state = self.coordinator.plan_order(
            request,
            venue_snapshot=self._snapshot(),
            at=context.now,
            **_margin_plan_args(order_params, request.instrument_id),
        )
        self._record_intent_event(context, intent, IntentEventKind.PLANNED, "", order_ids=(state.local_order_id,))
        if state.status.value == "rejected":
            self._record_intent_event(context, intent, IntentEventKind.FAILED, state.reason, order_ids=(state.local_order_id,))
            return True
        state = self.coordinator.submit_order(
            state.request.client_order_id,
            at=context.now,
            params=_broker_order_params(order_params),
        )
        if state.status.value in {"rejected", "unknown"}:
            self._record_intent_event(context, intent, IntentEventKind.FAILED, state.reason, order_ids=(state.local_order_id,))
        else:
            self._record_intent_event(context, intent, IntentEventKind.ORDERING, "", order_ids=(state.local_order_id,))
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
            raise ValueError("live execution intent events require context.now")
        context.intents.record(IntentEvent(intent.intent_id, kind, context.now, order_ids, reason))

    def _snapshot(self) -> AccountSnapshot:
        snapshot = self.snapshot_provider()
        if not isinstance(snapshot, AccountSnapshot):
            raise RuntimeError("live execution snapshot provider did not return an AccountSnapshot")
        return snapshot


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


__all__ = ["LiveExecutionAdapter"]
