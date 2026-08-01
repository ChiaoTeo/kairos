from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from kairospy.application.domain.reference.builders import catalog_from_equity_rows
from kairospy.core.reference import MarketDefinition, ReferenceCatalog

from .sapi import BinanceSapiClient
from kairospy.infrastructure.integrations.types import IntegrationParams, RawPayload, RawPayloadRows


@dataclass(frozen=True, slots=True)
class BinanceEquityReferenceConnector:
    client: BinanceSapiClient = field(default_factory=BinanceSapiClient)
    name: str = "binance_equity"
    venue: str = "binance"

    @classmethod
    def from_credential(cls, credential: str | None) -> "BinanceEquityReferenceConnector":
        return cls(BinanceSapiClient.from_credential(credential))

    def fetch_markets(
        self,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        payload = self.client.get("/sapi/v1/equity/market/exchangeInfo", params=params, signed=False, api_key=True)
        return tuple(_market_row(item, venue=self.venue) for item in _symbols(payload))

    def fetch_market_definitions(
        self,
        *,
        as_of: datetime | None = None,
        params: IntegrationParams | None = None,
    ) -> tuple[MarketDefinition, ...]:
        effective_from = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return catalog_from_equity_rows(self.fetch_markets(params=params), effective_from=effective_from).list_markets(at=effective_from)

    def fetch_reference_catalog(
        self,
        *,
        as_of: datetime | None = None,
        params: IntegrationParams | None = None,
    ) -> ReferenceCatalog:
        effective_from = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return catalog_from_equity_rows(self.fetch_markets(params=params), effective_from=effective_from)


def _symbols(payload: object) -> tuple[RawPayload, ...]:
    if isinstance(payload, Mapping):
        symbols = payload.get("symbols")
        if isinstance(symbols, list | tuple):
            return tuple(item for item in symbols if isinstance(item, Mapping))
        return ()
    if isinstance(payload, list | tuple):
        return tuple(item for item in payload if isinstance(item, Mapping))
    return ()


def _market_row(raw: RawPayload, *, venue: str) -> RawPayload:
    ticker = str(raw.get("symbol") or raw.get("underlyingAsset") or "").strip().upper()
    quote_asset = str(raw.get("quoteAsset") or raw.get("quote_asset") or "USDC").strip().upper() or "USDC"
    tradability = str(raw.get("tradability") or "").strip().upper()
    return {
        "venue": venue,
        "ticker": ticker,
        "source_symbol": ticker,
        "currency": "USD",
        "quote_asset": quote_asset,
        "active": False if tradability == "NONE" else None,
        "status": _status(raw),
        "price_tick": Decimal("0.01"),
        "amount_tick": _decimal(raw.get("stepSize") or raw.get("amountTick") or raw.get("lotSize")),
        "min_amount": _decimal(raw.get("minQty") or raw.get("minQuantity")),
        "min_notional": _decimal(raw.get("minNotional")),
        "tradability": tradability or None,
        "tradabilityUpdateTime": raw.get("tradabilityUpdateTime"),
        "overnightSupported": raw.get("overnightSupported"),
        "fractionable": raw.get("fractionable"),
        "fractionableEh": raw.get("fractionableEh"),
        "extendedSession": raw.get("extendedSession"),
        "maxNumOrders": raw.get("maxNumOrders"),
        "maxQty": raw.get("maxQty"),
        "maxNotional": raw.get("maxNotional"),
        "multiplierUp": raw.get("multiplierUp"),
        "multiplierDown": raw.get("multiplierDown"),
        "listingTime": raw.get("listingTime"),
        "delistingTime": raw.get("delistingTime"),
        "broker": "binance",
        "settlement_asset": quote_asset,
        "raw": dict(raw),
    }


def _status(raw: RawPayload) -> str:
    tradability = str(raw.get("tradability") or "").strip().upper()
    if tradability == "NONE":
        return "halted"
    if raw.get("delistingTime") not in {None, "", 0, "0"}:
        return "delisted"
    return "active" if tradability else "unknown"


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["BinanceEquityReferenceConnector"]
