"""Python-facing Reference contract."""

from __future__ import annotations

from pathlib import Path
import json

from .reference_client import ReferenceSnapshotClient

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope


def catalog_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.reference.v1.CatalogSnapshot import (
        CatalogSnapshot,
    )

    return MmapSnapshotReader(path, file_identifier=b"PRC1", root_type=CatalogSnapshot)


def markets_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.reference.v1.MarketsSnapshot import (
        MarketsSnapshot,
    )

    return MmapSnapshotReader(path, file_identifier=b"PRD1", root_type=MarketsSnapshot)


def read_manifest(path: str | Path) -> dict[str, object]:
    """Read the atomically committed Reference snapshot manifest.

    Individual view files are only a coherent set when their manifest exists
    and names the generation the caller is about to consume.
    """

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    expected_views = {
        "reference.catalog",
        "reference.entities",
        "reference.assets",
        "reference.instruments",
        "reference.listings",
        "reference.markets",
        "reference.financial_products",
        "reference.execution_accesses",
    }
    views = value.get("views") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("generation"), int)
        or value["generation"] < 0
        or not isinstance(value.get("event_sequence"), int)
        or value["event_sequence"] < 0
        or not isinstance(views, list)
        or not all(isinstance(view, str) for view in views)
        or set(views) != expected_views
    ):
        raise ValueError("invalid Reference snapshot manifest")
    return value


def client(
    *,
    socket_path: str | Path | None = None,
    snapshot_path: str | Path | None = None,
    markets_snapshot_path: str | Path | None = None,
) -> ReferenceSnapshotClient:
    return ReferenceSnapshotClient(
        socket_path=None if socket_path is None else Path(socket_path),
        snapshot_path=None if snapshot_path is None else Path(snapshot_path),
        markets_snapshot_path=(
            None if markets_snapshot_path is None else Path(markets_snapshot_path)
        ),
    )


__all__ = [
    "CommandEnvelope",
    "QueryEnvelope",
    "ReferenceSnapshotClient",
    "catalog_reader",
    "client",
    "markets_reader",
    "read_manifest",
]
