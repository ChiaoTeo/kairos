from __future__ import annotations

from dataclasses import dataclass

from kairospy.identity import InstrumentId
from kairospy.reference.contracts import InstrumentDefinition


@dataclass(frozen=True, slots=True)
class CcxtSymbolMapper:
    symbols: dict[InstrumentId, str]

    def symbol_for(self, instrument_id: InstrumentId) -> str:
        try:
            return self.symbols[instrument_id]
        except KeyError as error:
            raise LookupError(f"CCXT symbol mapping unavailable for {instrument_id}") from error

    def instrument_for(self, symbol: str) -> InstrumentId:
        matches = [instrument_id for instrument_id, mapped in self.symbols.items() if mapped == symbol]
        if len(matches) != 1:
            raise LookupError(f"CCXT instrument mapping unavailable or ambiguous for {symbol}")
        return matches[0]

    @classmethod
    def from_instrument_definitions(cls, instruments: tuple[InstrumentDefinition, ...]) -> "CcxtSymbolMapper":
        return cls({item.instrument_id: item.display_name or item.instrument_id.value for item in instruments})
