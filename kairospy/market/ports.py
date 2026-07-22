from __future__ import annotations

from typing import Protocol

from kairospy.identity import VenueId
from kairospy.market.subscriptions import MarketDataCapabilities
from kairospy.market.types import Quote
from kairospy.reference.contracts import InstrumentDefinition


class MarketDataPort(Protocol):
    venue_id: VenueId
    capabilities: MarketDataCapabilities

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]: ...


__all__ = ["MarketDataPort"]
