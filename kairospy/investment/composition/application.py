"""Construct the aggregate Investment application from public sub-apps."""

from __future__ import annotations

from typing import Any

from kairospy.investment.application import InvestmentApplication


def compose_investment_application(
    *,
    reference: Any,
    market: Any,
    account: Any,
    portfolio: Any,
    risk: Any,
    capital: Any,
    execution: Any,
) -> InvestmentApplication:
    return InvestmentApplication(
        reference=reference,
        market=market,
        account=account,
        portfolio=portfolio,
        risk=risk,
        capital=capital,
        execution=execution,
    )


__all__ = ["compose_investment_application"]
