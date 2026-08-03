from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.domain.market import Quote
from kairospy.domain.reference import ReferenceCatalog
from kairospy.domain.reference.identity import AssetId, InstrumentId, ListingId, MarketId, MarketTypeId, SourceSymbol
from kairospy.domain.reference.model import Asset, AssetType, InstrumentDefinition, InstrumentType, ListingDefinition, MarketDefinition, MarketStatus
from kairospy.domain.reference.markets import MarketRef


@dataclass(slots=True)
class BinanceEquityPayloadTranslator:
    def latest_quote(
        self,
        payload: object,
        *,
        market: MarketRef | None = None,
        observed_at: datetime | None = None,
    ) -> Quote | None:
        if not isinstance(payload, Mapping) or not payload:
            return None
        symbol = _text(payload.get("symbol"))
        if not symbol:
            return None
        market = market or MarketRef.ephemeral(venue="binance", market="equity", source_symbol=symbol)
        return Quote(
            instrument_id=market.instrument_id,
            market_id=market.market_id,
            market_key=market.market_key,
            time=observed_at or datetime.now(timezone.utc),
            bid=_decimal(payload.get("bidPrice")),
            ask=_decimal(payload.get("askPrice")),
            bid_size=_decimal(payload.get("bidSize")),
            ask_size=_decimal(payload.get("askSize")),
            source="binance",
        )

    def catalog(self, payload: object, *, as_of: datetime) -> ReferenceCatalog:
        if not isinstance(payload, Mapping) or not isinstance(payload.get("symbols"), list):
            raise ValueError("Binance Stocks Trading exchange info must contain symbols")
        assets: dict[str, Asset] = {}
        instruments: list[InstrumentDefinition] = []
        listings: list[ListingDefinition] = []
        markets: list[MarketDefinition] = []
        for item in payload["symbols"]:
            if not isinstance(item, Mapping):
                continue
            symbol = _text(item.get("symbol"))
            if not symbol:
                continue
            asset_id = AssetId(f"asset:equity:{symbol.lower()}")
            assets.setdefault(str(asset_id), Asset(asset_id=asset_id, asset_type=AssetType.EQUITY, symbol=symbol, effective_from=as_of))
            instrument_id = InstrumentId(f"instrument:equity:{symbol.lower()}")
            listing_id = ListingId(f"listing:binance:equity:{symbol.lower()}")
            market_id = MarketId(f"market:binance:equity:{symbol.lower()}")
            status = MarketStatus.ACTIVE if _text(item.get("tradability")) != "NONE" else MarketStatus.HALTED
            instruments.append(InstrumentDefinition(instrument_id=instrument_id, instrument_type=InstrumentType.EQUITY, base_asset_id=asset_id, display_name=symbol, effective_from=as_of))
            listings.append(ListingDefinition(listing_id=listing_id, instrument_id=instrument_id, venue="binance", trading_symbol=SourceSymbol(symbol), status=status, effective_from=as_of))
            markets.append(MarketDefinition(market_id=market_id, instrument_id=instrument_id, listing_id=listing_id, venue="binance", market=MarketTypeId("equity"), source_symbol=SourceSymbol(symbol), status=status, amount_tick=_decimal(item.get("stepSize")), min_amount=_decimal(item.get("minQty")), min_notional=_decimal(item.get("minNotional")), effective_from=as_of))
        return ReferenceCatalog(assets=tuple(assets.values()), instruments=tuple(instruments), listings=tuple(listings), markets=tuple(markets))


def _text(value: object) -> str:
    return "" if value is None else str(value).strip().upper()


def _decimal(value: object) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except Exception:
        return None


__all__ = ["BinanceEquityPayloadTranslator"]
