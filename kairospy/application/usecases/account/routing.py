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


def account_book_route(
    book: AccountBookRef,
    *,
    broker: str | None = None,
    provider: str | None = None,
    base_params: Mapping[str, object] | None = None,
) -> AccountBookRoute:
    broker_key = _broker_key(broker or provider or str(book.broker))
    params = dict(base_params or {})
    return AccountBookRoute(book, balance_params=params, order_params=dict(params), can_trade=_can_trade(str(book.book), broker=broker_key))


def account_book_routes(
    books: tuple[AccountBookRef, ...],
    *,
    broker: str | None = None,
    provider: str | None = None,
) -> tuple[AccountBookRoute, ...]:
    return tuple(account_book_route(book, broker=broker, provider=provider) for book in books)


def _normalize_kind(kind: str) -> str:
    value = kind.strip().lower()
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


def _can_trade(kind: str, *, broker: str | None = None) -> bool:
    normalized = _normalize_kind(kind)
    if (broker or "").strip().lower() == "binance" and normalized == AccountBookKind.EQUITY.value:
        return False
    return normalized not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}


def _broker_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


__all__ = ["AccountBookRoute", "account_book_route", "account_book_routes"]
