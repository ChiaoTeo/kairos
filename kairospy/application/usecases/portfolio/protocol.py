"""Consumer-owned ports for cross-account portfolio valuation."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol


class PortfolioRateProvider(Protocol):
    def rate(self, currency: str, valuation_currency: str) -> Decimal | None: ...


__all__ = ["PortfolioRateProvider"]
