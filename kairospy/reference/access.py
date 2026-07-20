from __future__ import annotations

from datetime import datetime

from kairospy.domain.identity import AssetId, InstrumentId

from .catalog import ReferenceCatalog


def definition_at(catalog: ReferenceCatalog, instrument_id: InstrumentId, at: datetime):
    return catalog.instruments.get(instrument_id, at)


def contract_spec(definition):
    return definition.contract_spec


def product_type(definition):
    return definition.instrument_type


def trade_cash_asset(catalog: ReferenceCatalog, definition, at: datetime) -> AssetId:
    spec = contract_spec(definition)
    for name in ("premium_asset", "quote_asset", "trading_currency"):
        value = getattr(spec, name, None)
        if isinstance(value, AssetId):
            return value
    product = catalog.products.get(definition.product_id, at)
    if product.currency is None:
        raise ValueError(f"product has no trade cash currency: {product.product_id}")
    return product.currency


def settlement_asset(catalog: ReferenceCatalog, definition, at: datetime) -> AssetId:
    spec = contract_spec(definition)
    value = getattr(spec, "settlement_asset", None)
    if isinstance(value, AssetId):
        return value
    if definition.settlement_terms_id is not None:
        terms = catalog.settlements.get(definition.settlement_terms_id, at).terms
        if terms.settlement_asset is not None:
            return terms.settlement_asset
    return trade_cash_asset(catalog, definition, at)
