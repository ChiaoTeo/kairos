from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.execution.application.orders import ExecutionOrderOptions
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderOptions,
    ConnectionOrderCancelRequest,
    ConnectionOrderSubmissionRequest,
)
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.domain.account import AccountSnapshot
from kairospy.domain.order import OrderRequest, OrderState, OrderType


class SymbolResolver(Protocol):
    def __call__(self, instrument_id: object) -> str:
        ...


class ExecutionOrderService:
    def __init__(
        self,
        coordinator: object,
        *,
        order_execution: object | None = None,
        symbol_resolver: SymbolResolver | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.order_execution = order_execution
        self.symbol_resolver = symbol_resolver or (lambda instrument_id: str(instrument_id))

    def plan(
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

    def submit(
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
                ConnectionOrderSubmissionRequest(
                    account=state.request.context.book,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    side=state.request.side,
                    order_type=state.request.order_type,
                    quantity=state.request.quantity,
                    limit_price=state.request.limit_price,
                    options=_connection_options(ExecutionOrderOptions.from_mapping(broker_order_params(params))),
                )
            )
        except Exception as error:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason=str(error))
        if not result.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id")
        return self.coordinator.acknowledge_order(order_id, order_venue_id=result.order_venue_id, at=at)

    def cancel(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.cancel_order(order_id, at=at)
        if self.order_execution is None:
            return state
        if not state.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id for cancel")
        try:
            result = self.order_execution.cancel(
                ConnectionOrderCancelRequest(
                    account=state.request.context.book,
                    order_venue_id=state.order_venue_id,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    options=_connection_options(ExecutionOrderOptions.from_mapping(broker_order_params(params))),
                )
            )
        except Exception as error:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason=str(error))
        if result.status.strip().lower() in {"canceled", "cancelled"}:
            return self.coordinator.cancel_confirmed(order_id, at=at)
        return state

    def _symbol_for(self, instrument_id: object) -> str:
        symbol = str(self.symbol_resolver(instrument_id)).strip()
        if not symbol:
            raise ValueError(f"empty broker symbol for instrument: {instrument_id}")
        return symbol


def _connection_options(options: ExecutionOrderOptions | None) -> ConnectionOrderOptions | None:
    if options is None:
        return None
    return ConnectionOrderOptions(
        time_in_force=options.time_in_force,
        reduce_only=options.reduce_only,
        post_only=options.post_only,
    )


def margin_plan_args(params: Mapping[str, object] | None, instrument_id: object) -> dict[str, object]:
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


def broker_order_params(params: Mapping[str, object] | None) -> Mapping[str, object] | None:
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
    values = {key: value for key, value in params.items() if key not in local_keys}
    return values or None


__all__ = [
    "ExecutionOrderService",
    "ExecutionSafetyPolicy",
    "SymbolResolver",
    "broker_order_params",
    "margin_plan_args",
]
