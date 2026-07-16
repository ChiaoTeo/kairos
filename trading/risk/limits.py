from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_loss_per_trade: Decimal = Decimal("5000")
    max_risk_fraction: Decimal = Decimal("0.05")
    max_open_structures: int = 1
    max_structures_per_expiry: int = 1
    max_contracts: int = 10
    max_abs_delta: Decimal = Decimal("10000")
    max_abs_gamma: Decimal = Decimal("10000")
    max_abs_vega: Decimal = Decimal("100000")
    min_remaining_cash: Decimal = Decimal("0")
    max_bid_ask_spread: Decimal = Decimal("2")
    min_greeks_coverage: Decimal = Decimal("1")
