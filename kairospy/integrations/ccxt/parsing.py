from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from kairospy.orders import OrderType


def ccxt_decimal(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def ccxt_optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def ccxt_required_text(raw: Mapping[str, object], key: str, *, subject: str = "ccxt value") -> str:
    value = raw.get(key)
    if value is None or not str(value).strip():
        raise ValueError(f"{subject} requires {key}")
    return str(value)


def ccxt_order_quantity(raw: Mapping[str, object], *, subject: str = "ccxt order") -> Decimal:
    amount = ccxt_decimal(raw.get("amount"))
    if amount > 0:
        return amount
    filled = ccxt_decimal(raw.get("filled"))
    remaining = ccxt_decimal(raw.get("remaining"))
    quantity = filled + remaining
    if quantity <= 0:
        raise ValueError(f"{subject} requires positive amount or remaining quantity")
    return quantity


def ccxt_order_type(raw: Mapping[str, object], price: Decimal | None) -> OrderType:
    raw_type = str(raw.get("type") or "").strip().lower()
    if raw_type == OrderType.LIMIT.value:
        return OrderType.LIMIT if price is not None else OrderType.MARKET
    if raw_type == OrderType.MARKET.value:
        return OrderType.MARKET
    return OrderType.LIMIT if price is not None else OrderType.MARKET


__all__ = [
    "ccxt_decimal",
    "ccxt_optional_decimal",
    "ccxt_order_quantity",
    "ccxt_order_type",
    "ccxt_required_text",
]
