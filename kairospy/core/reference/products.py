from __future__ import annotations

from dataclasses import dataclass

from .identity import AssetId, InstrumentId, ListingId, MarketId, reference_slug
from .model import AssetType, InstrumentType


@dataclass(frozen=True, slots=True)
class InstrumentProductSpec:
    market: str
    instrument_type: InstrumentType
    base_asset_type: AssetType
    quote_asset_type: AssetType
    identity_segment: str
    has_base_quote: bool = True


@dataclass(frozen=True, slots=True)
class EquityProductSpec:
    market: str = "equity"
    instrument_type: InstrumentType = InstrumentType.EQUITY
    asset_type: AssetType = AssetType.EQUITY
    currency_asset_type: AssetType = AssetType.FIAT
    entity_type: str = "company"


CRYPTO_SPOT = InstrumentProductSpec(
    market="spot",
    instrument_type=InstrumentType.SPOT,
    base_asset_type=AssetType.CRYPTO,
    quote_asset_type=AssetType.CRYPTO,
    identity_segment="spot",
)
CRYPTO_PERPETUAL = InstrumentProductSpec(
    market="perpetual",
    instrument_type=InstrumentType.PERPETUAL,
    base_asset_type=AssetType.CRYPTO,
    quote_asset_type=AssetType.CRYPTO,
    identity_segment="perpetual",
)
CRYPTO_FUTURE = InstrumentProductSpec(
    market="future",
    instrument_type=InstrumentType.FUTURE,
    base_asset_type=AssetType.CRYPTO,
    quote_asset_type=AssetType.CRYPTO,
    identity_segment="future",
)
OPTION = InstrumentProductSpec(
    market="option",
    instrument_type=InstrumentType.OPTION,
    base_asset_type=AssetType.OTHER,
    quote_asset_type=AssetType.FIAT,
    identity_segment="option",
)
EQUITY = EquityProductSpec()


def instrument_product_for_market(value: object) -> InstrumentProductSpec:
    text = _normalized_market(value)
    if text == "spot":
        return CRYPTO_SPOT
    if text in {"swap", "perp", "perpetual"}:
        return CRYPTO_PERPETUAL
    if text in {"future", "futures"}:
        return CRYPTO_FUTURE
    if text in {"option", "options"}:
        return OPTION
    if text == "derivative":
        return InstrumentProductSpec(
            market=text,
            instrument_type=InstrumentType.PERPETUAL,
            base_asset_type=AssetType.CRYPTO,
            quote_asset_type=AssetType.CRYPTO,
            identity_segment=text,
        )
    if text == "equity":
        return InstrumentProductSpec(
            market=text,
            instrument_type=InstrumentType.EQUITY,
            base_asset_type=AssetType.EQUITY,
            quote_asset_type=AssetType.FIAT,
            identity_segment=text,
        )
    return InstrumentProductSpec(
        market=text or "other",
        instrument_type=InstrumentType.OTHER,
        base_asset_type=AssetType.OTHER,
        quote_asset_type=AssetType.OTHER,
        identity_segment=reference_slug(text or "other"),
    )


def asset_id_for_product(spec: InstrumentProductSpec, symbol: str, *, quote: bool = False) -> AssetId:
    asset_type = spec.quote_asset_type if quote else spec.base_asset_type
    return AssetId(f"asset:{asset_type.value}:{reference_slug(symbol)}")


def equity_asset_id(stable_key: str) -> AssetId:
    return AssetId(f"asset:{EQUITY.asset_type.value}:{reference_slug(stable_key)}")


def currency_asset_id(currency: str) -> AssetId:
    return AssetId(f"asset:{EQUITY.currency_asset_type.value}:{reference_slug(currency)}")


def instrument_id_for_product(
    spec: InstrumentProductSpec,
    *,
    base: str | None,
    quote: str | None,
    venue: object,
    market: object,
    source_symbol: object,
) -> InstrumentId:
    if spec.has_base_quote and base and quote:
        return InstrumentId(f"instrument:{spec.identity_segment}:{reference_slug(base)}:{reference_slug(quote)}")
    return InstrumentId(
        "instrument:"
        f"{spec.identity_segment}:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}"
    )


def equity_instrument_id(stable_key: str) -> InstrumentId:
    return InstrumentId(f"instrument:{EQUITY.market}:{reference_slug(stable_key)}")


def listing_id_for_market(*, venue: object, market: object, source_symbol: object) -> ListingId:
    return ListingId(f"listing:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}")


def equity_listing_id(*, venue: object, stable_key: str) -> ListingId:
    return ListingId(f"listing:{reference_slug(venue)}:{reference_slug(stable_key)}")


def market_id_for_product(*, venue: object, market: object, source_symbol: object) -> MarketId:
    return MarketId(f"market:{reference_slug(venue)}:{reference_slug(market)}:{reference_slug(source_symbol)}")


def equity_market_id(*, venue: object, stable_key: str) -> MarketId:
    return MarketId(f"market:{reference_slug(venue)}:{EQUITY.market}:{reference_slug(stable_key)}")


def _normalized_market(value: object) -> str:
    return str(value or "").strip().lower()


__all__ = [
    "CRYPTO_FUTURE",
    "CRYPTO_PERPETUAL",
    "CRYPTO_SPOT",
    "EQUITY",
    "EquityProductSpec",
    "InstrumentProductSpec",
    "OPTION",
    "asset_id_for_product",
    "currency_asset_id",
    "equity_asset_id",
    "equity_instrument_id",
    "equity_listing_id",
    "equity_market_id",
    "instrument_id_for_product",
    "instrument_product_for_market",
    "listing_id_for_market",
    "market_id_for_product",
]
