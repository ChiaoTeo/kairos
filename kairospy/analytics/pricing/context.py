from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.identity import InstrumentId
from kairospy.reference.contracts import ListedOptionSpec
from kairospy.reference import ReferenceCatalog, ReferenceRole, SettlementTerms


@dataclass(frozen=True, slots=True)
class PricingContext:
    instrument_id: InstrumentId
    valuation_time: datetime
    underlying_instrument_id: InstrumentId
    underlying_value: Decimal
    contract_spec: ListedOptionSpec
    settlement_terms: SettlementTerms | None = None


class PricingContextResolver:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def resolve(self, instrument_id: InstrumentId, at: datetime, reference_prices: dict[InstrumentId, Decimal], *, settlement_terms: SettlementTerms | None = None) -> PricingContext:
        definition = self.catalog.instruments.get(instrument_id, at)
        if not isinstance(definition.contract_spec, ListedOptionSpec):
            raise TypeError(f"pricing context currently requires listed option: {instrument_id}")
        references = self.catalog.references(instrument_id, ReferenceRole.PRICING_UNDERLYING, at)
        underlying_ids = [item.target.instrument_id for item in references if item.target.instrument_id is not None]
        if len(underlying_ids) != 1:
            raise LookupError(f"expected one pricing underlying: {instrument_id} at {at}")
        underlying_id = underlying_ids[0]
        try:
            value = reference_prices[underlying_id]
        except KeyError as error:
            raise LookupError(f"missing underlying value: {underlying_id} at {at}") from error
        if value <= 0:
            raise ValueError("underlying value must be positive")
        return PricingContext(instrument_id, at, underlying_id, value, definition.contract_spec, settlement_terms)
