from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.usecases.execution.services.intents import ExecutionIntentService
from kairospy.domain.account import AccountContext
from kairospy.application.usecases.execution.services.coordinator import ExecutionCoordinator, FillReport
from kairospy.application.usecases.execution.domain.result import SimulatedFill
from kairospy.domain.intent import TradeIntent
from kairospy.domain.market import Bar, Quote, RateObservation, TradePrint
from kairospy.domain.order import OrderEvent, OrderEventKind, OrderRequest, OrderSide, OrderType
from kairospy.application.usecases.execution.domain.simulation import BasisPointSlippageModel


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


class SimulatedExecutionService:
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
        self.intents = ExecutionIntentService()
        self._order_number = 0

    def submit_intent(self, intent: TradeIntent, context: object) -> SimulatedFill | None:
        if context.now is None:
            return None

        current = self.coordinator.ledger.positions(self.account.book).get(str(intent.instrument_id), Decimal("0"))
        plan = self.intents.plan_target_position_order(
            intent,
            context,
            account=self.account,
            current_quantity=current,
            order_id=self._next_order_id(intent.intent_id),
        )
        if plan is None:
            return None
        request = plan.request
        side = request.side
        quantity = request.quantity
        order_id = request.order_id
        price = self._price(context, intent)
        if price is None:
            self.intents.reject(context, intent, f"missing price field: {self.price_field}", order_ids=(order_id,))
            return None

        self.coordinator.plan_order(request, at=context.now)
        self.intents.planned(context, intent, order_ids=(order_id,))
        self.coordinator.submit_order(order_id, at=context.now)
        self.intents.ordering(context, intent, order_ids=(order_id,))

        market_payload = self._market_payload(context, intent)
        candidate = self.fill_model.fill(request, market_price=price, payload=market_payload)
        if candidate is None:
            self.coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, context.now, reason="not filled"))
            self.intents.reject(context, intent, "order was not filled by fill model", order_ids=(order_id,))
            return None
        fill_quantity = min(candidate.quantity, quantity)
        if fill_quantity <= 0:
            self.coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, context.now, reason="zero fill"))
            self.intents.reject(context, intent, "fill model returned zero quantity", order_ids=(order_id,))
            return None
        fill_price = self.slippage_model.price(side, candidate.price, payload=market_payload)
        fee = self.commission_model.fee(side=side, quantity=fill_quantity, price=fill_price, payload=market_payload)
        cash_delta = fill_quantity * fill_price if side is OrderSide.SELL else -(fill_quantity * fill_price)
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
        fill = SimulatedFill(order_id, str(intent.intent_id), str(intent.instrument_id), side, fill_quantity, fill_price, fee, context.now)
        if updated_order.status.terminal:
            self.intents.satisfy(context, intent, order_ids=(order_id,))
        else:
            self.intents.partially_filled(context, intent, order_ids=(order_id,))
        return fill

    def _price(self, context: object, intent: TradeIntent) -> Decimal | None:
        raw_price = self._projected_market_value(context, intent)
        return None if raw_price is None else Decimal(str(raw_price))

    def _projected_market_value(self, context: object, intent: TradeIntent) -> object | None:
        payload = self._market_payload(context, intent)
        for key in (self.price_field, f"bar.{self.price_field}", f"trade.{self.price_field}", f"quote.{self.price_field}", f"rate.{self.price_field}"):
            if key in payload:
                return payload[key]
        return None

    def _market_payload(self, context: object, intent: TradeIntent) -> Mapping[str, object]:
        market = getattr(context, "market")
        values: dict[str, object] = {}
        for prefix, window in (
            ("quote", market.quotes(intent.market_id or intent.instrument_id)),
            ("bar", market.bars(intent.market_id or intent.instrument_id)),
            ("trade", market.trades(intent.market_id or intent.instrument_id)),
            ("rate", market.rates(intent.market_id or intent.instrument_id)),
        ):
            item = getattr(window, "latest", None)
            if item is not None:
                values.update(_market_model_payload(prefix, item))
        return values

    def _next_order_id(self, intent_id: object) -> str:
        self._order_number += 1
        return f"{intent_id}-order-{self._order_number}"


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
    "SimulatedExecutionService",
    "SimulatedFill",
    "SlippageModel",
]
