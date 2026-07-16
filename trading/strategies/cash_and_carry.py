from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.domain.identity import InstrumentId
from trading.domain.intent import CashAndCarryIntent


@dataclass(frozen=True, slots=True)
class CashAndCarryConfig:
    spot_quantity: Decimal = Decimal("0.1")
    minimum_annualized_basis: Decimal = Decimal("0.05")
    maximum_leverage: Decimal = Decimal("2")


class CashAndCarryStrategy:
    strategy_id = "spot-perpetual-carry-v1"

    def __init__(self, spot_id: InstrumentId, perpetual_id: InstrumentId, config: CashAndCarryConfig = CashAndCarryConfig()) -> None:
        self.spot_id, self.perpetual_id, self.config = spot_id, perpetual_id, config

    def intent(self, spot_price: Decimal, perpetual_price: Decimal, current_spot: Decimal, current_perpetual: Decimal):
        basis = perpetual_price / spot_price - 1
        if basis < self.config.minimum_annualized_basis:
            return None
        spot_delta = self.config.spot_quantity - current_spot
        perp_target = -self.config.spot_quantity
        perp_delta = perp_target - current_perpetual
        if spot_delta == 0 and perp_delta == 0:
            return None
        return CashAndCarryIntent(
            uuid5(NAMESPACE_URL, f"{self.strategy_id}:{spot_price}:{perpetual_price}"), self.strategy_id,
            self.spot_id, self.perpetual_id, spot_delta, perp_delta,
            f"basis={basis}",
        )
