from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from trading.domain.strategy_contract import EconomicIntent
from trading.domain.intent import TargetExposureIntent, TargetPositionIntent


class AllocationDecisionType(StrEnum):
    APPROVED = "approved"
    RESIZED = "resized"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    strategy_id: str
    maximum_risk_budget: Decimal
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.maximum_risk_budget <= 0:
            raise ValueError("strategy allocation must be positive")


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    decision: AllocationDecisionType
    strategy_id: str
    requested_risk_budget: Decimal
    approved_risk_budget: Decimal
    reason: str


class PortfolioAllocator:
    """Account-level capital gate; it never mutates strategy structure semantics."""

    def __init__(self, allocations: tuple[StrategyAllocation, ...], portfolio_risk_limit: Decimal) -> None:
        self.allocations = {item.strategy_id: item for item in allocations}
        self.portfolio_risk_limit = portfolio_risk_limit
        if portfolio_risk_limit <= 0:
            raise ValueError("portfolio risk limit must be positive")

    def approve(self, intent: EconomicIntent, *, committed_risk: Decimal = Decimal("0"),
                health_multiplier: Decimal = Decimal("1")) -> AllocationDecision:
        if not Decimal("0")<=health_multiplier<=Decimal("1"):raise ValueError("health multiplier must be in [0, 1]")
        allocation = self.allocations.get(intent.strategy_id)
        if allocation is None:
            return AllocationDecision(AllocationDecisionType.REJECTED, intent.strategy_id,
                                      intent.risk_budget, Decimal("0"), "strategy has no allocation")
        if not allocation.enabled:
            return AllocationDecision(AllocationDecisionType.REJECTED, intent.strategy_id,
                                      intent.risk_budget, Decimal("0"), "strategy allocation is disabled")
        available = max(Decimal("0"), self.portfolio_risk_limit - committed_risk)
        approved = min(intent.risk_budget, allocation.maximum_risk_budget*health_multiplier, available)
        if approved <= 0:
            return AllocationDecision(AllocationDecisionType.REJECTED, intent.strategy_id,
                                      intent.risk_budget, Decimal("0"), "portfolio risk budget exhausted")
        if approved < intent.risk_budget:
            return AllocationDecision(AllocationDecisionType.RESIZED, intent.strategy_id,
                                      intent.risk_budget, approved, "risk budget reduced by allocation gate")
        return AllocationDecision(AllocationDecisionType.APPROVED, intent.strategy_id,
                                  intent.risk_budget, approved, "risk budget approved")


@dataclass(frozen=True, slots=True)
class PositionSizingDecision:
    approved: bool
    intent: TargetPositionIntent | None
    approved_capital: Decimal
    reason: str


class PositionSizer:
    """Convert strategy exposure semantics into an account quantity after allocation."""

    def size(self, intent: TargetExposureIntent, *, approved_capital: Decimal,
             reference_price: Decimal, lot_size: Decimal = Decimal("0.00000001")) -> PositionSizingDecision:
        if approved_capital <= 0:
            return PositionSizingDecision(False, None, Decimal("0"), "approved capital is not positive")
        if reference_price <= 0 or lot_size <= 0:
            raise ValueError("position sizing requires positive price and lot size")
        raw = approved_capital * intent.target_fraction / reference_price
        quantity = (raw // lot_size) * lot_size
        target = TargetPositionIntent(
            intent.intent_id, intent.strategy_id, intent.instrument_id, quantity,
            f"{intent.reason}; sized with approved_capital={approved_capital}",
        )
        return PositionSizingDecision(True, target, approved_capital, "target exposure sized")
