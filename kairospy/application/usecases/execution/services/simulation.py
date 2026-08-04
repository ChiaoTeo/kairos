from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator, FillReport
from kairospy.application.usecases.execution.domain.result import SimulatedFill
from kairospy.domain.intent import TradeIntent
from kairospy.domain.market import Bar, Quote, RateObservation, TradePrint
from kairospy.domain.order import OrderEvent, OrderEventKind, OrderRequest, OrderSide, OrderType
from kairospy.application.usecases.execution.domain.simulation import BasisPointSlippageModel
from kairospy.infrastructure.integrations.application.execution import (
    ConnectionOrderCancelRequest,
    ConnectionOrderCancelResult,
    ConnectionOrderSubmissionRequest,
    ConnectionOrderSubmissionResult,
)


@dataclass(frozen=True, slots=True)
class FillCandidate:
    quantity: Decimal
    price: Decimal


class FillModel(Protocol):
    def fill(self, order: OrderRequest, *, market_price: Decimal, payload: Mapping[str, object]) -> FillCandidate | None:
        ...


@dataclass(frozen=True, slots=True)
class ImmediateFillModel:
    volume_field: str | None = None
    participation_rate: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.participation_rate <= 0:
            raise ValueError("participation_rate must be positive")

    def fill(self, order: OrderRequest, *, market_price: Decimal, payload: Mapping[str, object]) -> FillCandidate | None:
        if order.order_type is OrderType.LIMIT and not _limit_crosses(order, market_price):
            return None
        quantity = order.quantity
        if self.volume_field is not None and self.volume_field in payload:
            quantity = min(quantity, Decimal(str(payload[self.volume_field])) * self.participation_rate)
        if quantity <= 0:
            return None
        return FillCandidate(quantity, market_price)


class SlippageModel(Protocol):
    def price(self, side: OrderSide, price: Decimal, *, payload: Mapping[str, object]) -> Decimal:
        ...


@dataclass(frozen=True, slots=True)
class NoSlippageModel:
    def price(self, side: OrderSide, price: Decimal, *, payload: Mapping[str, object]) -> Decimal:
        return price


class CommissionModel(Protocol):
    def fee(self, *, side: OrderSide, quantity: Decimal, price: Decimal, payload: Mapping[str, object]) -> Decimal:
        ...


@dataclass(frozen=True, slots=True)
class PercentageCommissionModel:
    rate: Decimal = Decimal("0")
    minimum: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.rate < 0:
            raise ValueError("commission rate cannot be negative")
        if self.minimum < 0:
            raise ValueError("minimum commission cannot be negative")

    def fee(self, *, side: OrderSide, quantity: Decimal, price: Decimal, payload: Mapping[str, object]) -> Decimal:
        return max(quantity * price * self.rate, self.minimum)


class SimulatedOrderConnection:
    """Integration-shaped connection used by simulated execution.

    The execution application still creates and submits an order through the
    same connection contract as live execution. This connection only replaces
    the external venue with a deterministic fill model.
    """

    def __init__(
        self,
        *,
        coordinator: ExecutionCoordinator,
        context: object,
        intent: TradeIntent,
        cash_currency: str,
        price_field: str,
        fill_model: FillModel,
        slippage_model: SlippageModel,
        commission_model: CommissionModel,
    ) -> None:
        self.coordinator = coordinator
        self.context = context
        self.intent = intent
        self.cash_currency = cash_currency
        self.price_field = price_field
        self.fill_model = fill_model
        self.slippage_model = slippage_model
        self.commission_model = commission_model
        self.last_fill: SimulatedFill | None = None

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        order_id = request.client_order_id
        if not order_id:
            raise ValueError("simulated order submission requires client_order_id")
        state = self.coordinator.orders.get(order_id)
        payload = self._market_payload()
        raw_price = self._projected_market_value(payload)
        venue_id = f"simulated.{order_id}"
        if raw_price is None:
            self.coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, self.context.now, reason=f"missing price field: {self.price_field}"))
            return ConnectionOrderSubmissionResult(venue_id, "REJECTED")
        candidate = self.fill_model.fill(state.request, market_price=raw_price, payload=payload)
        if candidate is None:
            self.coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, self.context.now, reason="order was not filled by fill model"))
            return ConnectionOrderSubmissionResult(venue_id, "REJECTED")
        quantity = min(candidate.quantity, state.request.quantity)
        fill_price = self.slippage_model.price(state.request.side, candidate.price, payload=payload)
        fee = self.commission_model.fee(side=state.request.side, quantity=quantity, price=fill_price, payload=payload)
        cash_delta = quantity * fill_price if state.request.side is OrderSide.SELL else -(quantity * fill_price)
        updated = self.coordinator.ingest_fill(
            FillReport(
                order_id,
                self.context.now,
                quantity,
                fill_price,
                self.cash_currency,
                cash_delta=cash_delta,
                fee_currency=self.cash_currency if fee else None,
                fee_amount=fee,
            )
        )
        self.last_fill = SimulatedFill(order_id, str(self.intent.intent_id), str(self.intent.instrument_id), state.request.side, quantity, fill_price, fee, self.context.now)
        return ConnectionOrderSubmissionResult(venue_id, updated.status.value.upper())

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        state = self.coordinator.orders.get_by_order_venue_id(request.order_venue_id)
        self.coordinator.cancel_confirmed(state.order_id, at=self.context.now)
        return ConnectionOrderCancelResult(request.order_venue_id, "CANCELED")

    def _market_payload(self) -> Mapping[str, object]:
        market = getattr(self.context, "market")
        values: dict[str, object] = {}
        for prefix, window in (("quote", market.quotes(self.intent.market_id or self.intent.instrument_id)), ("bar", market.bars(self.intent.market_id or self.intent.instrument_id)), ("trade", market.trades(self.intent.market_id or self.intent.instrument_id)), ("rate", market.rates(self.intent.market_id or self.intent.instrument_id))):
            item = getattr(window, "latest", None)
            if item is not None:
                values.update(_market_model_payload(prefix, item))
        return values

    def _projected_market_value(self, payload: Mapping[str, object]) -> Decimal | None:
        for key in (self.price_field, f"bar.{self.price_field}", f"trade.{self.price_field}", f"quote.{self.price_field}", f"rate.{self.price_field}"):
            if key in payload:
                return Decimal(str(payload[key]))
        return None


def _limit_crosses(order: OrderRequest, market_price: Decimal) -> bool:
    if order.limit_price is None:
        return True
    if order.side is OrderSide.BUY:
        return market_price <= order.limit_price
    return market_price >= order.limit_price


def _market_model_payload(prefix: str, item: object) -> dict[str, object]:
    values: dict[str, object] = {}
    if isinstance(item, Quote):
        values.update(
            {
                "bid": item.bid,
                "ask": item.ask,
                "bid_size": item.bid_size,
                "ask_size": item.ask_size,
                "midpoint": item.midpoint,
            }
        )
    elif isinstance(item, Bar):
        values.update(
            {
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
            }
        )
    elif isinstance(item, TradePrint):
        values.update(
            {
                "price": item.price,
                "size": item.size,
                "cost": item.cost,
            }
        )
    elif isinstance(item, RateObservation):
        values.update(
            {
                "rate": item.rate,
                "mark_price": item.mark_price,
            }
        )
    return {key: value for key, value in _prefixed(prefix, values).items() if value is not None}


def _prefixed(prefix: str, values: Mapping[str, object | None]) -> dict[str, object | None]:
    payload: dict[str, object | None] = {}
    for key, value in values.items():
        payload.setdefault(key, value)
        payload[f"{prefix}.{key}"] = value
    return payload


__all__ = [
    "BasisPointSlippageModel",
    "CommissionModel",
    "FillCandidate",
    "FillModel",
    "ImmediateFillModel",
    "NoSlippageModel",
    "PercentageCommissionModel",
    "SimulatedOrderConnection",
    "SimulatedFill",
    "SlippageModel",
]
