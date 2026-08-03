from __future__ import annotations

from dataclasses import dataclass

from kairospy.infrastructure.integrations.services.clients.binance_equity import BinanceEquityRestClient


@dataclass(slots=True)
class BinanceEquityMarketOperations:
    client: BinanceEquityRestClient

    def exchange_info(self, *, symbol: str | None = None) -> object:
        return self.client.get("/sapi/v1/equity/market/exchangeInfo", params={"symbol": symbol})

    def latest_quote(self, *, symbol: str) -> object:
        normalized = symbol.strip()
        if not normalized:
            raise ValueError("Binance Stocks Trading quote symbol is required")
        return self.client.get("/sapi/v1/equity/market/quote", params={"symbol": normalized.upper()})


__all__ = ["BinanceEquityMarketOperations"]
