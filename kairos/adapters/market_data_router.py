from __future__ import annotations

from collections import defaultdict

from kairos.ports import MarketDataPort
from kairos.reference.contracts import InstrumentDefinition
from kairos.domain.product import ProductType


class CompositeMarketDataClient:
    """Routes normalized definitions by product without leaking Venue SDK types."""

    def __init__(self, routes: dict[ProductType, MarketDataPort]) -> None:
        for product_type, market_data_client in routes.items():
            market_data_client.capabilities.require_product(product_type)
        self.routes = routes

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]):
        grouped = defaultdict(list)
        for definition in instruments:
            market_data_client = self.routes.get(definition.instrument_type)
            if market_data_client is None:
                raise ValueError(f"no market data route for {definition.instrument_type}")
            grouped[market_data_client].append(definition)
        values = []
        for market_data_client, definitions in grouped.items():
            values.extend(market_data_client.snapshot(tuple(definitions)))
        by_id = {item.instrument_id: item for item in values}
        return tuple(by_id[item.instrument_id] for item in instruments)


CompositeMarketDataAdapter = CompositeMarketDataClient
