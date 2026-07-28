from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountSnapshot,
    AccountSource,
    MarginScope,
    MarginState,
    OpenOrderSnapshot,
)
from kairospy.core.reference import MarketRef, MarketResolver
from kairospy.core.order import OrderOrigin, OrderSide, OrderState, OrderStatus, OrderType

from .ccxt_parsing import ccxt_decimal, ccxt_optional_decimal, ccxt_order_quantity, ccxt_order_type, ccxt_required_text
from .ccxt_execution import ingest_ccxt_my_trade, ingest_ccxt_order_update


class CcxtAccountPayloadAdapter:
    def __init__(self, market_resolver: MarketResolver | None = None) -> None:
        self.market_resolver = market_resolver

    def bind_market_resolver(self, market_resolver: MarketResolver) -> None:
        self.market_resolver = market_resolver

    def snapshot(
        self,
        context: AccountContext,
        raw_balance: Mapping[str, object],
        raw_orders: tuple[Mapping[str, object], ...],
        *,
        observed_at: datetime,
    ) -> AccountSnapshot:
        return ccxt_balance_snapshot(
            context,
            raw_balance,
            at=observed_at,
            open_orders=tuple(_open_order_snapshot(order, market_resolver=self.market_resolver) for order in raw_orders),
            raw={"balance": dict(raw_balance), "open_orders": raw_orders},
            market_resolver=self.market_resolver,
        )

    def import_open_order(
        self,
        context: AccountContext,
        coordinator,
        raw: Mapping[str, object],
        *,
        observed_at: datetime,
    ) -> OrderState:
        return import_ccxt_open_order(context, coordinator, raw, observed_at=observed_at, market_resolver=self.market_resolver)

    def balance_snapshot(
        self,
        context: AccountContext,
        raw_balance: Mapping[str, object],
        *,
        at: datetime,
        open_orders: tuple[OpenOrderSnapshot, ...] = (),
    ) -> AccountSnapshot:
        return ccxt_balance_snapshot(context, raw_balance, at=at, open_orders=open_orders, market_resolver=self.market_resolver)

    def ingest_order_update(self, coordinator, context: AccountContext, raw: Mapping[str, object]) -> OrderState:
        return ingest_ccxt_order_update(coordinator, context, raw, market_resolver=self.market_resolver)

    def ingest_trade_update(self, coordinator, context: AccountContext, raw: Mapping[str, object]) -> OrderState:
        return ingest_ccxt_my_trade(coordinator, context, raw)


CcxtAccountBootstrapParser = CcxtAccountPayloadAdapter


def ccxt_balance_snapshot(
    context: AccountContext,
    raw_balance: Mapping[str, object],
    *,
    at: datetime,
    open_orders: tuple[OpenOrderSnapshot, ...] = (),
    raw: Mapping[str, object] | None = None,
    market_resolver: MarketResolver | None = None,
) -> AccountSnapshot:
    if at.tzinfo is None:
        raise ValueError("balance snapshot timestamp must be timezone-aware")
    return AccountSnapshot(
        context,
        balances=_balances_from_ccxt(raw_balance),
        margins=_margins_from_ccxt(raw_balance, market_resolver=market_resolver),
        open_orders=open_orders,
        observed_at=at,
        source=AccountSource.VENUE,
        raw=dict(raw or {"balance": dict(raw_balance)}),
    )


def import_ccxt_open_order(
    context: AccountContext,
    coordinator,
    raw: Mapping[str, object],
    *,
    observed_at: datetime,
    market_resolver: MarketResolver | None = None,
) -> OrderState:
    filled = ccxt_decimal(raw.get("filled"))
    remaining = ccxt_decimal(raw.get("remaining"))
    status = OrderStatus.PARTIALLY_FILLED if filled > 0 and remaining > 0 else OrderStatus.ACKNOWLEDGED
    price = ccxt_optional_decimal(raw.get("price"))
    order_type = ccxt_order_type(raw, price)
    market = _resolve_market(ccxt_required_text(raw, "symbol", subject="ccxt order"), market_resolver)
    return coordinator.orders.import_venue_open_order(
        context=context,
        venue_order_id=ccxt_required_text(raw, "id", subject="ccxt order"),
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        side=OrderSide(ccxt_required_text(raw, "side", subject="ccxt order").lower()),
        quantity=ccxt_order_quantity(raw, subject="ccxt order"),
        order_type=order_type,
        limit_price=price if order_type is OrderType.LIMIT else None,
        status=status,
        filled_quantity=filled,
        observed_at=observed_at,
        origin=OrderOrigin.VENUE,
    )


def _balances_from_ccxt(raw: Mapping[str, object]) -> tuple[AccountBalance, ...]:
    free_values = _mapping(raw.get("free"))
    locked_values = _mapping(raw.get("used")) or _mapping(raw.get("locked"))
    total_values = _mapping(raw.get("total"))
    currencies = sorted(set(free_values) | set(locked_values) | set(total_values))
    balances: list[AccountBalance] = []
    for currency in currencies:
        free = ccxt_decimal(free_values.get(currency))
        if currency in total_values:
            total = ccxt_decimal(total_values.get(currency))
            locked = total - free
            if locked < 0:
                locked = ccxt_decimal(locked_values.get(currency))
                total = free + locked
        else:
            locked = ccxt_decimal(locked_values.get(currency))
            total = free + locked
        if total == 0 and free == 0 and locked == 0:
            continue
        balances.append(AccountBalance(currency, total, free, locked, AccountSource.VENUE))
    return tuple(balances)


def _margins_from_ccxt(raw: Mapping[str, object], *, market_resolver: MarketResolver | None = None) -> tuple[MarginState, ...]:
    margins: list[MarginState] = []
    margins.extend(_account_margins_from_ccxt(raw))
    for value in _sequence(raw.get("positions")):
        position = _mapping(value)
        margin = _instrument_margin_from_ccxt(position, market_resolver=market_resolver)
        if margin is not None:
            margins.append(margin)
    for value in _sequence(raw.get("assets")):
        asset = _mapping(value)
        margin = _asset_margin_from_ccxt(asset)
        if margin is not None:
            margins.append(margin)
    return tuple(margins)


def _account_margins_from_ccxt(raw: Mapping[str, object]) -> tuple[MarginState, ...]:
    currency = _margin_currency(raw)
    initial = _first_decimal(raw, "totalInitialMargin", "initialMargin", "totalMargin")
    maintenance = _first_decimal(raw, "totalMaintMargin", "totalMaintenanceMargin", "maintenanceMargin")
    available = _first_optional_decimal(raw, "availableBalance", "availableMargin", "freeCollateral")
    if currency is None or (initial == 0 and maintenance == 0 and available is None):
        return ()
    return (MarginState(currency, initial, maintenance, AccountSource.VENUE, available=available),)


def _instrument_margin_from_ccxt(raw: Mapping[str, object], *, market_resolver: MarketResolver | None = None) -> MarginState | None:
    raw_symbol = _first_text(raw, "symbol", "instrumentId", "instrument_id")
    currency = _margin_currency(raw)
    initial = _first_decimal(raw, "initialMargin", "initial", "positionInitialMargin")
    maintenance = _first_decimal(raw, "maintenanceMargin", "maintMargin", "positionMaintenanceMargin")
    available = _first_optional_decimal(raw, "availableMargin", "available", "freeCollateral")
    if raw_symbol is None or currency is None or (initial == 0 and maintenance == 0 and available is None):
        return None
    market = _resolve_market(raw_symbol, market_resolver)
    return MarginState(
        currency,
        initial,
        maintenance,
        AccountSource.VENUE,
        scope=MarginScope.INSTRUMENT,
        instrument_id=market.instrument_id,
        available=available,
    )


def _asset_margin_from_ccxt(raw: Mapping[str, object]) -> MarginState | None:
    currency = _first_text(raw, "asset", "currency", "marginAsset")
    initial = _first_decimal(raw, "initialMargin", "initial", "walletInitialMargin")
    maintenance = _first_decimal(raw, "maintenanceMargin", "maintMargin", "walletMaintenanceMargin")
    available = _first_optional_decimal(raw, "availableBalance", "availableMargin", "freeCollateral")
    if currency is None or (initial == 0 and maintenance == 0 and available is None):
        return None
    return MarginState(currency, initial, maintenance, AccountSource.VENUE, available=available)


def _open_order_snapshot(raw: Mapping[str, object], *, market_resolver: MarketResolver | None = None) -> OpenOrderSnapshot:
    order_id = ccxt_required_text(raw, "id", subject="ccxt order")
    market = _resolve_market(ccxt_required_text(raw, "symbol", subject="ccxt order"), market_resolver)
    quantity = _open_quantity(raw)
    cost = ccxt_decimal(raw.get("cost"))
    quote_currency = _quote_currency(market.source_symbol)
    return OpenOrderSnapshot(
        order_id,
        market.instrument_id,
        ccxt_required_text(raw, "side", subject="ccxt order").lower(),
        quantity,
        AccountSource.VENUE,
        reserved_currency=quote_currency if cost > 0 else None,
        reserved_amount=cost,
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _margin_currency(raw: Mapping[str, object]) -> str | None:
    return _first_text(raw, "marginCurrency", "marginAsset", "currency", "asset")


def _first_text(raw: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    info = _mapping(raw.get("info"))
    for key in keys:
        value = info.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _first_decimal(raw: Mapping[str, object], *keys: str) -> Decimal:
    value = _first_value(raw, *keys)
    return ccxt_decimal(value)


def _first_optional_decimal(raw: Mapping[str, object], *keys: str) -> Decimal | None:
    value = _first_value(raw, *keys)
    return None if value is None else ccxt_decimal(value)


def _first_value(raw: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in raw:
            return raw.get(key)
    info = _mapping(raw.get("info"))
    for key in keys:
        if key in info:
            return info.get(key)
    return None


def _open_quantity(raw: Mapping[str, object]) -> Decimal:
    remaining = ccxt_decimal(raw.get("remaining"))
    if remaining > 0:
        return remaining
    return ccxt_order_quantity(raw, subject="ccxt order")


def _quote_currency(symbol: str) -> str | None:
    if "/" not in symbol:
        return None
    quote = symbol.split("/", 1)[1].split(":", 1)[0].strip()
    return quote or None


def _resolve_market(symbol: str, market_resolver: MarketResolver | None) -> MarketRef:
    if market_resolver is None:
        return MarketRef.ephemeral(venue="ccxt", market="unknown", source_symbol=symbol)
    try:
        return market_resolver.resolve(symbol)
    except KeyError:
        return MarketRef.ephemeral(venue="ccxt", market="unknown", source_symbol=symbol)


__all__ = [
    "CcxtAccountBootstrapParser",
    "CcxtAccountPayloadAdapter",
    "ccxt_balance_snapshot",
    "import_ccxt_open_order",
]
