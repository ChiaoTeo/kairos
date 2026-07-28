from __future__ import annotations

from kairospy.core.reference import MarketRef


def market_data_id(kind: object, market_ref: MarketRef, *, timeframe: str | None = None) -> str:
    parts = ("market", _dataset_kind(kind), market_ref.market_key, _optional_segment(timeframe))
    return ".".join(part for part in parts if part)


def market_stream_name(kind: object, market_ref: MarketRef, *, timeframe: str | None = None) -> str:
    return market_data_id(kind, market_ref, timeframe=timeframe)


def _dataset_kind(value: object) -> str:
    text = str(value).strip().lower()
    if text == "trade":
        return "trades"
    if not text:
        raise ValueError("market data kind cannot be empty")
    return text


def _optional_segment(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        raise ValueError("market data timeframe cannot be empty")
    return text


__all__ = ["market_data_id", "market_stream_name"]
