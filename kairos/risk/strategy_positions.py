from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairos.domain.identity import InstrumentId


@dataclass(frozen=True, slots=True)
class StrategyPosition:
    strategy_id: str
    instrument_id: InstrumentId
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class NettedPosition:
    instrument_id: InstrumentId
    account_quantity: Decimal
    allocations: tuple[StrategyPosition, ...]


class StrategyPositionBook:
    """Preserves virtual strategy ownership while exposing account net positions."""
    def __init__(self) -> None:self._quantities={}
    def apply(self,strategy_id: str,instrument_id: InstrumentId,quantity_delta: Decimal) -> None:
        if not strategy_id:raise ValueError("strategy id is required")
        key=(strategy_id,instrument_id);value=self._quantities.get(key,Decimal("0"))+quantity_delta
        if value:self._quantities[key]=value
        else:self._quantities.pop(key,None)
    def strategy_positions(self,strategy_id: str) -> tuple[StrategyPosition,...]:
        return tuple(StrategyPosition(strategy,instrument,quantity) for (strategy,instrument),quantity in sorted(
            self._quantities.items(),key=lambda item:(item[0][0],item[0][1].value)) if strategy==strategy_id)
    def netted_positions(self) -> tuple[NettedPosition,...]:
        grouped={}
        for (strategy,instrument),quantity in self._quantities.items():grouped.setdefault(instrument,[]).append(StrategyPosition(strategy,instrument,quantity))
        return tuple(NettedPosition(instrument,sum((item.quantity for item in values),Decimal("0")),tuple(sorted(values,key=lambda item:item.strategy_id)))
            for instrument,values in sorted(grouped.items(),key=lambda item:item[0].value))
    def reconcile(self,account_positions: dict[InstrumentId,Decimal]) -> tuple[str,...]:
        expected={item.instrument_id:item.account_quantity for item in self.netted_positions() if item.account_quantity}
        actual={key:value for key,value in account_positions.items() if value};messages=[]
        for instrument in sorted(set(expected)|set(actual),key=lambda item:item.value):
            if expected.get(instrument,Decimal("0"))!=actual.get(instrument,Decimal("0")):
                messages.append(f"{instrument.value}: virtual={expected.get(instrument,Decimal('0'))} account={actual.get(instrument,Decimal('0'))}")
        return tuple(messages)
