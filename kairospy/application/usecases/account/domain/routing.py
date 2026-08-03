from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.domain.account import AccountBookKind, AccountBookRef


@dataclass(frozen=True, slots=True)
class AccountBookRoute:
    book: AccountBookRef
    balance_params: Mapping[str, object]
    order_params: Mapping[str, object]
    can_trade: bool


@dataclass(frozen=True, slots=True)
class AccountCapabilityPolicy:
    broker: str | None = None

    def can_trade(self, book: AccountBookRef | str) -> bool:
        kind = str(book.book) if isinstance(book, AccountBookRef) else str(book)
        return _can_trade(kind, broker=_broker_key(self.broker) if self.broker is not None else None)


@dataclass(frozen=True, slots=True)
class AccountBookRoutingService:
    broker: str | None = None
    base_params: Mapping[str, object] | None = None

    def route(
        self,
        book: AccountBookRef,
        *,
        broker: str | None = None,
        base_params: Mapping[str, object] | None = None,
    ) -> AccountBookRoute:
        broker_key = _broker_key(broker or self.broker or str(book.broker))
        params = dict(self.base_params or {})
        params.update(dict(base_params or {}))
        return AccountBookRoute(
            book,
            balance_params=params,
            order_params=dict(params),
            can_trade=AccountCapabilityPolicy(broker_key).can_trade(book),
        )


def account_book_route(
    book: AccountBookRef,
    *,
    broker: str | None = None,
    base_params: Mapping[str, object] | None = None,
) -> AccountBookRoute:
    return AccountBookRoutingService(broker=broker, base_params=base_params).route(book)


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


__all__ = [
    "AccountBookRoute",
    "AccountBookRoutingService",
    "AccountCapabilityPolicy",
    "account_book_route",
]
