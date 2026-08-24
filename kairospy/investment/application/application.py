"""Aggregate access to the Investment subsystem's public use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InvestmentApplication:
    reference: Any
    market: Any
    account: Any
    portfolio: Any
    risk: Any
    capital: Any
    execution: Any


__all__ = ["InvestmentApplication"]
