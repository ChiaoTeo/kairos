"""Compatibility shim for the former composite market data adapter module."""

from __future__ import annotations

from .market_data_router import CompositeMarketDataAdapter, CompositeMarketDataClient

__all__ = ["CompositeMarketDataAdapter", "CompositeMarketDataClient"]
