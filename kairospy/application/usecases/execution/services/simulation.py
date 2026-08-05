from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator
from kairospy.application.usecases.execution.domain.result import SimulatedFill
from kairospy.domain.intent import TradeIntent
from kairospy.domain.market import Bar, MarketObservation, OrderBookSnapshot, Quote, RateObservation, TradePrint
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderEventKind, OrderRequest, OrderSide, OrderType
from kairospy.application.usecases.execution.domain.simulation import BasisPointSlippageModel, TradingRules
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
        elif "orderbook.asks" in payload or "orderbook.bids" in payload:
            level_size = payload.get("orderbook.ask_size" if order.side is OrderSide.BUY else "orderbook.bid_size")
            if level_size is not None:
                quantity = min(quantity, Decimal(str(level_size)) * self.participation_rate)
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
        emit_update: Callable[[ExecutionUpdate], None],
        record_fill: Callable[[SimulatedFill], None] | None = None,
        trading_rules: TradingRules | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.context = context
        self.intent = intent
        self.cash_currency = cash_currency
        self.price_field = price_field
        self.fill_model = fill_model
        self.slippage_model = slippage_model
        self.commission_model = commission_model
        self.emit_update = emit_update
        self.record_fill = record_fill
        self.trading_rules = trading_rules
        self.last_fill: SimulatedFill | None = None
        self.order_id: str | None = None
        self._state: OrderRequest | None = None
        self._venue_id: str | None = None
        self._filled_quantity = Decimal("0")
        self._canceled = False
        self._rejected = False

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        order_id = request.client_order_id
        if not order_id:
            raise ValueError("simulated order submission requires client_order_id")
        self.order_id = order_id
        state = self.coordinator.orders.get(order_id)
        venue_id = f"simulated.{order_id}"
        self._state = state.request
        self._venue_id = venue_id
        if self.trading_rules is not None:
            reason = self.trading_rules.validate(self._state)
            if reason is not None:
                self._rejected = True
                return ConnectionOrderSubmissionResult(venue_id, "REJECTED", reason)
        self.emit_update(ExecutionUpdate(
            self.context.now,
            OrderEventKind.ACKNOWLEDGED,
            order_venue_id=venue_id,
            order_id=order_id,
            context=state.request.context,
            instrument_id=state.request.instrument_id,
            market_id=state.request.market_id,
            side=state.request.side,
            quantity=state.request.quantity,
            order_type=state.request.order_type,
            limit_price=state.request.limit_price,
            source="simulated_exchange",
        ))
        return ConnectionOrderSubmissionResult(venue_id, "NEW")

    def on_market_event(self, event: object, *, available_quantity: Decimal | None = None) -> Decimal:
        if self._canceled or self._rejected or self._state is None or self._filled_quantity >= self._state.quantity:
            return Decimal("0")
        value = getattr(event, "value", event)
        instrument_id = getattr(value, "instrument_id", None)
        if instrument_id is None:
            subject = getattr(event, "subject", None)
            instrument_id = getattr(subject, "subject_id", None) if getattr(subject, "subject_type", None) == "instrument" else None
        if instrument_id is not None and str(instrument_id) != str(self._state.instrument_id):
            return Decimal("0")
        payload, observed_at, marker = self._market_payload(event)
        raw_price = self._projected_market_value(self._state, payload)
        if raw_price is None:
            return Decimal("0")
        if self.trading_rules is not None:
            reason = self.trading_rules.validate(self._state, market_price=raw_price)
            if reason is not None:
                self._rejected = True
                self.emit_update(ExecutionUpdate(
                    observed_at,
                    OrderEventKind.REJECTED,
                    order_venue_id=self._venue_id or f"simulated.{self.order_id}",
                    order_id=self.order_id,
                    context=self._state.context,
                    instrument_id=self._state.instrument_id,
                    market_id=self._state.market_id,
                    side=self._state.side,
                    quantity=self._state.quantity,
                    order_type=self._state.order_type,
                    limit_price=self._state.limit_price,
                    reason=reason,
                    source="simulated_exchange",
                ))
                return Decimal("0")
        candidate = self.fill_model.fill(self._state, market_price=raw_price, payload=payload)
        if candidate is None:
            return Decimal("0")
        remaining = self._state.quantity - self._filled_quantity
        quantity = min(candidate.quantity, remaining)
        if available_quantity is not None:
            quantity = min(quantity, available_quantity)
        if quantity <= 0:
            return Decimal("0")
        fill_price = self.slippage_model.price(self._state.side, candidate.price, payload=payload)
        fee = self.commission_model.fee(side=self._state.side, quantity=quantity, price=fill_price, payload=payload)
        cash_delta = quantity * fill_price if self._state.side is OrderSide.SELL else -(quantity * fill_price)
        reason = self._buying_power_reason(quantity, fill_price, fee)
        if reason is not None:
            self._rejected = True
            self.emit_update(ExecutionUpdate(
                observed_at,
                OrderEventKind.REJECTED,
                order_venue_id=self._venue_id or f"simulated.{self.order_id}",
                order_id=self.order_id,
                context=self._state.context,
                instrument_id=self._state.instrument_id,
                market_id=self._state.market_id,
                side=self._state.side,
                quantity=self._state.quantity,
                order_type=self._state.order_type,
                limit_price=self._state.limit_price,
                reason=reason,
                source="simulated_exchange",
            ))
            return Decimal("0")
        self._filled_quantity += quantity
        self.emit_update(ExecutionUpdate(
            observed_at,
            OrderEventKind.FILLED if self._filled_quantity >= self._state.quantity else OrderEventKind.PARTIALLY_FILLED,
            order_venue_id=self._venue_id or f"simulated.{self.order_id}",
            order_id=self.order_id,
            context=self._state.context,
            instrument_id=self._state.instrument_id,
            market_id=self._state.market_id,
            side=self._state.side,
            quantity=self._state.quantity,
            order_type=self._state.order_type,
            limit_price=self._state.limit_price,
            filled_quantity=self._filled_quantity,
            fill_quantity=quantity,
            fill_price=fill_price,
            settlement_currency=self.cash_currency,
            cash_delta=cash_delta,
            fee_currency=self.cash_currency if fee else None,
            fee_amount=fee,
            source="simulated_exchange",
            metadata={"trade_id": f"{self._venue_id}:{marker}:{self._filled_quantity}"},
        ))
        fill = SimulatedFill(self.order_id or "", str(self.intent.intent_id), str(self.intent.instrument_id), self._state.side, quantity, fill_price, fee, observed_at)
        self.last_fill = fill
        if self.record_fill is not None:
            self.record_fill(fill)
        return quantity

    def _buying_power_reason(self, quantity: Decimal, price: Decimal, fee: Decimal) -> str | None:
        if self._state is None or self._state.side is OrderSide.SELL:
            return None
        cash = self.coordinator.ledger.cash(self._state.context.book).get(self.cash_currency, Decimal("0"))
        held = self.coordinator.reservations.active_amounts(self._state.context.book).get(self.cash_currency, Decimal("0"))
        reservation_id = self._state.reservation_id or self._state.order_id
        own_hold = next(
            (
                item.amount
                for item in self.coordinator.reservations.reservations
                if item.reservation_id == reservation_id and item.status.value in {"held", "reflected"}
            ),
            Decimal("0"),
        )
        required = quantity * price + fee
        if cash - held + own_hold < required:
            return "insufficient simulated buying power"
        return None

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        state = self.coordinator.orders.get_by_order_venue_id(request.order_venue_id)
        self._canceled = True
        self.emit_update(ExecutionUpdate(
            self.context.now,
            OrderEventKind.CANCELED,
            order_venue_id=request.order_venue_id,
            order_id=state.order_id,
            context=state.request.context,
            instrument_id=state.request.instrument_id,
            market_id=state.request.market_id,
            side=state.request.side,
            quantity=state.request.quantity,
            order_type=state.request.order_type,
            limit_price=state.request.limit_price,
            filled_quantity=state.filled_quantity or None,
            source="simulated_exchange",
        ))
        return ConnectionOrderCancelResult(request.order_venue_id, "CANCELED")

    def _market_payload(self, event: object) -> tuple[Mapping[str, object], datetime, str]:
        value = getattr(event, "value", event)
        observed_at = getattr(value, "time", None) or getattr(event, "observed_at", None) or self.context.now
        marker = str(getattr(event, "sequence", None) or observed_at)
        prefix = (
            "quote" if isinstance(value, Quote)
            else "orderbook" if isinstance(value, OrderBookSnapshot)
            else "bar" if isinstance(value, Bar)
            else "trade" if isinstance(value, TradePrint)
            else "rate" if isinstance(value, RateObservation)
            else "observation" if isinstance(value, MarketObservation)
            else "market"
        )
        return _market_model_payload(prefix, value), observed_at, marker

    def _projected_market_value(self, order: OrderRequest, payload: Mapping[str, object]) -> Decimal | None:
        side_field = "ask" if order.side is OrderSide.BUY else "bid"
        for key in (f"orderbook.{side_field}", f"quote.{side_field}", side_field):
            if key in payload:
                return Decimal(str(payload[key]))
        for key in (self.price_field, f"bar.{self.price_field}", f"trade.{self.price_field}", f"quote.{self.price_field}", f"rate.{self.price_field}", f"observation.{self.price_field}"):
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
    elif isinstance(item, OrderBookSnapshot):
        values.update(
            {
                "bids": item.bids,
                "asks": item.asks,
                "bid": item.bid1.price if item.bid1 is not None else None,
                "ask": item.ask1.price if item.ask1 is not None else None,
                "bid_size": item.bid1.size if item.bid1 is not None else None,
                "ask_size": item.ask1.size if item.ask1 is not None else None,
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
    elif isinstance(item, MarketObservation):
        values.update(item.payload)
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
