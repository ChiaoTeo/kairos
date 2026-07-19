from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Callable
from uuid import UUID, uuid4

from trading.backtest.execution import combo_quote
from trading.backtest.feed import MarketSlice
from trading.backtest.portfolio import PortfolioSnapshot
from trading.reference import ReferenceCatalog
from trading.reference.access import contract_spec, definition_at
from trading.domain.execution import TradeSide
from trading.domain.intent import Intent, OpenStructureIntent
from trading.domain.product import is_option_spec, option_multiplier
from .option_structure import maximum_expiry_loss

from .limits import RiskLimits


class RiskDecisionType(StrEnum):
    APPROVED = "approved"
    RESIZED = "resized"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: UUID
    intent_id: UUID
    decision: RiskDecisionType
    rule: str
    reason: str
    requested_quantity: int
    approved_quantity: int
    actual_value: Decimal | None = None
    threshold: Decimal | None = None


class RiskEngine:
    def __init__(self, limits: RiskLimits, catalog: ReferenceCatalog, id_factory: Callable[[], UUID] = uuid4) -> None:
        self.limits = limits
        self.catalog = catalog
        self.id_factory = id_factory

    def evaluate(self, intent: Intent, portfolio: PortfolioSnapshot, market: MarketSlice, *, reduce_only: bool = False) -> tuple[Intent | None, RiskDecision]:
        original_quantity = intent.quantity
        resize_decision = None
        reject = lambda rule, reason, actual=None, threshold=None: (None, RiskDecision(self.id_factory(), intent.intent_id, RiskDecisionType.REJECTED, rule, reason, intent.quantity, 0, actual, threshold))
        if market.quality_issues:
            return reject("data_quality", "market slice contains quality issues")
        if reduce_only and isinstance(intent, OpenStructureIntent):
            return reject("reduce_only", "post-trade risk state permits closing orders only")
        quote = combo_quote(intent.legs, market, intent.quantity)
        if quote is None:
            return reject("quote", "missing or crossed quote")
        if quote.max_spread > self.limits.max_bid_ask_spread:
            return reject("spread", "bid-ask spread exceeds limit", quote.max_spread, self.limits.max_bid_ask_spread)
        contracts = sum(leg.ratio for leg in intent.legs) * intent.quantity
        if contracts > self.limits.max_contracts:
            return reject("contracts", "contract count exceeds limit", Decimal(contracts), Decimal(self.limits.max_contracts))
        if isinstance(intent, OpenStructureIntent):
            if len(portfolio.open_structures) >= self.limits.max_open_structures:
                return reject("open_structures", "maximum open structures reached")
            first = definition_at(self.catalog, intent.legs[0].instrument_id, market.timestamp)
            first_spec = contract_spec(first)
            expiry = first_spec.expiry if is_option_spec(first_spec) else None
            same_expiry = sum(any(
                is_option_spec(contract_spec(definition_at(self.catalog, instrument_id, market.timestamp)))
                and contract_spec(definition_at(self.catalog, instrument_id, market.timestamp)).expiry == expiry
                for instrument_id, _ in structure.legs
            ) for structure in portfolio.open_structures)
            if same_expiry >= self.limits.max_structures_per_expiry:
                return reject("expiry_concentration", "maximum structures for expiry reached")
            sells = [leg for leg in intent.legs if leg.side is TradeSide.SELL]
            buys = [leg for leg in intent.legs if leg.side is TradeSide.BUY]
            if sells and not buys:
                return reject("naked_option", "naked short options are prohibited")
            option_specs = [
                contract_spec(definition) for leg in intent.legs
                for definition in (definition_at(self.catalog, leg.instrument_id, market.timestamp),)
                if is_option_spec(contract_spec(definition))
            ]
            if len(option_specs) >= 2:
                credit = max(Decimal("0"), quote.natural)
                per_contract_risk = maximum_expiry_loss(tuple((contract_spec(definition_at(self.catalog, leg.instrument_id,market.timestamp)),leg.side.sign*leg.ratio) for leg in intent.legs),credit)
                allowed = min(self.limits.max_loss_per_trade, portfolio.equity_liquidation * self.limits.max_risk_fraction)
                max_quantity = int(allowed // per_contract_risk) if per_contract_risk else intent.quantity
                if max_quantity < 1:
                    return reject("max_loss", "trade risk exceeds limit", per_contract_risk, allowed)
                if max_quantity < intent.quantity:
                    resized = replace(intent, quantity=max_quantity)
                    resize_decision = RiskDecision(self.id_factory(), intent.intent_id, RiskDecisionType.RESIZED, "max_loss", "quantity reduced to risk budget", original_quantity, max_quantity, per_contract_risk * original_quantity, allowed)
                    intent = resized
                if portfolio.cash - per_contract_risk * intent.quantity < self.limits.min_remaining_cash:
                    return reject("remaining_cash", "risk reserve would breach minimum cash", portfolio.cash - per_contract_risk * intent.quantity, self.limits.min_remaining_cash)
            projected = {"delta": portfolio.delta or Decimal("0"), "gamma": portfolio.gamma or Decimal("0"), "vega": portfolio.vega or Decimal("0")}
            snapshots = {item.instrument_id: item for item in market.instruments}
            for leg in intent.legs:
                item = snapshots.get(leg.instrument_id)
                if item is None or item.greeks is None:
                    return reject("projected_greeks", "Greeks unavailable for projected risk")
                definition = definition_at(self.catalog, leg.instrument_id, market.timestamp)
                spec = contract_spec(definition)
                multiplier = option_multiplier(spec) if is_option_spec(spec) else Decimal("1")
                signed_quantity = Decimal(leg.side.sign * leg.ratio * intent.quantity)
                for name in projected:
                    value = getattr(item.greeks, name)
                    if value is None:
                        return reject("projected_greeks", f"{name} unavailable for projected risk")
                    projected[name] += signed_quantity * multiplier * value
            for name, limit in (("delta", self.limits.max_abs_delta), ("gamma", self.limits.max_abs_gamma), ("vega", self.limits.max_abs_vega)):
                if abs(projected[name]) > limit:
                    return reject(f"projected_{name}", f"projected {name} exceeds limit", abs(projected[name]), limit)
        if resize_decision is not None:
            return intent, resize_decision
        return intent, RiskDecision(self.id_factory(), intent.intent_id, RiskDecisionType.APPROVED, "all", "all pre-trade checks passed", intent.quantity, intent.quantity)

    def post_trade_violations(self, portfolio: PortfolioSnapshot) -> tuple[str, ...]:
        violations = []
        if portfolio.cash < self.limits.min_remaining_cash:
            violations.append("minimum_cash")
        for name, limit in (("delta", self.limits.max_abs_delta), ("gamma", self.limits.max_abs_gamma), ("vega", self.limits.max_abs_vega)):
            value = getattr(portfolio, name)
            if value is None or abs(value) > limit:
                violations.append(f"portfolio_{name}")
        if portfolio.greeks_coverage < self.limits.min_greeks_coverage:
            violations.append("greeks_coverage")
        return tuple(violations)
