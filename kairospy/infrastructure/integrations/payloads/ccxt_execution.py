from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from kairospy.core.reference import MarketRef, MarketResolver
from kairospy.core.account import AccountContext
from kairospy.core.order import OrderEventKind, OrderSide, OrderState, OrderStatus, OrderType

from kairospy.core.execution import ExecutionCoordinator, ExecutionUpdate

from .ccxt_parsing import ccxt_decimal, ccxt_optional_decimal, ccxt_order_quantity, ccxt_order_type, ccxt_required_text
from kairospy.infrastructure.integrations.payloads.types import RawPayload


def ingest_ccxt_order_update(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: RawPayload,
    *,
    at: datetime | None = None,
    market_resolver: MarketResolver | None = None,
) -> OrderState:
    return coordinator.apply_execution_update(
        ccxt_order_update(context, raw, at=at, market_resolver=market_resolver)
    )


def ccxt_order_update(
    context: AccountContext,
    raw: RawPayload,
    *,
    at: datetime | None = None,
    market_resolver: MarketResolver | None = None,
) -> ExecutionUpdate:
    price = ccxt_optional_decimal(raw.get("price"))
    order_type = ccxt_order_type(raw, price)
    symbol = _optional_text(raw.get("symbol"))
    market = None if symbol is None else _resolve_market(symbol, market_resolver)
    side = _optional_text(raw.get("side"))
    return ExecutionUpdate(
        observed_at=_event_time(raw, at),
        kind=_event_kind(raw),
        order_venue_id=ccxt_required_text(raw, "id", subject="ccxt order update"),
        context=context,
        instrument_id=None if market is None else market.instrument_id,
        market_id=None if market is None else market.market_id,
        side=None if side is None else OrderSide(side.lower()),
        quantity=_optional_order_quantity(raw),
        order_type=order_type,
        limit_price=price if order_type is OrderType.LIMIT else None,
        filled_quantity=ccxt_optional_decimal(raw.get("filled")),
        remaining_quantity=ccxt_optional_decimal(raw.get("remaining")),
        reason=str(raw.get("status") or ""),
        source="ccxt",
        metadata={"raw": dict(raw)},
    )


def ingest_ccxt_my_trade(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: RawPayload,
    *,
    at: datetime | None = None,
) -> OrderState:
    return coordinator.apply_execution_update(ccxt_trade_update(coordinator, context, raw, at=at))


def ccxt_trade_update(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: RawPayload,
    *,
    at: datetime | None = None,
) -> ExecutionUpdate:
    occurred_at = _event_time(raw, at)
    order_venue_id = str(raw.get("order") or raw.get("orderId") or "").strip()
    if not order_venue_id:
        raise ValueError("ccxt trade update requires order or orderId")
    state = coordinator.orders.get_by_order_venue_id(order_venue_id)
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
    return ExecutionUpdate(
        observed_at=occurred_at,
        kind=OrderEventKind.PARTIALLY_FILLED,
        order_venue_id=order_venue_id,
        order_id=state.request.order_id,
        context=context,
        fill_quantity=quantity,
        fill_price=price,
        settlement_currency=settlement_currency,
        cash_delta=cash_delta,
        fee_currency=fee_currency,
        fee_amount=fee_amount,
        filled_quantity=state.filled_quantity if state.filled_quantity >= quantity else None,
        source="ccxt",
        metadata={"raw": dict(raw)},
    )


def _import_active_order(
    coordinator: ExecutionCoordinator,
    context: AccountContext,
    raw: RawPayload,
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
    return coordinator.orders.import_order_venue_open_order(
        context=context,
        order_venue_id=ccxt_required_text(raw, "id", subject="ccxt order update"),
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


def _event_kind(raw: RawPayload) -> OrderEventKind:
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


def _event_time(raw: RawPayload, fallback: datetime | None) -> datetime:
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


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_order_quantity(raw: RawPayload) -> Decimal | None:
    if raw.get("amount") in {None, ""} and raw.get("filled") in {None, ""} and raw.get("remaining") in {None, ""}:
        return None
    return ccxt_order_quantity(raw, subject="ccxt order update")


def _fee(raw: RawPayload) -> tuple[str | None, Decimal]:
    fee = raw.get("fee")
    if not isinstance(fee, Mapping):
        return None, Decimal("0")
    amount = ccxt_decimal(fee.get("cost"))
    currency = fee.get("currency")
    return (None if currency is None else str(currency), amount)


__all__ = ["ccxt_order_update", "ccxt_trade_update", "ingest_ccxt_my_trade", "ingest_ccxt_order_update"]
