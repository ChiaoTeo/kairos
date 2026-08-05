from __future__ import annotations

from kairospy.domain.reference import MarketRef

from .datasets import market_dataset_id
from .specs import MarketDataKind


def market_data_id(kind: MarketDataKind | str, market_ref: MarketRef, *, timeframe: str | None = None) -> str:
    return market_dataset_id(kind, market_ref, timeframe=timeframe)


def market_stream_name(kind: MarketDataKind | str, market_ref: MarketRef, *, timeframe: str | None = None) -> str:
    return market_data_id(kind, market_ref, timeframe=timeframe)


__all__ = ["market_data_id", "market_stream_name"]
