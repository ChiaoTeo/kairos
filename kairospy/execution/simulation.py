from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol

from kairospy.accounts import AccountContext
from kairospy.intents import IntentEvent, IntentEventKind, IntentKind, TradeIntent
from kairospy.orders import OrderEvent, OrderEventKind, OrderRequest, OrderSide, OrderType
from kairospy.strategy import StrategyContext

from .coordinator import ExecutionCoordinator, FillReport


@dataclass(frozen=True, slots=True)
class FillCandidate:
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    order_id: str
    intent_id: str
    instrument_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    occurred_at: datetime

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


class FillModel(Protocol):
    def fill(
        self,
        order: OrderRequest,
        *,
        market_price: Decimal,
        payload: Mapping[str, object],
    ) -> FillCandidate | None:
        ...


@dataclass(frozen=True, slots=True)
class ImmediateFillModel:
    volume_field: str | None = None
    participation_rate: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.participation_rate <= 0:
            raise ValueError("participation_rate must be positive")

    def fill(
        self,
        order: OrderRequest,
        *,
        market_price: Decimal,
        payload: Mapping[str, object],
    ) -> FillCandidate | None:
        if order.order_type is OrderType.LIMIT and not _limit_crosses(order, market_price):
            return None
        quantity = order.quantity
        if self.volume_field is not None and self.volume_field in payload:
            max_quantity = Decimal(str(payload[self.volume_field])) * self.participation_rate
            quantity = min(quantity, max_quantity)
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


@dataclass(frozen=True, slots=True)
class BasisPointSlippageModel:
    basis_points: Decimal

    def __post_init__(self) -> None:
        if self.basis_points < 0:
            raise ValueError("basis_points cannot be negative")

    def price(self, side: OrderSide, price: Decimal, *, payload: Mapping[str, object]) -> Decimal:
        adjustment = self.basis_points / Decimal("10000")
        return price * (Decimal("1") + adjustment if side is OrderSide.BUY else Decimal("1") - adjustment)


class CommissionModel(Protocol):
    def fee(
        self,
        *,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        payload: Mapping[str, object],
    ) -> Decimal:
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

    def fee(
        self,
        *,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        payload: Mapping[str, object],
    ) -> Decimal:
        fee = quantity * price * self.rate
        return max(fee, self.minimum)


@dataclass(frozen=True, slots=True)
class PerShareCommissionModel:
    amount: Decimal
    minimum: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("per-share commission cannot be negative")
        if self.minimum < 0:
            raise ValueError("minimum commission cannot be negative")

    def fee(
        self,
        *,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        payload: Mapping[str, object],
    ) -> Decimal:
        fee = quantity * self.amount
        return max(fee, self.minimum)


class SimulatedExecutionAdapter:
    def __init__(
        self,
        *,
        account: AccountContext,
        cash_currency: str,
        price_field: str,
        coordinator: ExecutionCoordinator,
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        commission_model: CommissionModel | None = None,
    ) -> None:
        if not cash_currency.strip():
            raise ValueError("simulated execution requires cash_currency")
        if not price_field.strip():
            raise ValueError("simulated execution requires price_field")
        self.account = account
        self.cash_currency = cash_currency
        self.price_field = price_field
        self.coordinator = coordinator
        self.fill_model = fill_model or ImmediateFillModel()
        self.slippage_model = slippage_model or NoSlippageModel()
        self.commission_model = commission_model or PercentageCommissionModel()
        self._order_number = 0

    def execute_intent(self, intent: TradeIntent, context: StrategyContext) -> SimulatedFill | None:
        if context.now is None:
            self._reject_intent(intent, context, "intent has no executable timestamp")
            return None
        if intent.kind is not IntentKind.TARGET_POSITION:
            self._reject_intent(intent, context, f"unsupported intent kind: {intent.kind}")
            return None
        if intent.target_quantity is None:
            self._reject_intent(intent, context, "target_position intent requires target_quantity")
            return None
        price = self._price(context)
        if price is None:
            self._reject_intent(intent, context, f"missing price field: {self.price_field}")
            return None

        current = self.coordinator.ledger.positions(self.account.account).get(
            intent.instrument_id,
            Decimal("0"),
        )
        delta = intent.target_quantity - current
        if delta == 0:
            context.intents.record(IntentEvent(intent.intent_id, IntentEventKind.ACCEPTED, context.now))
            context.intents.record(IntentEvent(intent.intent_id, IntentEventKind.SATISFIED, context.now))
            return None

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = abs(delta)
        order_id = self._next_order_id(intent.intent_id)
        order_type = OrderType.LIMIT if intent.limit_price is not None else OrderType.MARKET
        request = OrderRequest(
            order_id,
            self.account,
            intent.instrument_id,
            side,
            quantity,
            order_type=order_type,
            limit_price=intent.limit_price,
            market_id=intent.market_id,
        )

        context.intents.record(IntentEvent(intent.intent_id, IntentEventKind.ACCEPTED, context.now))
        self.coordinator.plan_order(request, at=context.now)
        context.intents.record(IntentEvent(intent.intent_id, IntentEventKind.PLANNED, context.now, order_ids=(order_id,)))
        self.coordinator.submit_order(order_id, at=context.now)
        context.intents.record(IntentEvent(intent.intent_id, IntentEventKind.ORDERING, context.now))

        assert context.event is not None
        candidate = self.fill_model.fill(request, market_price=price, payload=context.event.payload)
        if candidate is None:
            self.coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, context.now, reason="not filled"))
            self._reject_intent(intent, context, "order was not filled by fill model")
            return None
        fill_quantity = min(candidate.quantity, quantity)
        if fill_quantity <= 0:
            self.coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, context.now, reason="zero fill"))
            self._reject_intent(intent, context, "fill model returned zero quantity")
            return None
        fill_price = self.slippage_model.price(side, candidate.price, payload=context.event.payload)
        fee = self.commission_model.fee(
            side=side,
            quantity=fill_quantity,
            price=fill_price,
            payload=context.event.payload,
        )
        notional = fill_quantity * fill_price
        cash_delta = notional if side is OrderSide.SELL else -notional
        updated_order = self.coordinator.ingest_fill(
            FillReport(
                order_id,
                context.now,
                fill_quantity,
                fill_price,
                self.cash_currency,
                cash_delta=cash_delta,
                fee_currency=self.cash_currency if fee else None,
                fee_amount=fee,
            )
        )
        fill = SimulatedFill(
            order_id,
            intent.intent_id,
            intent.instrument_id,
            side,
            fill_quantity,
            fill_price,
            fee,
            context.now,
        )
        intent_event_kind = IntentEventKind.SATISFIED if updated_order.status.terminal else IntentEventKind.PARTIALLY_FILLED
        context.intents.record(IntentEvent(intent.intent_id, intent_event_kind, context.now))
        return fill

    def _reject_intent(self, intent: TradeIntent, context: StrategyContext, reason: str) -> None:
        if context.now is None:
            return
        context.intents.record(IntentEvent(intent.intent_id, IntentEventKind.REJECTED, context.now, reason=reason))

    def _price(self, context: StrategyContext) -> Decimal | None:
        if context.event is None:
            return None
        raw_price = context.event.payload.get(self.price_field)
        return None if raw_price is None else Decimal(str(raw_price))

    def _next_order_id(self, intent_id: str) -> str:
        self._order_number += 1
        return f"{intent_id}-order-{self._order_number}"


def _limit_crosses(order: OrderRequest, market_price: Decimal) -> bool:
    if order.limit_price is None:
        return True
    if order.side is OrderSide.BUY:
        return market_price <= order.limit_price
    return market_price >= order.limit_price


__all__ = [
    "BasisPointSlippageModel",
    "CommissionModel",
    "FillCandidate",
    "FillModel",
    "ImmediateFillModel",
    "NoSlippageModel",
    "PerShareCommissionModel",
    "PercentageCommissionModel",
    "SimulatedExecutionAdapter",
    "SimulatedFill",
    "SlippageModel",
]
