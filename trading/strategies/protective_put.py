from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.domain.identity import InstrumentId
from trading.domain.intent import ProtectivePutIntent, TargetPositionIntent


@dataclass(frozen=True, slots=True)
class ProtectivePutConfig:
    equity_quantity: Decimal = Decimal("100")
    contracts: Decimal = Decimal("1")


class ProtectivePutStrategy:
    strategy_id = "protective-put-v1"

    def __init__(self, equity_id: InstrumentId, option_id: InstrumentId, config: ProtectivePutConfig = ProtectivePutConfig()) -> None:
        self.equity_id, self.option_id, self.config = equity_id, option_id, config

    def intents(self, current_equity_quantity: Decimal, current_option_quantity: Decimal):
        if current_equity_quantity < self.config.equity_quantity:
            return (TargetPositionIntent(
                uuid5(NAMESPACE_URL, f"{self.strategy_id}:equity"), self.strategy_id,
                self.equity_id, self.config.equity_quantity, "acquire protected shares",
            ),)
        if current_option_quantity < self.config.contracts:
            return (ProtectivePutIntent(
                uuid5(NAMESPACE_URL, f"{self.strategy_id}:put"), self.strategy_id,
                self.equity_id, self.option_id, self.config.contracts, "buy protective put",
            ),)
        return ()
