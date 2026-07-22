from __future__ import annotations

from datetime import timedelta, timezone
import json
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
from kairospy.infrastructure.storage.codec import from_primitive, to_primitive
from kairospy.analytics.volatility.contracts import SurfaceSnapshot

from kairospy.data.catalog import DataCatalog
from kairospy.data.storage.client import DatasetClient


class SurfaceFeaturePublisher:
    """Publish calibrated volatility surfaces as immutable Feature Releases."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)

    def publish(self, surfaces: tuple[SurfaceSnapshot, ...], *, input_release_id: str):
        raise RuntimeError("surface Feature Release publishing has been removed; write features through DatasetWriter")


def load_surface_features(root: str | Path, dataset: str) -> tuple[SurfaceSnapshot, ...]:
    try:
        release = DataCatalog(root).release(dataset)
    except Exception:
        rows = DatasetClient(root).load_rows(dataset)
    else:
        import pyarrow.parquet as pq

        rows = []
        for path in sorted((Path(root) / release.relative_path).glob("**/*.parquet")):
            rows.extend(pq.read_table(path).to_pylist())
    return tuple(from_primitive(json.loads(str(row["surface_json"])), SurfaceSnapshot) for row in rows)
