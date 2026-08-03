from __future__ import annotations

from enum import StrEnum


class ProductFamily(StrEnum):
    SPOT = "spot"
    EQUITY = "equity"
    USD_M_FUTURES = "usd_margined_futures"
    COIN_M_FUTURES = "coin_margined_futures"
    OPTIONS = "options"


__all__ = ["ProductFamily"]
