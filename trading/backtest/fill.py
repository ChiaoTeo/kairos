from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Callable, Protocol
from uuid import UUID, uuid4

from trading.reference import ReferenceCatalog
from trading.reference.access import contract_spec, definition_at
from trading.domain.execution import TradeSide
from trading.domain.identity import InstrumentId
from trading.domain.market_data import Bar, OrderBookSnapshot, Quote, TradingState, TradingStatus
from trading.domain.order import Fill, LegFill, Order, OrderStatus, TimeInForce

from .execution import combo_quote
from .feed import MarketSlice


class FillModelType(StrEnum):
    CONSERVATIVE = "conservative"
    MIDPOINT = "midpoint"
    STRESS = "stress"


class CommissionModel(Protocol):
    def calculate(self, order: Order, quantity: int) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class FixedCommissionModel:
    per_contract: Decimal = Decimal("0.65")
    minimum_per_order: Decimal = Decimal("1.00")
    regulatory_per_contract: Decimal = Decimal("0.03")

    def calculate(self, order: Order, quantity: int) -> Decimal:
        contracts = sum(leg.ratio for leg in order.legs) * quantity
        return max(self.minimum_per_order, Decimal(contracts) * (self.per_contract + self.regulatory_per_contract))


@dataclass(slots=True)
class FillAttempt:
    order: Order
    fill: Fill | None
    reason: str


class ListedOptionComboFillModel:
    def __init__(
        self,
        model_type: FillModelType,
        commission: CommissionModel,
        catalog: ReferenceCatalog,
        *,
        stress_slippage_per_leg: Decimal = Decimal("0.10"),
        max_spread: Decimal = Decimal("2.00"),
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self.model_type = model_type
        self.commission = commission
        self.catalog = catalog
        self.stress_slippage_per_leg = stress_slippage_per_leg
        self.max_spread = max_spread
        self.id_factory = id_factory

    def attempt(self, order: Order, market: MarketSlice) -> FillAttempt:
        if order.status is OrderStatus.CREATED:
            order = order.transition(OrderStatus.WORKING)
        if order.status not in (OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED):
            return FillAttempt(order, None, "order_not_working")
        if market.timestamp < order.eligible_at:
            return FillAttempt(order, None, "not_yet_eligible")
        if market.timestamp > order.expires_at:
            return FillAttempt(order.transition(OrderStatus.EXPIRED, reason="time_in_force"), None, "expired")
        if market.quality_issues:
            return self._miss(order, "market_quality_issue")
        quote = combo_quote(order.legs, market, order.quantity)
        if quote is None:
            return self._miss(order, "missing_or_invalid_quote")
        if quote.max_spread > self.max_spread:
            return self._miss(order, "spread_too_wide")
        if not quote.sufficient_size:
            return self._miss(order, "insufficient_size")
        slippage = Decimal("0")
        if self.model_type is FillModelType.MIDPOINT:
            net_price = quote.midpoint
        else:
            net_price = quote.natural
            if self.model_type is FillModelType.STRESS:
                slippage = self.stress_slippage_per_leg * sum(leg.ratio for leg in order.legs)
                net_price -= slippage
        if order.limit_price is not None and net_price < order.limit_price:
            return self._miss(order, "limit_not_reached")
        leg_fills = []
        snapshots = {item.instrument_id: item for item in market.instruments}
        for leg in order.legs:
            quote_value = snapshots[leg.instrument_id].quote
            if self.model_type is FillModelType.MIDPOINT:
                price = (quote_value.bid + quote_value.ask) / 2
            else:
                price = quote_value.ask if leg.side is TradeSide.BUY else quote_value.bid
                if self.model_type is FillModelType.STRESS:
                    price += self.stress_slippage_per_leg if leg.side is TradeSide.BUY else -self.stress_slippage_per_leg
            leg_fills.append(LegFill(leg.instrument_id, leg.side, leg.ratio, price))
        commission = self.commission.calculate(order, order.quantity)
        fill = Fill(
            self.id_factory(), order.order_id, order.intent_id, order.strategy_id, order.structure_id,
            market.timestamp, tuple(leg_fills), net_price, order.quantity, commission,
            slippage * Decimal(order.quantity) * _multiplier(definition_at(self.catalog, order.legs[0].instrument_id, market.timestamp)), order.is_closing,
        )
        return FillAttempt(order.transition(OrderStatus.FILLED, filled_quantity=order.quantity), fill, "filled")

    @staticmethod
    def _miss(order: Order, reason: str) -> FillAttempt:
        if order.time_in_force is TimeInForce.IOC:
            return FillAttempt(order.transition(OrderStatus.EXPIRED, reason=reason), None, reason)
        return FillAttempt(order, None, reason)


def _multiplier(definition) -> Decimal:
    spec = contract_spec(definition)
    return getattr(spec, "multiplier", getattr(spec, "contract_size", Decimal("1")))


@dataclass(frozen=True, slots=True)
class SingleAssetOrder:
    order_id: UUID
    instrument_id: InstrumentId
    side: TradeSide
    quantity: Decimal
    eligible_at: datetime
    limit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SingleAssetFill:
    order_id: UUID
    instrument_id: InstrumentId
    side: TradeSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    slippage: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class SingleFillAttempt:
    fill: SingleAssetFill | None
    reason: str


class EquityTopOfBookFillModel:
    def __init__(self, fee_per_unit: Decimal = Decimal("0.005")) -> None:
        self.fee_per_unit = fee_per_unit

    def attempt(self, order: SingleAssetOrder, quote: Quote, status: TradingStatus | None = None) -> SingleFillAttempt:
        if quote.event_time < order.eligible_at:
            return SingleFillAttempt(None, "not_yet_eligible")
        if status is not None and status.state is not TradingState.OPEN:
            return SingleFillAttempt(None, f"trading_{status.state.value}")
        price = quote.ask if order.side is TradeSide.BUY else quote.bid
        available = quote.ask_size if order.side is TradeSide.BUY else quote.bid_size
        if price is None or available is None or available <= 0:
            return SingleFillAttempt(None, "missing_liquidity")
        if order.limit_price is not None and not _marketable(order.side, price, order.limit_price):
            return SingleFillAttempt(None, "limit_not_reached")
        quantity = min(order.quantity, available)
        return SingleFillAttempt(SingleAssetFill(
            order.order_id, order.instrument_id, order.side, quantity, price,
            quantity * self.fee_per_unit, Decimal("0"), quote.event_time,
        ), "filled" if quantity == order.quantity else "partially_filled")


class EquityBarFillModel:
    def __init__(self, fee_per_unit: Decimal = Decimal("0.005")) -> None:
        self.fee_per_unit = fee_per_unit

    def attempt(self, order: SingleAssetOrder, bar: Bar) -> SingleFillAttempt:
        if order.eligible_at > bar.start:
            return SingleFillAttempt(None, "bar_started_before_order_eligible")
        price = bar.open
        if order.limit_price is not None:
            reached = bar.low <= order.limit_price if order.side is TradeSide.BUY else bar.high >= order.limit_price
            if not reached:
                return SingleFillAttempt(None, "limit_not_reached")
            price = min(bar.open, order.limit_price) if order.side is TradeSide.BUY else max(bar.open, order.limit_price)
        quantity = min(order.quantity, bar.volume)
        if quantity <= 0:
            return SingleFillAttempt(None, "missing_liquidity")
        return SingleFillAttempt(SingleAssetFill(
            order.order_id, order.instrument_id, order.side, quantity, price,
            quantity * self.fee_per_unit, Decimal("0"), bar.end,
        ), "filled" if quantity == order.quantity else "partially_filled")


class CryptoOrderBookFillModel:
    def __init__(self, fee_rate: Decimal = Decimal("0.001")) -> None:
        self.fee_rate = fee_rate

    def attempt(self, order: SingleAssetOrder, book: OrderBookSnapshot) -> SingleFillAttempt:
        if book.event_time < order.eligible_at:
            return SingleFillAttempt(None, "not_yet_eligible")
        levels = book.asks if order.side is TradeSide.BUY else book.bids
        remaining, notional = order.quantity, Decimal("0")
        for level in levels:
            if order.limit_price is not None and not _marketable(order.side, level.price, order.limit_price):
                break
            taken = min(remaining, level.quantity)
            notional += taken * level.price
            remaining -= taken
            if remaining == 0:
                break
        quantity = order.quantity - remaining
        if quantity == 0:
            return SingleFillAttempt(None, "limit_not_reached" if order.limit_price is not None else "missing_liquidity")
        price = notional / quantity
        return SingleFillAttempt(SingleAssetFill(
            order.order_id, order.instrument_id, order.side, quantity, price,
            notional * self.fee_rate, Decimal("0"), book.event_time,
        ), "filled" if remaining == 0 else "partially_filled")


class PerpetualFillModel(CryptoOrderBookFillModel):
    def __init__(self, taker_fee_rate: Decimal = Decimal("0.0005"), maximum_mark_divergence: Decimal = Decimal("0.05")) -> None:
        super().__init__(taker_fee_rate)
        self.maximum_mark_divergence = maximum_mark_divergence

    def attempt(self, order: SingleAssetOrder, book: OrderBookSnapshot, *, mark_price: Decimal | None = None, index_price: Decimal | None = None) -> SingleFillAttempt:
        if mark_price is not None and index_price is not None and index_price > 0:
            divergence = abs(mark_price / index_price - 1)
            if divergence > self.maximum_mark_divergence:
                return SingleFillAttempt(None, "mark_index_divergence")
        return super().attempt(order, book)


class DeliveryFutureFillModel(PerpetualFillModel):
    """Order-book execution for dated futures; expiry is handled by lifecycle events."""


class CryptoOptionFillModel(CryptoOrderBookFillModel):
    """Premium order-book execution; cash settlement is handled independently."""

    def __init__(self, fee_rate: Decimal = Decimal("0.0003")) -> None:
        super().__init__(fee_rate)


class StressWrapperFillModel:
    def __init__(self, inner, *, adverse_bps: Decimal = Decimal("10"), fee_multiplier: Decimal = Decimal("2")) -> None:
        self.inner, self.adverse_bps, self.fee_multiplier = inner, adverse_bps, fee_multiplier

    def attempt(self, order: SingleAssetOrder, market, **kwargs) -> SingleFillAttempt:
        result = self.inner.attempt(order, market, **kwargs)
        if result.fill is None:
            return result
        direction = Decimal("1") if order.side is TradeSide.BUY else Decimal("-1")
        stressed_price = result.fill.price * (Decimal("1") + direction * self.adverse_bps / Decimal("10000"))
        slippage = abs(stressed_price - result.fill.price) * result.fill.quantity
        return SingleFillAttempt(replace(
            result.fill, price=stressed_price, fee=result.fill.fee * self.fee_multiplier,
            slippage=result.fill.slippage + slippage,
        ), result.reason)


def _marketable(side: TradeSide, market_price: Decimal, limit_price: Decimal) -> bool:
    return market_price <= limit_price if side is TradeSide.BUY else market_price >= limit_price
