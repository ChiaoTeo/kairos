from __future__ import annotations

from collections.abc import Mapping

from kairospy.core.account import AccountBookKind
from kairospy.infrastructure.integrations.domain.capabilities import ProductLine
from kairospy.infrastructure.integrations.domain.participants import ParticipantRef


def broker_book_params(broker: object, book: object, *, qualifier: str = "") -> dict[str, Mapping[str, object]]:
    participant = ParticipantRef("broker", broker)
    product = _normalize_book(book)
    if participant.name in {"binance", "okx", "okex"}:
        return _ccxt_params(product, qualifier=qualifier, broker=participant.name)
    if participant.name == "hyperliquid":
        return _same({"type": "swap"} if product in {"swap", AccountBookKind.USD_M_FUTURES.value} else {})
    return _same({} if product == AccountBookKind.DEFAULT.value else {"type": product})


def broker_book_can_trade(broker: object, book: object) -> bool:
    participant = ParticipantRef("broker", broker)
    product = _normalize_book(book)
    if participant.name == "binance" and product == AccountBookKind.EQUITY.value:
        return False
    return product not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}


def _ccxt_params(kind: str, *, qualifier: str, broker: str) -> dict[str, Mapping[str, object]]:
    if broker == "binance" and kind == AccountBookKind.EQUITY.value:
        return _same({})
    if kind == AccountBookKind.SPOT.value:
        if broker == "binance":
            return {"balance": {}, "order": {"type": "spot", "defaultType": "spot"}}
        return _same({"type": "spot", "defaultType": "spot"})
    if kind in {"swap", "perp", "future", AccountBookKind.USD_M_FUTURES.value}:
        future_type = "swap" if broker in {"okx", "okex"} else "future"
        return _same({"type": future_type, "defaultType": future_type})
    if kind == AccountBookKind.COIN_M_FUTURES.value:
        return _same({"type": "delivery", "defaultType": "delivery"})
    if kind == AccountBookKind.CROSS_MARGIN.value:
        return _same({"type": "margin", "marginMode": "cross"})
    if kind == AccountBookKind.ISOLATED_MARGIN.value:
        params: dict[str, object] = {"type": "margin", "marginMode": "isolated", "isIsolated": True}
        if qualifier:
            params["symbols"] = [qualifier]
        return _same(params)
    if kind == AccountBookKind.FUNDING.value:
        return {"balance": {"type": "funding"}, "order": {"type": "funding"}}
    if kind == AccountBookKind.EARN.value:
        return {"balance": {"type": "earn"}, "order": {"type": "earn"}}
    if kind == AccountBookKind.PORTFOLIO_MARGIN.value:
        return _same({"type": "portfolio_margin"})
    if kind == AccountBookKind.DEFAULT.value:
        return _same({})
    return _same({"type": kind})


def _same(params: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    values = dict(params)
    return {"balance": values, "order": dict(values)}


def _normalize_book(book: object) -> str:
    value = ProductLine(book).value
    aliases = {
        "spot": AccountBookKind.SPOT.value,
        "fund": AccountBookKind.FUNDING.value,
        "funding": AccountBookKind.FUNDING.value,
        "equity": AccountBookKind.EQUITY.value,
        "stocks": AccountBookKind.EQUITY.value,
        "stock": AccountBookKind.EQUITY.value,
        "earn": AccountBookKind.EARN.value,
        "swap": "swap",
        "perp": "perp",
        "perpetual": "perp",
        "future": "future",
        "futures": AccountBookKind.USD_M_FUTURES.value,
        "usd_m": AccountBookKind.USD_M_FUTURES.value,
        "usdm": AccountBookKind.USD_M_FUTURES.value,
        "usd_m_futures": AccountBookKind.USD_M_FUTURES.value,
        "coin_m": AccountBookKind.COIN_M_FUTURES.value,
        "coinm": AccountBookKind.COIN_M_FUTURES.value,
        "coin_m_futures": AccountBookKind.COIN_M_FUTURES.value,
        "margin": AccountBookKind.CROSS_MARGIN.value,
        "cross_margin": AccountBookKind.CROSS_MARGIN.value,
        "isolated_margin": AccountBookKind.ISOLATED_MARGIN.value,
    }
    return aliases.get(value, value or AccountBookKind.DEFAULT.value)


__all__ = ["broker_book_can_trade", "broker_book_params"]
