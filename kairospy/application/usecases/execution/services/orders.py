from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderOptions,
    ConnectionOrderSubmissionRequest,
    OrderConnection,
)
from kairospy.domain.account import AccountBookRef
from kairospy.domain.account import AccountSnapshot
from kairospy.domain.order import OrderRequest, OrderState, OrderType
from kairospy.application.usecases.risk.domain import RiskMetric


class SymbolResolver(Protocol):
    def __call__(self, instrument_id: object) -> str:
        ...


class ExecutionOrderService:
    def __init__(
        self,
        coordinator: ExecutionCoordinator,
        *,
        order_connection: OrderConnection | Mapping[AccountBookRef, OrderConnection] | None = None,
        symbol_resolver: SymbolResolver | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.order_connection = order_connection
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
        risk_amount: Decimal | None = None,
        risk_metric: RiskMetric = RiskMetric.NOTIONAL,
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
            risk_amount=risk_amount,
            risk_metric=risk_metric,
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
        connection = self._connection_for(state.request.context.book)
        if connection is None:
            return state
        try:
            result = connection.submit(
                ConnectionOrderSubmissionRequest(
                    account=state.request.context.book,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    side=state.request.side,
                    order_type=state.request.order_type,
                    quantity=state.request.quantity,
                    limit_price=state.request.limit_price,
                    options=_connection_options(broker_order_params(params)),
                    client_order_id=state.request.order_id,
                )
            )
        except Exception as error:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason=str(error))
        if result.status.strip().lower() in {"rejected", "reject"}:
            return self.coordinator.reject_order(order_id, at=at, reason=result.reason)
        if not result.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id")
        current = self.coordinator.orders.get(order_id)
        if current.status.terminal:
            return current
        return self.coordinator.acknowledge_order(order_id, order_venue_id=result.order_venue_id, at=at)

    def cancel(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.coordinator.cancel_order(order_id, at=at)
        connection = self._connection_for(state.request.context.book)
        if connection is None:
            return state
        if not state.order_venue_id:
            return self.coordinator.mark_order_unknown(order_id, at=at, reason="missing venue order id for cancel")
        try:
            result = connection.cancel(
                ConnectionOrderCancelRequest(
                    account=state.request.context.book,
                    order_venue_id=state.order_venue_id,
                    symbol=self._symbol_for(state.request.market_id or state.request.instrument_id),
                    options=_connection_options(broker_order_params(params)),
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

    def _connection_for(self, account: AccountBookRef) -> OrderConnection | None:
        connections = self.order_connection
        if connections is None:
            return None
        if isinstance(connections, Mapping):
            return connections.get(account)
        return connections


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


def _connection_options(params: Mapping[str, object] | None) -> ConnectionOrderOptions | None:
    if not params:
        return None

    def text(*keys: str) -> str | None:
        for key in keys:
            value = params.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    def boolean(*keys: str) -> bool | None:
        for key in keys:
            if key not in params or params[key] is None:
                continue
            value = params[key]
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"invalid boolean execution option: {value!r}")
        return None

    result = ConnectionOrderOptions(
        time_in_force=text("time_in_force", "timeInForce"),
        reduce_only=boolean("reduce_only", "reduceOnly"),
        post_only=boolean("post_only", "postOnly"),
    )
    return None if result == ConnectionOrderOptions() else result


__all__ = [
    "ExecutionOrderService",
    "ExecutionSafetyPolicy",
    "SymbolResolver",
    "broker_order_params",
    "margin_plan_args",
]
