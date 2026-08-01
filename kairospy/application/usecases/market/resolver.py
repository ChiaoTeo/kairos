from __future__ import annotations

from dataclasses import dataclass

from kairospy.core.reference import MarketRef, MarketResolver

from .datasets import parse_market_dataset_id
from .identity import market_data_id, market_stream_name
from .specs import MarketDataSpec


@dataclass(frozen=True, slots=True)
class ResolvedMarketData:
    spec: MarketDataSpec
    market_ref: MarketRef
    dataset_id: str
    stream_name: str


class MarketDataResolver:
    def __init__(
        self,
        market_resolver: MarketResolver | None = None,
        *,
        default_venue: str | None = None,
        default_market: str | None = None,
    ) -> None:
        self.market_resolver = market_resolver or MarketResolver(default_venue=default_venue, default_market=default_market)

    def resolve(self, spec: MarketDataSpec) -> ResolvedMarketData:
        if spec.dataset is not None:
            parsed = parse_market_dataset_id(spec.dataset)
            market_ref = self.market_resolver.resolve(parsed.source_symbol, venue=parsed.venue, market=parsed.market)
            return ResolvedMarketData(
                spec,
                market_ref,
                parsed.dataset_id,
                spec.stream or parsed.dataset_id,
            )
        market_ref = self.market_resolver.resolve(spec.symbol, venue=spec.venue, market=spec.market)
        dataset_id = spec.dataset or market_data_id(spec.kind, market_ref, timeframe=spec.timeframe)
        stream_name = spec.stream or market_stream_name(spec.kind, market_ref, timeframe=spec.timeframe)
        return ResolvedMarketData(
            spec,
            market_ref,
            dataset_id,
            stream_name,
        )


__all__ = ["MarketDataResolver", "ResolvedMarketData"]
