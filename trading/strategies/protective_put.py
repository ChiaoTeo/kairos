from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from trading.domain.identity import InstrumentId
from trading.domain.intent import ProtectivePutIntent, TargetPositionIntent
from trading.strategies.base import StrategyContext,StrategyDecision


@dataclass(frozen=True, slots=True)
class ProtectivePutConfig:
    equity_quantity: Decimal = Decimal("100")
    contracts: Decimal = Decimal("1")


class ProtectivePutStrategy:
    strategy_id = "protective-put-v1"

    def __init__(self, equity_id: InstrumentId, option_id: InstrumentId, config: ProtectivePutConfig = ProtectivePutConfig()) -> None:
        self.equity_id, self.option_id, self.config = equity_id, option_id, config
        self._decisions=[]

    @property
    def decisions(self):return tuple(self._decisions)
    def on_start(self,context):return ()
    def on_market(self,context:StrategyContext):
        equity=_position(context,self.equity_id);option=_position(context,self.option_id);intents=self.intents(equity,option)
        self._decisions.append(StrategyDecision(context.now.isoformat(),"intent" if intents else "hold",
            f"equity={equity},option={option}",(self.equity_id.value,self.option_id.value)))
        return intents
    def on_fill(self,fill,context):return ()
    def on_end(self,context):return ()

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


def _position(context,instrument_id):
    explicit=dict(context.strategy_positions).get(instrument_id)
    if explicit is not None:return explicit
    return next((item.quantity for item in getattr(context.portfolio,"positions",()) if item.instrument_id==instrument_id),Decimal("0"))
