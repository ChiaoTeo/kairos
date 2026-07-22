from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from kairospy.identity import InstrumentId
from kairospy.market import (
    MarketInstrumentSlice,
    MarketSlice,
    OptionMarketObservation,
    blocking_issues,
    validate_option_observation,
)
from kairospy.market.types import Greeks
from kairospy.analytics.volatility import SurfaceSnapshot, VolObservation, build_surface, surface_implied_volatility
from kairospy.reference.catalog import ReferenceCatalog
from kairospy.reference.contracts import ListedOptionSpec, ProductType, ReferenceRole, SettlementType

from .black import price_with_volatility
from .implied_vol import implied_volatility
from .option_pricing_contracts import ImpliedVolResult, PricingInput, PricingModel, PricingResult


SECONDS_PER_YEAR = Decimal("31557600")


@dataclass(frozen=True, slots=True)
class InstrumentValuation:
    instrument_id: InstrumentId
    model: PricingModel
    inputs: PricingInput
    market_price: Decimal
    implied_vol: ImpliedVolResult
    pricing: PricingResult | None
    source: str = "internal_quote_iv"
    vendor_delta_error: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ValuationSnapshot:
    as_of: datetime
    instruments: tuple[InstrumentValuation, ...]
    surface: SurfaceSnapshot | None
    failures: tuple[str, ...]
    available_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("valuation snapshot as_of must be timezone-aware")
        if self.available_time is None:
            object.__setattr__(self, "available_time", self.as_of)
        if self.available_time is not None and self.available_time.tzinfo is None:
            raise ValueError("valuation snapshot available_time must be timezone-aware")
        if self.available_time is not None and self.available_time < self.as_of:
            raise ValueError("valuation snapshot available_time cannot precede as_of")

    def get(self, instrument_id: InstrumentId) -> InstrumentValuation | None:
        return next((item for item in self.instruments if item.instrument_id == instrument_id), None)


class OptionValuationService:
    def __init__(
        self,
        catalog: ReferenceCatalog,
        *,
        risk_free_rate: Decimal = Decimal("0"),
        dividend_yield: Decimal = Decimal("0"),
        max_quote_age_seconds: Decimal = Decimal("5"),
    ) -> None:
        self.catalog = catalog
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        if max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be positive")
        self.max_quote_age_seconds = max_quote_age_seconds

    def value(self, market: MarketSlice) -> tuple[MarketSlice, ValuationSnapshot]:
        available_time = getattr(market, "available_time", market.timestamp)
        references = dict(market.reference_prices)
        valuations: list[InstrumentValuation] = []
        observations: list[VolObservation] = []
        failures: list[str] = []
        enriched: list[MarketInstrumentSlice] = []
        underlying_ids: set[InstrumentId] = set()
        for item in market.instruments:
            definition = self._definition(item.instrument_id, market.timestamp)
            spec = _contract_spec(definition)
            if not isinstance(spec, ListedOptionSpec):
                enriched.append(item)
                continue
            underlying_id = self._pricing_underlying(item.instrument_id, spec, market.timestamp)
            underlying_ids.add(underlying_id)
            spot = references.get(underlying_id)
            quote = item.quote
            if spot is None or quote is None:
                failures.append(f"{item.instrument_id.value}:missing_valid_quote_or_underlying")
                enriched.append(item)
                continue
            quality = validate_option_observation(OptionMarketObservation(
                item.instrument_id, quote.event_time, quote.bid, quote.ask,
                quote.bid_size, quote.ask_size, "market_snapshot",
            ), market.timestamp, max_age_seconds=self.max_quote_age_seconds)
            errors = blocking_issues(quality)
            if errors:
                failures.extend(f"{item.instrument_id.value}:quality_{issue.code}" for issue in errors)
                enriched.append(item)
                continue
            market_price = (quote.bid + quote.ask) / 2
            maturity = Decimal(str((spec.expiry - market.timestamp).total_seconds())) / SECONDS_PER_YEAR
            if maturity <= 0:
                failures.append(f"{item.instrument_id.value}:expired")
                enriched.append(item)
                continue
            underlying_definition = self._definition(underlying_id, market.timestamp)
            model = PricingModel.BLACK_76 if _product_type(underlying_definition) is ProductType.INDEX and spec.settlement_type is SettlementType.CASH else PricingModel.BLACK_SCHOLES
            inputs = PricingInput(
                spot, spec.strike, maturity, self.risk_free_rate, Decimal("0.2"), spec.right,
                Decimal("0") if model is PricingModel.BLACK_76 else self.dividend_yield,
            )
            solved = implied_volatility(market_price, inputs, model)
            pricing = price_with_volatility(inputs, solved.volatility, model) if solved.volatility is not None else None
            vendor_error = (
                abs(item.greeks.delta - pricing.delta)
                if item.greeks is not None and item.greeks.delta is not None and pricing is not None else None
            )
            valuations.append(InstrumentValuation(item.instrument_id, model, inputs, market_price, solved, pricing, "internal_quote_iv", vendor_error))
            if pricing is None or solved.volatility is None:
                failures.append(f"{item.instrument_id.value}:iv_{solved.status.value}")
                enriched.append(item)
                continue
            internal_greeks = Greeks(
                item.instrument_id, solved.volatility, pricing.delta, pricing.gamma,
                pricing.theta, pricing.vega, market.timestamp,
            )
            enriched.append(item if item.greeks is not None else replace(item, greeks=internal_greeks, greeks_time=market.timestamp))
            observations.append(VolObservation(
                item.instrument_id, underlying_id, market.timestamp, spec.expiry, spec.strike,
                spot, maturity, spec.right, market_price, solved.volatility, quote.bid, quote.ask,
            ))
        surface = None
        if len(underlying_ids) == 1 and observations:
            surface = build_surface(next(iter(underlying_ids)), market.timestamp, tuple(observations), available_time=available_time)
        if surface is not None:
            surface_valuations = []
            for valuation in valuations:
                definition = self._definition(valuation.instrument_id, market.timestamp)
                spec = _contract_spec(definition)
                try:
                    from math import log
                    log_moneyness = Decimal(str(log(float(valuation.inputs.strike / valuation.inputs.underlying))))
                    surface_vol = surface_implied_volatility(surface, spec.expiry, log_moneyness)
                except (LookupError, ValueError):
                    surface_valuations.append(valuation)
                    continue
                surface_inputs = replace(valuation.inputs, volatility=surface_vol)
                surface_pricing = price_with_volatility(surface_inputs, surface_vol, valuation.model)
                surface_valuations.append(replace(
                    valuation, inputs=surface_inputs, pricing=surface_pricing, source="internal_surface",
                ))
            valuations = surface_valuations
        valued_market = replace(market, instruments=tuple(enriched))
        return valued_market, ValuationSnapshot(market.timestamp, tuple(valuations), surface, tuple(failures), available_time)

    def _definition(self, instrument_id: InstrumentId, at: datetime):
        return self.catalog.instruments.get(instrument_id, at)

    def _pricing_underlying(self, instrument_id: InstrumentId, spec: ListedOptionSpec, at: datetime) -> InstrumentId:
        references = self.catalog.references(instrument_id, ReferenceRole.PRICING_UNDERLYING, at)
        matches = [item.target.instrument_id for item in references if item.target.instrument_id is not None]
        if len(matches) != 1:
            raise LookupError(f"expected one pricing underlying: {instrument_id} at {at}")
        return matches[0]


def _contract_spec(definition):
    return definition.contract_spec


def _product_type(definition):
    return definition.instrument_type
