from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from math import log

from kairos.domain.identity import InstrumentId
from kairos.pricing import PricingInput, PricingModel, PricingResult, price_with_volatility


@dataclass(frozen=True, slots=True)
class RevaluationPosition:
    instrument_id: InstrumentId
    quantity: Decimal
    multiplier: Decimal
    inputs: PricingInput
    model: PricingModel
    structure_id: str | None = None
    account_id: str | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    spot_shock: Decimal = Decimal("0")
    volatility_shock: Decimal = Decimal("0")
    skew_twist: Decimal = Decimal("0")
    term_twist: Decimal = Decimal("0")
    rate_shock: Decimal = Decimal("0")
    time_advance_days: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class InstrumentScenarioResult:
    instrument_id: InstrumentId
    base_value: Decimal
    scenario_value: Decimal
    pnl: Decimal
    base_pricing: PricingResult
    scenario_pricing: PricingResult
    structure_id: str | None
    account_id: str | None


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: Scenario
    base_value: Decimal
    scenario_value: Decimal
    pnl: Decimal
    instruments: tuple[InstrumentScenarioResult, ...]
    pnl_by_structure: tuple[tuple[str, Decimal], ...]
    pnl_by_account: tuple[tuple[str, Decimal], ...]


class ScenarioEngine:
    def evaluate(self, positions: tuple[RevaluationPosition, ...], scenario: Scenario) -> ScenarioResult:
        results = []
        structures: dict[str, Decimal] = {}
        accounts: dict[str, Decimal] = {}
        for position in positions:
            base = price_with_volatility(position.inputs, position.inputs.volatility, position.model)
            stressed_inputs = self._shock(position.inputs, scenario)
            stressed = price_with_volatility(stressed_inputs, stressed_inputs.volatility, position.model)
            scale = position.quantity * position.multiplier
            base_value, scenario_value = base.price * scale, stressed.price * scale
            pnl = scenario_value - base_value
            results.append(InstrumentScenarioResult(
                position.instrument_id, base_value, scenario_value, pnl, base, stressed,
                position.structure_id, position.account_id,
            ))
            if position.structure_id is not None:
                structures[position.structure_id] = structures.get(position.structure_id, Decimal("0")) + pnl
            if position.account_id is not None:
                accounts[position.account_id] = accounts.get(position.account_id, Decimal("0")) + pnl
        base_total = sum((item.base_value for item in results), Decimal("0"))
        scenario_total = sum((item.scenario_value for item in results), Decimal("0"))
        return ScenarioResult(
            scenario, base_total, scenario_total, scenario_total - base_total, tuple(results),
            tuple(sorted(structures.items())), tuple(sorted(accounts.items())),
        )

    @staticmethod
    def _shock(inputs: PricingInput, scenario: Scenario) -> PricingInput:
        underlying = inputs.underlying * (Decimal("1") + scenario.spot_shock)
        if underlying <= 0:
            raise ValueError("scenario spot shock produces non-positive underlying")
        maturity = max(Decimal("0"), inputs.time_to_expiry - scenario.time_advance_days / Decimal("365.25"))
        log_moneyness = Decimal(str(log(float(inputs.strike / inputs.underlying))))
        volatility = (
            inputs.volatility + scenario.volatility_shock
            + scenario.skew_twist * log_moneyness
            + scenario.term_twist * inputs.time_to_expiry
        )
        volatility = max(Decimal("0.000001"), volatility)
        return replace(
            inputs, underlying=underlying, time_to_expiry=maturity,
            risk_free_rate=inputs.risk_free_rate + scenario.rate_shock,
            volatility=volatility,
        )


def standard_scenario_grid(
    spot_shocks: tuple[Decimal, ...] = (Decimal("-0.20"), Decimal("-0.10"), Decimal("0"), Decimal("0.10"), Decimal("0.20")),
    volatility_shocks: tuple[Decimal, ...] = (Decimal("-0.10"), Decimal("0"), Decimal("0.10"), Decimal("0.20")),
) -> tuple[Scenario, ...]:
    return tuple(
        Scenario(f"spot={spot:+};vol={vol:+}", spot_shock=spot, volatility_shock=vol)
        for spot in spot_shocks for vol in volatility_shocks
    )

