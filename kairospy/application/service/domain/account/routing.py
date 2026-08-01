from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.core.account import AccountBookKind, AccountBookRef


@dataclass(frozen=True, slots=True)
class AccountBookRoute:
    book: AccountBookRef
    balance_params: Mapping[str, object]
    order_params: Mapping[str, object]
    can_trade: bool


def account_book_route(book: AccountBookRef, *, provider: str | None = None, base_params: Mapping[str, object] | None = None) -> AccountBookRoute:
    provider_key = (provider or str(book.broker)).strip().lower()
    defaults = _provider_book_params(provider_key, str(book.book), qualifier=book.qualifier)
    balance_params = {**defaults["balance"], **dict(base_params or {})}
    order_params = {**defaults["order"], **dict(base_params or {})}
    return AccountBookRoute(book, balance_params=balance_params, order_params=order_params, can_trade=_can_trade(str(book.book)))


def account_book_routes(books: tuple[AccountBookRef, ...], *, provider: str | None = None) -> tuple[AccountBookRoute, ...]:
    return tuple(account_book_route(book, provider=provider) for book in books)


def _provider_book_params(provider: str, kind: str, *, qualifier: str) -> dict[str, Mapping[str, object]]:
    normalized = _normalize_kind(kind)
    if provider in {"binance", "okx", "okex"}:
        return _ccxt_params(normalized, qualifier=qualifier, provider=provider)
    if provider == "hyperliquid":
        return _same({"type": "swap"} if normalized in {"swap", AccountBookKind.USD_M_FUTURES.value} else {})
    return _same({} if normalized == AccountBookKind.DEFAULT.value else {"type": normalized})


def _ccxt_params(kind: str, *, qualifier: str, provider: str) -> dict[str, Mapping[str, object]]:
    if kind == AccountBookKind.SPOT.value:
        if provider == "binance":
            return {"balance": {}, "order": {"type": "spot", "defaultType": "spot"}}
        return _same({"type": "spot", "defaultType": "spot"})
    if kind in {"swap", "perp", "future", AccountBookKind.USD_M_FUTURES.value}:
        future_type = "swap" if provider in {"okx", "okex"} else "future"
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


def _normalize_kind(kind: str) -> str:
    value = kind.strip().lower()
    aliases = {
        "spot": AccountBookKind.SPOT.value,
        "fund": AccountBookKind.FUNDING.value,
        "funding": AccountBookKind.FUNDING.value,
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


def _can_trade(kind: str) -> bool:
    normalized = _normalize_kind(kind)
    return normalized not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}


__all__ = ["AccountBookRoute", "account_book_route", "account_book_routes"]
