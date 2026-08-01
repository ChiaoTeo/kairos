from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .sapi import BinanceSapiClient
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, RawPayload, RawPayloadStream


@dataclass(frozen=True, slots=True)
class BinanceEquityMarketDataConnector:
    client: BinanceSapiClient = field(default_factory=BinanceSapiClient)
    name: str = "binance_equity"
    venue: str = "binance"

    @classmethod
    def from_credential(cls, credential: str | None) -> "BinanceEquityMarketDataConnector":
        return cls(BinanceSapiClient.from_credential(credential))

    def fetch_quote(self, symbol: str, *, params: IntegrationParams | None = None) -> RawPayload:
        ticker = _symbol(symbol)
        query = {"symbol": ticker, **dict(params or {})}
        payload = self.client.get("/sapi/v1/equity/market/quote", params=query, signed=False, api_key=True)
        return _quote_row(payload, symbol=ticker, venue=self.venue)

    async def watch_ticker(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        options = dict(params or {})
        poll_seconds = float(options.pop("poll_seconds", 1.0))
        max_events = options.pop("max_events", None)
        remaining = int(max_events) if max_events is not None else None
        while remaining is None or remaining > 0:
            yield self.fetch_quote(symbol, params=options)
            if remaining is not None:
                remaining -= 1
            if remaining == 0:
                break
            if poll_seconds > 0:
                await asyncio.sleep(poll_seconds)

    def watch_order_book(self, symbol: str, *, limit: int | None = None, params: IntegrationParams | None = None) -> RawPayloadStream:
        raise NotImplementedError("Binance equity order book streaming is not implemented")

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        raise NotImplementedError("Binance equity trade streaming is not implemented")

    def watch_option_greeks(self, symbol: str, *, params: IntegrationParams | None = None) -> RawPayloadStream:
        raise NotImplementedError("Binance equity option greeks streaming is not supported")


def _quote_row(payload: object, *, symbol: str, venue: str) -> RawPayload:
    raw = payload if isinstance(payload, Mapping) else {}
    return {
        "venue": venue,
        "market": "equity",
        "source_symbol": symbol,
        "bid": _decimal(raw.get("bp") or raw.get("bidPrice") or raw.get("bid")),
        "ask": _decimal(raw.get("ap") or raw.get("askPrice") or raw.get("ask")),
        "bid_size": _decimal(raw.get("bs") or raw.get("bidQty") or raw.get("bidSize")),
        "ask_size": _decimal(raw.get("as") or raw.get("askQty") or raw.get("askSize")),
        "event_time": raw.get("E"),
        "quote_time": raw.get("T"),
        "raw": dict(raw),
    }


def _symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise ValueError("Binance equity symbol cannot be empty")
    return symbol


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["BinanceEquityMarketDataConnector"]
