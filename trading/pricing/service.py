from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from trading.backtest.feed import MarketSlice
from trading.catalog.service import InstrumentCatalog
from trading.domain.identity import InstrumentId
from trading.domain.market_data import Greeks
from trading.domain.product import ListedOptionSpec, ProductType, SettlementType
from trading.market_data import OptionMarketObservation, blocking_issues, validate_option_observation
from trading.research.snapshot import InstrumentSnapshot
from trading.volatility import SurfaceSnapshot, VolObservation, build_surface, surface_implied_volatility

from .black import price_with_volatility
from .implied_vol import implied_volatility
from .models import ImpliedVolResult, PricingInput, PricingModel, PricingResult


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

    def get(self, instrument_id: InstrumentId) -> InstrumentValuation | None:
        return next((item for item in self.instruments if item.instrument_id == instrument_id), None)


class ValuationService:
    def __init__(
        self,
        catalog: InstrumentCatalog,
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
        references = dict(market.reference_prices)
        valuations: list[InstrumentValuation] = []
        observations: list[VolObservation] = []
        failures: list[str] = []
        enriched: list[InstrumentSnapshot] = []
        underlying_ids: set[InstrumentId] = set()
        for item in market.instruments:
            definition = self.catalog.get(item.instrument_id, market.timestamp)
            spec = definition.product_spec
            if not isinstance(spec, ListedOptionSpec):
                enriched.append(item)
                continue
            underlying_ids.add(spec.underlying)
            spot = references.get(spec.underlying)
            quote = item.quote
            if spot is None or quote is None:
                failures.append(f"{item.instrument_id.value}:missing_valid_quote_or_underlying")
                enriched.append(item)
                continue
            quality = validate_option_observation(OptionMarketObservation(
                item.instrument_id, quote.event_time, quote.bid, quote.ask,
                quote.bid_size, quote.ask_size, "market_slice",
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
            underlying_definition = self.catalog.get(spec.underlying, market.timestamp)
            model = PricingModel.BLACK_76 if underlying_definition.product_type is ProductType.INDEX and spec.settlement_type is SettlementType.CASH else PricingModel.BLACK_SCHOLES
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
                item.instrument_id, spec.underlying, market.timestamp, spec.expiry, spec.strike,
                spot, maturity, spec.right, market_price, solved.volatility, quote.bid, quote.ask,
            ))
        surface = None
        if len(underlying_ids) == 1 and observations:
            surface = build_surface(next(iter(underlying_ids)), market.timestamp, tuple(observations))
        if surface is not None:
            surface_valuations = []
            for valuation in valuations:
                definition = self.catalog.get(valuation.instrument_id, market.timestamp)
                spec = definition.product_spec
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
        return valued_market, ValuationSnapshot(market.timestamp, tuple(valuations), surface, tuple(failures))
