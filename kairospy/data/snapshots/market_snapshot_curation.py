from __future__ import annotations

from pathlib import Path


def curate_complete_market_snapshots(
    root: str | Path,
    source_release_id: str,
    *,
    input_event_release_id: str,
):
    raise RuntimeError("market snapshot release publishing has been removed; write curated datasets through DatasetWriter")
