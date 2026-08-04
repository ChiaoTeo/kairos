"""Composition entry points for risk budget runtimes."""

from __future__ import annotations

from ..domain import RiskBudget
from ..services.ledger import RiskBudgetLedger
from .component import RiskApplication


def build_risk_application(
    budgets: tuple[RiskBudget, ...] = (),
    *,
    allow_unbudgeted: bool = True,
) -> RiskApplication:
    return RiskApplication(RiskBudgetLedger(budgets, allow_unbudgeted=allow_unbudgeted))


__all__ = ["build_risk_application"]
