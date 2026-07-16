from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from .scenarios import RevaluationPosition, Scenario, ScenarioEngine, ScenarioResult


@dataclass(frozen=True, slots=True)
class PnLExplain:
    total_pnl: Decimal
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    rho: Decimal
    residual: Decimal


@dataclass(frozen=True, slots=True)
class TailRiskResult:
    confidence: Decimal
    observation_count: int
    value_at_risk: Decimal
    expected_shortfall: Decimal
    worst_pnl: Decimal


def explain_scenario(position: RevaluationPosition, scenario: Scenario, result: ScenarioResult | None = None) -> PnLExplain:
    evaluated = result or ScenarioEngine().evaluate((position,), scenario)
    if len(evaluated.instruments) != 1:
        raise ValueError("PnL explain requires a single-position scenario result")
    item = evaluated.instruments[0]
    scale = position.quantity * position.multiplier
    spot_change = position.inputs.underlying * scenario.spot_shock
    time_change = -scenario.time_advance_days / Decimal("365.25")
    from math import log
    log_moneyness = Decimal(str(log(float(position.inputs.strike / position.inputs.underlying))))
    vol_change = scenario.volatility_shock + scenario.skew_twist * log_moneyness + scenario.term_twist * position.inputs.time_to_expiry
    delta = item.base_pricing.delta * spot_change * scale
    gamma = Decimal("0.5") * item.base_pricing.gamma * spot_change * spot_change * scale
    theta = item.base_pricing.theta * time_change * scale
    vega = item.base_pricing.vega * vol_change * scale
    rho = item.base_pricing.rho * scenario.rate_shock * scale
    explained = delta + gamma + theta + vega + rho
    return PnLExplain(item.pnl, delta, gamma, theta, vega, rho, item.pnl - explained)


def historical_var_es(pnls: tuple[Decimal, ...], confidence: Decimal = Decimal("0.95")) -> TailRiskResult:
    if not pnls:
        raise ValueError("historical VaR requires observations")
    if not Decimal("0") < confidence < Decimal("1"):
        raise ValueError("confidence must be between zero and one")
    losses = sorted((-value for value in pnls), reverse=True)
    tail_count = max(1, int(((Decimal("1") - confidence) * Decimal(len(losses))).to_integral_value(rounding=ROUND_CEILING)))
    tail = losses[:tail_count]
    value_at_risk = tail[-1]
    expected_shortfall = sum(tail, Decimal("0")) / Decimal(len(tail))
    return TailRiskResult(confidence, len(pnls), value_at_risk, expected_shortfall, min(pnls))
