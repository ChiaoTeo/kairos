from __future__ import annotations

"""Compatibility exports for market snapshot storage helpers."""

from .market_snapshot_storage import MarketSnapshotStorageDriver

MarketSliceStorageDriver = MarketSnapshotStorageDriver

__all__ = [
    "MarketSnapshotStorageDriver",
    "MarketSliceStorageDriver",
]
