"""Binance reference data clients and product definition builders."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.ports import ReferenceDataRequest
from kairospy.trading.capability import ReferenceCapabilities
from kairospy.trading.identity import AssetId, InstrumentId, VenueId
from kairospy.trading.product import (
    ContractType,
    CryptoOptionSpec,
    CryptoSpotSpec,
    ExerciseStyle,
    FutureSpec,
    OptionRight,
    PerpetualSpec,
    ProductType,
)
from kairospy.reference import (
    AssetDefinition,
    AssetType,
    ListingDefinition,
    ListingId,
    ReferenceCatalog,
    TradingRules,
    VenueDefinition,
    VenueType,
)
from kairospy.reference.factory import publish_instrument

from .rest_transport import BinanceTransport, RateLimiter


BINANCE_SPOT_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.CRYPTO_SPOT}),
)
BINANCE_FUTURES_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.PERPETUAL, ProductType.FUTURE}),
)
BINANCE_OPTIONS_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.CRYPTO_OPTION}),
)


class BinanceSpotReferenceDataClient:
    venue_id = VenueId("binance")
    capabilities = BINANCE_SPOT_REFERENCE_CAPABILITIES

    def __init__(self, transport: BinanceTransport, limiter: RateLimiter | None = None) -> None:
        self.transport, self.limiter = transport, limiter or RateLimiter(1200, 60)

    def sync(self, request: ReferenceDataRequest) -> ReferenceCatalog:
        if request.product_type is not ProductType.CRYPTO_SPOT:
            raise ValueError("Binance spot reference data client requires crypto_spot request")
        symbols = request.symbols
        self.limiter.acquire()
        data = self.transport.request("GET", "/api/v3/exchangeInfo")
        wanted = set(symbols)
        catalog = ReferenceCatalog()
        for item in data["symbols"]:
            if item["symbol"] in wanted and item.get("status") == "TRADING":
                _spot_definition(catalog, item)
        return catalog


class BinanceFuturesReferenceDataClient:
    venue_id = VenueId("binance")
    capabilities = BINANCE_FUTURES_REFERENCE_CAPABILITIES

    def __init__(self, transport: BinanceTransport, *, inverse: bool = False, limiter: RateLimiter | None = None) -> None:
        self.transport, self.inverse, self.limiter = transport, inverse, limiter or RateLimiter(1200, 60)

    def sync(self, request: ReferenceDataRequest) -> ReferenceCatalog:
        if request.product_type not in {ProductType.PERPETUAL, ProductType.FUTURE}:
            raise ValueError("Binance futures reference data client requires future/perpetual request")
        symbols = request.symbols
        path = "/dapi/v1/exchangeInfo" if self.inverse else "/fapi/v1/exchangeInfo"
        self.limiter.acquire()
        data = self.transport.request("GET", path)
        wanted = set(symbols)
        rows = [item for item in data["symbols"] if item["symbol"] in wanted]
        catalog = ReferenceCatalog()
        if request.product_type is ProductType.PERPETUAL:
            for item in rows:
                if item.get("contractType") == "PERPETUAL":
                    _perpetual_definition(catalog, item, inverse=self.inverse)
        else:
            for item in rows:
                if item.get("contractType") != "PERPETUAL":
                    _future_definition(catalog, item, inverse=self.inverse)
        return catalog


class BinanceOptionsReferenceDataClient:
    venue_id = VenueId("binance")
    capabilities = BINANCE_OPTIONS_REFERENCE_CAPABILITIES

    def __init__(self, transport: BinanceTransport, limiter: RateLimiter | None = None) -> None:
        self.transport, self.limiter = transport, limiter or RateLimiter(1200, 60)

    def sync(self, request: ReferenceDataRequest) -> ReferenceCatalog:
        if request.product_type is not ProductType.CRYPTO_OPTION:
            raise ValueError("Binance options reference data client requires crypto_option request")
        symbols = request.symbols
        self.limiter.acquire()
        data = self.transport.request("GET", "/eapi/v1/exchangeInfo")
        wanted = set(symbols)
        catalog = ReferenceCatalog()
        for item in data.get("optionSymbols", []):
            if item["symbol"] in wanted:
                _option_definition(catalog, item)
        return catalog


def _spot_definition(catalog: ReferenceCatalog, row):
    filters = {item["filterType"]: item for item in row["filters"]}
    lot, price = filters["LOT_SIZE"], filters["PRICE_FILTER"]
    notional = filters.get("MIN_NOTIONAL", {}).get("minNotional")
    symbol = row["symbol"]
    instrument_id = InstrumentId(f"crypto:binance:spot:{symbol}")
    effective_from = datetime.now(timezone.utc)
    return publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=ProductType.CRYPTO_SPOT,
        display_name=symbol,
        contract_spec=CryptoSpotSpec(AssetId(row["baseAsset"]), AssetId(row["quoteAsset"]), _decimal(notional)),
        trading_currency=AssetId(row["quoteAsset"]),
        listings=(ListingDefinition(
            ListingId(f"listing:binance:{instrument_id.value}:{symbol}"), instrument_id, VenueId("binance"), symbol,
            AssetId(row["quoteAsset"]), TradingRules(
                Decimal(price["tickSize"]), Decimal(lot["stepSize"]), Decimal(lot["minQty"]),
                minimum_notional=_decimal(notional),
            ), effective_from, venue_instrument_id=symbol,
        ),), effective_from=effective_from,
        **_binance_reference_facts(row["baseAsset"], row["quoteAsset"], at=effective_from),
    )


def _perpetual_definition(catalog: ReferenceCatalog, row, inverse=False):
    filters = {item["filterType"]: item for item in row["filters"]}
    lot, price = filters["LOT_SIZE"], filters["PRICE_FILTER"]
    symbol = row["symbol"]
    settlement = row.get("marginAsset") or row.get("quoteAsset")
    instrument_id = InstrumentId(f"crypto:binance:perpetual:{symbol}")
    effective_from = datetime.now(timezone.utc)
    return publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=ProductType.PERPETUAL, display_name=symbol,
        contract_spec=PerpetualSpec(AssetId(row["baseAsset"]), AssetId(settlement), row.get("pair", symbol), Decimal(row.get("contractSize", "1")), ContractType.INVERSE if inverse else ContractType.LINEAR, 28800),
        trading_currency=AssetId(row["quoteAsset"]),
        listings=(ListingDefinition(
            ListingId(f"listing:binance:{instrument_id.value}:{symbol}"), instrument_id, VenueId("binance"), symbol,
            AssetId(row["quoteAsset"]), TradingRules(Decimal(price["tickSize"]), Decimal(lot["stepSize"]), Decimal(lot["minQty"])),
            effective_from, venue_instrument_id=symbol,
        ),), effective_from=effective_from,
        **_binance_reference_facts(row["baseAsset"], settlement, row["quoteAsset"], at=effective_from),
    )


def _future_definition(catalog: ReferenceCatalog, row, inverse=False):
    filters = {item["filterType"]: item for item in row["filters"]}
    lot, price = filters["LOT_SIZE"], filters["PRICE_FILTER"]
    symbol = row["symbol"]
    settlement = row.get("marginAsset") or row.get("quoteAsset")
    expiry_ms = row.get("deliveryDate") or row.get("deliveryTime")
    if expiry_ms is None:
        raise ValueError(f"delivery future is missing expiry: {symbol}")
    expiry = datetime.fromtimestamp(int(expiry_ms) / 1000, timezone.utc)
    instrument_id = InstrumentId(f"crypto:binance:future:{symbol}")
    effective_from = datetime.now(timezone.utc)
    return publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=ProductType.FUTURE, display_name=symbol,
        contract_spec=FutureSpec(
            AssetId(row["baseAsset"]), AssetId(settlement), expiry,
            Decimal(row.get("contractSize", "1")), ContractType.INVERSE if inverse else ContractType.LINEAR,
            row.get("pair", symbol),
        ),
        trading_currency=AssetId(row["quoteAsset"]),
        listings=(ListingDefinition(
            ListingId(f"listing:binance:{instrument_id.value}:{symbol}"), instrument_id, VenueId("binance"), symbol,
            AssetId(row["quoteAsset"]), TradingRules(Decimal(price["tickSize"]), Decimal(lot["stepSize"]), Decimal(lot["minQty"])),
            effective_from, venue_instrument_id=symbol,
        ),), effective_from=effective_from,
        **_binance_reference_facts(row["baseAsset"], settlement, row["quoteAsset"], at=effective_from),
    )


def _option_definition(catalog: ReferenceCatalog, row):
    symbol = row["symbol"]
    expiry = datetime.fromtimestamp(int(row["expiryDate"]) / 1000, timezone.utc)
    instrument_id = InstrumentId(f"crypto:binance:option:{symbol}")
    effective_from = datetime.now(timezone.utc)
    return publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=ProductType.CRYPTO_OPTION, display_name=symbol,
        contract_spec=CryptoOptionSpec(AssetId(row["underlying"]), AssetId(row["quoteAsset"]), AssetId(row["settleAsset"]), AssetId(row["quoteAsset"]), expiry, Decimal(row["strikePrice"]), OptionRight.CALL if row["side"] == "CALL" else OptionRight.PUT, ExerciseStyle.EUROPEAN, Decimal(row.get("unit", "1")), row.get("underlying", "")),
        trading_currency=AssetId(row["quoteAsset"]),
        listings=(ListingDefinition(
            ListingId(f"listing:binance:{instrument_id.value}:{symbol}"), instrument_id, VenueId("binance"), symbol,
            AssetId(row["quoteAsset"]), TradingRules(
                Decimal(row.get("priceScale", "0.01")), Decimal(row.get("quantityScale", "0.01")), Decimal(row.get("minQty", "0.01")),
            ), effective_from, venue_instrument_id=symbol,
        ),), effective_from=effective_from,
        **_binance_reference_facts(row["underlying"], row["quoteAsset"], row["settleAsset"], at=effective_from),
    )


def _binance_reference_facts(*asset_codes: str, at: datetime) -> dict[str, tuple]:
    assets = tuple(
        AssetDefinition(AssetId(code), AssetType.CRYPTO, code, at, decimals=8)
        for code in sorted(set(asset_codes))
    )
    venue = VenueDefinition(VenueId("binance"), VenueType.CRYPTO_EXCHANGE, "Binance", "UTC", at)
    return {"asset_definitions": assets, "venue_definitions": (venue,)}


def _decimal(value):
    return Decimal(str(value)) if value not in (None, "") else None
