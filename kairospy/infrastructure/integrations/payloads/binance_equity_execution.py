from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from kairospy.core.account import AccountContext
from kairospy.core.execution import ExecutionUpdate
from kairospy.core.order import OrderEventKind, OrderSide, OrderType
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, RawPayload


def binance_equity_create_order_params(
    symbol: str,
    *,
    side: str,
    type: str,
    amount: object,
    price: object | None = None,
    params: IntegrationParams | None = None,
) -> dict[str, object]:
    values = dict(params or {})
    order_side = _side(side)
    order_type = _order_type(type)
    request: dict[str, object] = {
        "symbol": _symbol(symbol),
        "side": order_side,
        "orderType": order_type,
    }
    _copy_alias(values, request, "quoteAsset", "quote_asset")
    _copy_alias(values, request, "clientOrderId", "client_order_id")
    _copy_alias(values, request, "timeInForce", "time_in_force")

    if order_type == "LIMIT":
        if price is None:
            raise ValueError("Binance equity LIMIT order requires price")
        trading_session = _pop_alias(values, "tradingSession", "trading_session", required=True)
        request["price"] = _decimal_text(price, "price", quantize=Decimal("0.01"))
        request["quantity"] = _decimal_text(amount, "quantity")
        request["tradingSession"] = trading_session
    elif order_side == "BUY":
        notional = _pop_alias(values, "notional", "quoteOrderQty", "quote_order_qty", required=True)
        request["notional"] = _decimal_text(notional, "notional")
    else:
        request["quantity"] = _decimal_text(amount, "quantity")

    _reject_forbidden(values, ("price", "quantity", "qty", "notional", "quoteOrderQty", "quote_order_qty", "tradingSession", "trading_session"))
    request.update(values)
    return request


def binance_equity_cancel_order_params(
    order_id: str,
    *,
    symbol: str | None = None,
    params: IntegrationParams | None = None,
) -> dict[str, object]:
    request = {"orderId": _required_text(order_id, "order_id")}
    if symbol is not None:
        request["symbol"] = _symbol(symbol)
    request.update(dict(params or {}))
    return request


def binance_equity_order_update(
    context: AccountContext,
    raw: RawPayload,
    *,
    at: datetime | None = None,
) -> ExecutionUpdate:
    symbol = _required_text(raw.get("symbol"), "Binance equity order symbol")
    order_type = _optional_order_type(raw.get("orderType") or raw.get("type"))
    side = _optional_side(raw.get("side"))
    quantity = _optional_decimal(raw.get("qty") or raw.get("quantity"))
    filled_quantity = _optional_decimal(raw.get("filledQty") or raw.get("filledQuantity"))
    limit_price = _optional_decimal(raw.get("limitPrice") or raw.get("price"))
    return ExecutionUpdate(
        observed_at=_event_time(raw, at),
        kind=_event_kind(raw),
        order_venue_id=_required_text(raw.get("orderId") or raw.get("id"), "Binance equity order id"),
        context=context,
        instrument_id=f"instrument:equity:{symbol.lower()}",
        market_id=f"market:binance:equity:{symbol.lower()}",
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price if order_type is OrderType.LIMIT else None,
        filled_quantity=filled_quantity,
        remaining_quantity=None if quantity is None or filled_quantity is None else max(quantity - filled_quantity, Decimal("0")),
        reason=str(raw.get("status") or ""),
        source="binance_equity",
        metadata={"raw": dict(raw)},
    )


def binance_equity_trade_update(
    context: AccountContext,
    raw: RawPayload,
    *,
    at: datetime | None = None,
) -> ExecutionUpdate:
    symbol = _required_text(raw.get("symbol"), "Binance equity trade symbol")
    quantity = _decimal(raw.get("qty") or raw.get("quantity"), "trade quantity")
    price = _decimal(raw.get("price"), "trade price")
    side = _optional_side(raw.get("side"))
    settlement_currency = str(raw.get("quote") or raw.get("quoteAsset") or "USDC").strip().upper() or "USDC"
    cost = _optional_decimal(raw.get("notional") or raw.get("filledTotal"))
    if cost is None:
        cost = quantity * price
    cash_delta = cost if side is OrderSide.SELL else -cost
    fee_amount = _optional_decimal(raw.get("fee")) or Decimal("0")
    return ExecutionUpdate(
        observed_at=_event_time(raw, at, time_keys=("executionAt", "createdAt", "updatedAt")),
        kind=OrderEventKind.PARTIALLY_FILLED,
        order_venue_id=_required_text(raw.get("orderId") or raw.get("id"), "Binance equity trade order id"),
        context=context,
        instrument_id=f"instrument:equity:{symbol.lower()}",
        market_id=f"market:binance:equity:{symbol.lower()}",
        side=side,
        fill_quantity=quantity,
        fill_price=price,
        settlement_currency=settlement_currency,
        cash_delta=cash_delta,
        fee_currency=settlement_currency if fee_amount else None,
        fee_amount=fee_amount,
        source="binance_equity",
        metadata={"raw": dict(raw)},
    )


def binance_equity_error_reason(code: int | None, message: str) -> str:
    if code is None:
        return message
    known = {
        486410: "US equity disclaimer must be signed before trading",
        486411: "US equity account is not eligible for this action",
        486412: "US equity symbol is not tradable",
        486413: "US equity order violates trading session or order type restrictions",
        486414: "US equity order violates fractional trading restrictions",
    }
    detail = known.get(code)
    if detail is None:
        return message
    return detail if not message else f"{detail}: {message}"


def _event_kind(raw: RawPayload) -> OrderEventKind:
    status = str(raw.get("status") or "").strip().upper()
    if status in {"NEW", "ACCEPTED"}:
        return OrderEventKind.ACKNOWLEDGED
    if status == "PARTIALLY_FILLED":
        return OrderEventKind.PARTIALLY_FILLED
    if status == "FILLED":
        return OrderEventKind.FILLED
    if status in {"CANCELED", "CANCELLED"}:
        return OrderEventKind.CANCELED
    if status == "EXPIRED":
        return OrderEventKind.EXPIRED
    if status == "REJECTED":
        return OrderEventKind.REJECTED
    return OrderEventKind.UNKNOWN


def _event_time(raw: RawPayload, fallback: datetime | None, *, time_keys: tuple[str, ...] = ("updatedAt", "createdAt")) -> datetime:
    for key in time_keys:
        value = raw.get(key)
        if value not in {None, ""}:
            return datetime.fromtimestamp(float(Decimal(str(value)) / Decimal("1000")), tz=timezone.utc)
    event_time = fallback or datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        raise ValueError("Binance equity event timestamp fallback must be timezone-aware")
    return event_time


def _side(value: str) -> str:
    side = str(value).strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"unsupported Binance equity order side: {value}")
    return side


def _optional_side(value: object) -> OrderSide | None:
    if value in {None, ""}:
        return None
    return OrderSide(str(value).strip().lower())


def _order_type(value: str) -> str:
    order_type = str(value).strip().upper()
    if order_type not in {"MARKET", "LIMIT"}:
        raise ValueError(f"unsupported Binance equity order type: {value}")
    return order_type


def _optional_order_type(value: object) -> OrderType | None:
    if value in {None, ""}:
        return None
    return OrderType(str(value).strip().lower())


def _symbol(value: object) -> str:
    return _required_text(value, "Binance equity symbol").upper()


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _decimal_text(value: object, label: str, *, quantize: Decimal | None = None) -> str:
    number = _decimal(value, label)
    if quantize is not None:
        number = number.quantize(quantize)
    return format(number, "f")


def _decimal(value: object, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Binance equity {label} must be a decimal") from error
    if number <= 0:
        raise ValueError(f"Binance equity {label} must be positive")
    return number


def _optional_decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _pop_alias(values: dict[str, object], *keys: str, required: bool = False) -> object:
    for key in keys:
        if key in values:
            return values.pop(key)
    if required:
        raise ValueError(f"Binance equity order requires {keys[0]}")
    return None


def _copy_alias(values: dict[str, object], request: dict[str, object], canonical: str, alias: str) -> None:
    value = _pop_alias(values, canonical, alias)
    if value not in {None, ""}:
        request[canonical] = value


def _reject_forbidden(values: Mapping[str, object], forbidden: tuple[str, ...]) -> None:
    supplied = sorted(key for key in forbidden if key in values)
    if supplied:
        raise ValueError(f"Binance equity order params conflict with translated fields: {', '.join(supplied)}")


__all__ = [
    "binance_equity_cancel_order_params",
    "binance_equity_create_order_params",
    "binance_equity_error_reason",
    "binance_equity_order_update",
    "binance_equity_trade_update",
]
