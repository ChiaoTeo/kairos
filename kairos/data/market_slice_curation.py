from __future__ import annotations

"""Compatibility exports for market snapshot curation helpers."""

from .market_snapshot_curation import curate_complete_market_snapshots

curate_complete_market_slices = curate_complete_market_snapshots

__all__ = [
    "curate_complete_market_snapshots",
    "curate_complete_market_slices",
]
