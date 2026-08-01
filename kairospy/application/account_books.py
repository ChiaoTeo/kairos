from __future__ import annotations

from kairospy.core.account import AccountBookKind


DEFAULT_ACCOUNT_BOOKS: dict[str, tuple[str, ...]] = {
    "binance": ("spot", "equity", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding"),
    "okx": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding"),
    "okex": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding"),
    "hyperliquid": ("swap",),
}


def default_account_books(broker: str, *, fallback: str | None = None) -> tuple[str, ...]:
    key = broker.strip().lower().replace("-", "_")
    if key in DEFAULT_ACCOUNT_BOOKS:
        return DEFAULT_ACCOUNT_BOOKS[key]
    return ((fallback or AccountBookKind.SPOT.value).strip().lower().replace("-", "_"),)


__all__ = ["DEFAULT_ACCOUNT_BOOKS", "default_account_books"]
