from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.domain.identity import InstrumentId
from trading.domain.intent import CoveredCallIntent, TargetPositionIntent


@dataclass(frozen=True, slots=True)
class CoveredCallConfig:
    equity_quantity: Decimal = Decimal("100")
    contracts: Decimal = Decimal("1")


class CoveredCallStrategy:
    strategy_id = "covered-call-v1"

    def __init__(self, equity_id: InstrumentId, option_id: InstrumentId, config: CoveredCallConfig = CoveredCallConfig()) -> None:
        self.equity_id, self.option_id, self.config = equity_id, option_id, config

    def intents(self, current_equity_quantity: Decimal, current_option_quantity: Decimal):
        results = []
        if current_equity_quantity < self.config.equity_quantity:
            results.append(TargetPositionIntent(
                uuid5(NAMESPACE_URL, f"{self.strategy_id}:equity"), self.strategy_id,
                self.equity_id, self.config.equity_quantity, "acquire covered shares",
            ))
        if current_equity_quantity >= self.config.equity_quantity and current_option_quantity == 0:
            results.append(CoveredCallIntent(
                uuid5(NAMESPACE_URL, f"{self.strategy_id}:option"), self.strategy_id,
                self.equity_id, self.option_id, self.config.contracts, "sell covered call",
            ))
        return tuple(results)
