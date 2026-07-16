from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from trading.accounting.portfolio import PortfolioSnapshotV2
from trading.catalog.service import InstrumentCatalog
from trading.domain.identity import AccountKey, InstrumentId
from trading.risk.margin import MarginResult


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
    snapshot: PortfolioSnapshotV2,
    catalog: InstrumentCatalog,
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
        definition = catalog.get(position.instrument_id, snapshot.timestamp)
        for dimension, key in (
            ("venue", definition.listings[0].venue_id.value),
            ("account", position.account.value),
            ("product", definition.product_type.value),
            ("asset", (definition.base_asset or definition.quote_asset).value),
        ):
            grouped[(dimension, key)][0] += abs(value)
            grouped[(dimension, key)][1] += value
        values = unit_greeks.get(position.instrument_id)
        if values:
            multiplier = getattr(definition.product_spec, "multiplier", getattr(definition.product_spec, "contract_size", Decimal("1")))
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
