from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from kairospy.reference import MarketRef, MarketResolver
from kairospy.accounts import AccountContext
from kairospy.orders import OrderEvent, OrderEventKind, OrderSide, OrderState, OrderStatus, OrderType

from kairospy.execution import FillReport, ExecutionCoordinator

from .parsing import ccxt_decimal, ccxt_optional_decimal, ccxt_order_quantity, ccxt_order_type, ccxt_required_text


def ingest_ccxt_order_update(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: Mapping[str, object],
    *,
    at: datetime | None = None,
    market_resolver: MarketResolver | None = None,
) -> OrderState:
    occurred_at = _event_time(raw, at)
    venue_order_id = ccxt_required_text(raw, "id", subject="ccxt order update")
    try:
        existing = coordinator.orders.get_by_venue_order_id(venue_order_id)
    except LookupError:
        existing = None

    kind = _event_kind(raw)
    filled = ccxt_optional_decimal(raw.get("filled"))
    if existing is None:
        if kind in {OrderEventKind.ACKNOWLEDGED, OrderEventKind.PARTIALLY_FILLED}:
            return _import_active_order(coordinator, context, raw, occurred_at=occurred_at, market_resolver=market_resolver)
        raise LookupError(f"terminal ccxt order update has no known local order: {venue_order_id}")
    if kind is OrderEventKind.ACKNOWLEDGED and existing.status is OrderStatus.ACKNOWLEDGED:
        return existing

    return coordinator.orders.record(
        OrderEvent(
            existing.request.client_order_id,
            kind,
            occurred_at,
            venue_order_id=venue_order_id,
            filled_quantity=filled if kind in {OrderEventKind.PARTIALLY_FILLED, OrderEventKind.FILLED} else None,
            reason=str(raw.get("status") or ""),
        )
    )


def ingest_ccxt_my_trade(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: Mapping[str, object],
    *,
    at: datetime | None = None,
) -> OrderState:
    occurred_at = _event_time(raw, at)
    venue_order_id = str(raw.get("order") or raw.get("orderId") or "").strip()
    if not venue_order_id:
        raise ValueError("ccxt trade update requires order or orderId")
    state = coordinator.orders.get_by_venue_order_id(venue_order_id)
    if state.request.context != context:
        raise ValueError("ccxt trade context does not match order context")
    quantity = ccxt_decimal(raw.get("amount"))
    price = ccxt_decimal(raw.get("price"))
    if quantity <= 0 or price <= 0:
        raise ValueError("ccxt trade update requires positive amount and price")
    settlement_currency = _settlement_currency(state.request.instrument_id)
    cost = ccxt_decimal(raw.get("cost"))
    if cost == 0:
        cost = quantity * price
    cash_delta = cost if state.request.side is OrderSide.SELL else -cost
    fee_currency, fee_amount = _fee(raw)
    return coordinator.ingest_fill(
        FillReport(
            state.request.client_order_id,
            occurred_at,
            quantity,
            price,
            settlement_currency,
            cash_delta=cash_delta,
            fee_currency=fee_currency,
            fee_amount=fee_amount,
            cumulative_filled_quantity=state.filled_quantity if state.filled_quantity >= quantity else None,
        )
    )


def _import_active_order(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: Mapping[str, object],
    *,
    occurred_at: datetime,
    market_resolver: MarketResolver | None = None,
) -> OrderState:
    filled = ccxt_decimal(raw.get("filled"))
    remaining = ccxt_decimal(raw.get("remaining"))
    status = OrderStatus.PARTIALLY_FILLED if filled > 0 and remaining > 0 else OrderStatus.ACKNOWLEDGED
    price = ccxt_optional_decimal(raw.get("price"))
    order_type = ccxt_order_type(raw, price)
    market = _resolve_market(ccxt_required_text(raw, "symbol", subject="ccxt order update"), market_resolver)
    return coordinator.orders.import_venue_open_order(
        context=context,
        venue_order_id=ccxt_required_text(raw, "id", subject="ccxt order update"),
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        side=OrderSide(ccxt_required_text(raw, "side", subject="ccxt order update").lower()),
        quantity=ccxt_order_quantity(raw, subject="ccxt order update"),
        order_type=order_type,
        limit_price=price if order_type is OrderType.LIMIT else None,
        status=status,
        filled_quantity=filled,
        observed_at=occurred_at,
    )


def _event_kind(raw: Mapping[str, object]) -> OrderEventKind:
    status = str(raw.get("status") or "").strip().lower()
    filled = ccxt_decimal(raw.get("filled"))
    remaining = ccxt_decimal(raw.get("remaining"))
    amount = ccxt_decimal(raw.get("amount"))
    if status in {"closed", "filled"}:
        return OrderEventKind.FILLED
    if status in {"canceled", "cancelled"}:
        return OrderEventKind.CANCELED
    if status == "rejected":
        return OrderEventKind.REJECTED
    if status == "expired":
        return OrderEventKind.EXPIRED
    if filled > 0 and (remaining > 0 or amount == 0 or filled < amount):
        return OrderEventKind.PARTIALLY_FILLED
    if status in {"open", "new"}:
        return OrderEventKind.ACKNOWLEDGED
    return OrderEventKind.UNKNOWN


def _event_time(raw: Mapping[str, object], fallback: datetime | None) -> datetime:
    value = raw.get("timestamp") or raw.get("lastTradeTimestamp")
    if value is not None:
        return datetime.fromtimestamp(float(Decimal(str(value)) / Decimal("1000")), tz=timezone.utc)
    event_time = fallback or datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        raise ValueError("ccxt event timestamp fallback must be timezone-aware")
    return event_time


def _settlement_currency(symbol: str) -> str:
    if "/" not in symbol:
        parts = symbol.split(":")
        if len(parts) >= 4 and parts[0] == "instrument":
            return parts[-1].upper() or "USD"
        return "USD"
    return symbol.split("/", 1)[1].split(":", 1)[0] or "USD"


def _resolve_market(symbol: str, market_resolver: MarketResolver | None) -> MarketRef:
    if market_resolver is None:
        return MarketRef.ephemeral(venue="ccxt", market="unknown", source_symbol=symbol)
    try:
        return market_resolver.resolve(symbol)
    except KeyError:
        return MarketRef.ephemeral(venue="ccxt", market="unknown", source_symbol=symbol)


def _fee(raw: Mapping[str, object]) -> tuple[str | None, Decimal]:
    fee = raw.get("fee")
    if not isinstance(fee, Mapping):
        return None, Decimal("0")
    amount = ccxt_decimal(fee.get("cost"))
    currency = fee.get("currency")
    return (None if currency is None else str(currency), amount)


__all__ = ["ingest_ccxt_my_trade", "ingest_ccxt_order_update"]
