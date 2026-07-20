from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairos.domain.identity import InstrumentId
from kairos.domain.intent import CashAndCarryIntent
from kairos.strategies.strategy_protocols import StrategyContext,StrategyDecision


@dataclass(frozen=True, slots=True)
class CashAndCarryConfig:
    spot_quantity: Decimal = Decimal("0.1")
    minimum_annualized_basis: Decimal = Decimal("0.05")
    maximum_leverage: Decimal = Decimal("2")


class CashAndCarryStrategy:
    strategy_id = "spot-perpetual-carry-v1"

    def __init__(self, spot_id: InstrumentId, perpetual_id: InstrumentId, config: CashAndCarryConfig = CashAndCarryConfig()) -> None:
        self.spot_id, self.perpetual_id, self.config = spot_id, perpetual_id, config
        self._decisions=[]

    @property
    def decisions(self):return tuple(self._decisions)
    def on_start(self,context):return ()
    def on_market(self,context:StrategyContext):
        spot=_price(context,self.spot_id);perpetual=_price(context,self.perpetual_id)
        if spot is None or perpetual is None:
            self._decisions.append(StrategyDecision(context.now.isoformat(),"skip","missing two-sided prices",(self.spot_id.value,self.perpetual_id.value)))
            return ()
        intent=self.intent(spot,perpetual,_position(context,self.spot_id),_position(context,self.perpetual_id))
        self._decisions.append(StrategyDecision(context.now.isoformat(),"intent" if intent else "hold",
            f"spot={spot},perpetual={perpetual}",(self.spot_id.value,self.perpetual_id.value)))
        return (intent,) if intent else ()
    def on_fill(self,fill,context):return ()
    def on_end(self,context):return ()

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


def _position(context,instrument_id):
    explicit=dict(context.strategy_positions).get(instrument_id)
    if explicit is not None:return explicit
    return next((item.quantity for item in getattr(context.portfolio,"positions",()) if item.instrument_id==instrument_id),Decimal("0"))
def _price(context,instrument_id):
    item=next((value for value in context.market.instruments if value.instrument_id==instrument_id),None)
    quote=getattr(item,"quote",None)
    if quote and quote.bid is not None and quote.ask is not None:return (quote.bid+quote.ask)/Decimal("2")
    return dict(context.market.reference_prices).get(instrument_id)
