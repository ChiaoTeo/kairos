from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from trading.accounting.portfolio import PortfolioSnapshot
from trading.domain.identity import AccountKey, InstrumentId
from trading.domain.product import is_option_spec, option_multiplier
from trading.risk.margin import MarginResult
from trading.reference import ReferenceCatalog, ReferenceRole


@dataclass(frozen=True, slots=True)
class RiskExposure:
    dimension: str
    key: str
    gross: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class UnifiedRiskView:
    gross_exposure: Decimal
    net_exposure: Decimal
    exposures: tuple[RiskExposure, ...]
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    leverage: Decimal | None
    margin_usage: Decimal
    available_collateral: Decimal
    maximum_concentration: Decimal
    minimum_liquidation_distance: Decimal | None
    conversion_risk: tuple[str, ...]
    liquidity_risk: tuple[str, ...]


def build_risk_view(
    snapshot: PortfolioSnapshot,
    catalog: ReferenceCatalog,
    *,
    unit_greeks: dict[InstrumentId, tuple[Decimal, Decimal, Decimal, Decimal]] | None = None,
    margins: dict[AccountKey, MarginResult] | None = None,
    liquidation_prices: dict[InstrumentId, Decimal] | None = None,
    liquidity_flags: tuple[str, ...] = (),
) -> UnifiedRiskView:
    unit_greeks, margins, liquidation_prices = unit_greeks or {}, margins or {}, liquidation_prices or {}
    grouped = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    gross = net = Decimal("0")
    greek_totals = [Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")]
    liquidation_distances = []
    for position in snapshot.positions:
        value = position.market_value_reporting or Decimal("0")
        gross += abs(value)
        net += value
        definition = _definition(catalog, position.instrument_id, snapshot.timestamp)
        for dimension, key in _dimensions(catalog, definition, position.account, snapshot.timestamp):
            grouped[(dimension, key)][0] += abs(value)
            grouped[(dimension, key)][1] += value
        values = unit_greeks.get(position.instrument_id)
        if values:
            spec = definition.contract_spec
            multiplier = option_multiplier(spec) if is_option_spec(spec) else getattr(spec, "contract_size", Decimal("1"))
            for index, amount in enumerate(values):
                greek_totals[index] += position.quantity * multiplier * amount
        liquidation = liquidation_prices.get(position.instrument_id)
        if liquidation is not None and position.mark_price and position.mark_price > 0:
            liquidation_distances.append(abs(position.mark_price - liquidation) / position.mark_price)
    exposures = tuple(
        RiskExposure(dimension, key, values[0], values[1])
        for (dimension, key), values in sorted(grouped.items())
    )
    margin_usage = sum((item.initial_margin for item in margins.values()), Decimal("0"))
    available_collateral = sum((item.available_after for item in margins.values()), Decimal("0"))
    leverage = gross / snapshot.net_asset_value if snapshot.net_asset_value > 0 else None
    concentration = max((item.gross for item in exposures if item.dimension == "asset"), default=Decimal("0"))
    concentration = concentration / gross if gross else Decimal("0")
    return UnifiedRiskView(
        gross, net, exposures, *greek_totals, leverage, margin_usage, available_collateral,
        concentration, min(liquidation_distances) if liquidation_distances else None,
        tuple((*snapshot.unpriced_assets, *snapshot.unpriced_positions)), liquidity_flags,
    )


def _definition(catalog, instrument_id, at):
    return catalog.instruments.get(instrument_id, at)


def _dimensions(catalog, definition, account, at):
    product = catalog.products.get(definition.product_id, at)
    values = [("account", account.value), ("product", product.product_type.value), ("product_family", product.product_id.value)]
    try:
        route = catalog.resolve_execution_route(account, definition.instrument_id, at)
        listing = catalog.listings.get(route.listing_id, at)
        values.extend((("venue", listing.venue_id.value), ("broker", route.broker_id.value)))
    except LookupError:
        values.append(("venue", "unlisted"))
    references = catalog.references(definition.instrument_id, ReferenceRole.ECONOMIC_UNDERLYING, at)
    for reference in references:
        target = reference.target
        if target.asset_id is not None:
            values.append(("asset", target.asset_id.value))
        elif target.instrument_id is not None:
            values.append(("underlying", target.instrument_id.value))
        elif target.product_id is not None:
            values.append(("underlying_product", target.product_id.value))
        elif target.benchmark_id is not None:
            values.append(("benchmark", target.benchmark_id.value))
    if not references:
        spec = definition.contract_spec
        asset = getattr(spec, "base_asset", None) or getattr(spec, "underlying_asset", None) or getattr(spec, "settlement_asset", None) or getattr(spec, "quote_asset", None)
        if asset is not None:
            values.append(("asset", asset.value))
    return tuple(values)
