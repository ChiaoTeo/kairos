from __future__ import annotations

from kairospy.domain.account import ProductFamily


DEFAULT_ACCOUNT_SEGMENTS: dict[str, tuple[str, ...]] = {
    "binance": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "options"),
    "okx": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures"),
    "okex": ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures"),
    "hyperliquid": ("swap",),
}


def default_account_segments(broker: str, *, fallback: str | None = None) -> tuple[str, ...]:
    key = broker.strip().lower().replace("-", "_")
    if key in DEFAULT_ACCOUNT_SEGMENTS:
        return DEFAULT_ACCOUNT_SEGMENTS[key]
    return ((fallback or ProductFamily.SPOT.value).strip().lower().replace("-", "_"),)


__all__ = ["DEFAULT_ACCOUNT_SEGMENTS", "default_account_segments"]
