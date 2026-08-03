"""Optional CCXT driver for normalized public market requests.

CCXT is imported lazily and its objects never leave this module. The
connection service translates returned OHLCV rows into system models.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CcxtMarketDriver:
    exchange_id: str = "binance"
    options: Mapping[str, object] = field(default_factory=dict)
    _exchange: Any = field(init=False, default=None, repr=False)

    def _client(self) -> Any:
        if self._exchange is None:
            try:
                import ccxt  # type: ignore[import-not-found]
            except ImportError as error:
                raise RuntimeError("CCXT public market support requires the crypto extra") from error
            exchange_type = getattr(ccxt, self.exchange_id, None)
            if exchange_type is None:
                raise ValueError(f"unsupported CCXT exchange: {self.exchange_id}")
            self._exchange = exchange_type(dict(self.options))
        return self._exchange

    def ohlcv(self, symbol: str, *, timeframe: str, since: int | None, limit: int, until: int | None = None) -> object:
        values = self._client().fetch_ohlcv(_ccxt_symbol(symbol), timeframe=timeframe, since=since, limit=limit)
        if until is None:
            return values
        return [row for row in values if isinstance(row, (list, tuple)) and row and int(row[0]) <= until]


__all__ = ["CcxtMarketDriver"]


def _ccxt_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if "/" in value:
        return value
    for quote in ("USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "BNB"):
        if value.endswith(quote) and len(value) > len(quote):
            return f"{value[:-len(quote)]}/{quote}"
    return value
