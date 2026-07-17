from __future__ import annotations

from collections import defaultdict

from trading.adapters.base import MarketDataAdapter
from trading.reference.models import InstrumentDefinition
from trading.domain.product import ProductType


class CompositeMarketDataAdapter:
    """Routes normalized definitions by product without leaking Venue SDK types."""

    def __init__(self, routes: dict[ProductType, MarketDataAdapter]) -> None:
        for product_type, adapter in routes.items():
            adapter.capabilities.require_product(product_type)
        self.routes = routes

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]):
        grouped = defaultdict(list)
        for definition in instruments:
            adapter = self.routes.get(definition.instrument_type)
            if adapter is None:
                raise ValueError(f"no market data route for {definition.instrument_type}")
            grouped[adapter].append(definition)
        values = []
        for adapter, definitions in grouped.items():
            values.extend(adapter.snapshot(tuple(definitions)))
        by_id = {item.instrument_id: item for item in values}
        return tuple(by_id[item.instrument_id] for item in instruments)
