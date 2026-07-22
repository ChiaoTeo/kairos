from __future__ import annotations

from datetime import timedelta, timezone
import json
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
from kairospy.infrastructure.storage.codec import from_primitive, to_primitive
from kairospy.infrastructure.storage.data_lake import write_intraday_dataset
from kairospy.analytics.volatility.contracts import SurfaceSnapshot

from kairospy.data.catalog import DataCatalog
from kairospy.data.client import DatasetClient
from kairospy.data.contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetStorageKind, QualityLevel,
)
from kairospy.data.products import capabilities_payload
from kairospy.data.publishing import content_release_id, publish_release, release_path
from kairospy.data.quality import DatasetQualityService


class SurfaceFeaturePublisher:
    """Publish calibrated volatility surfaces as immutable Feature Releases."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)

    def publish(self, surfaces: tuple[SurfaceSnapshot, ...], *, input_release_id: str):
        if not surfaces:
            raise ValueError("surface Feature Release cannot be empty")
        catalog = DataCatalog(self.root)
        input_release = catalog.release(input_release_id)
        underlying = surfaces[0].underlying_id.value
        if any(item.underlying_id.value != underlying for item in surfaces):
            raise ValueError("one surface Feature Release must contain one underlying")
        slug = underlying.lower().replace(":", "-")
        product = DataProductDefinition(
            DatasetKey(f"features.volatility_surface.{slug}"),
            f"{underlying} calibrated volatility surfaces",
            DatasetLayer.FEATURES,
            f"Point-in-time calibrated SVI surfaces derived from {input_release.product_key}.",
            {"underlying": underlying, "model": "svi", "frequency": "event"},
            "available_time",
            owner="workspace-platform",
        )
        spec = DataProductContract(
            product,
            f"features/volatility_surface/underlying={slug}",
            "features.volatility_surface.snapshot.v1",
            {
                "point_in_time_universe": True,
                "supported_return_drivers": ["volatility", "skew", "term_structure"],
            },
            DatasetStorageKind.TABULAR,
            "1",
            "feature",
            QualityLevel.WORKSPACE,
        )
        catalog.register_product_spec(spec, enrich=True)
        catalog.save()
        rows = []
        for surface in sorted(surfaces, key=lambda item: (item.as_of, item.surface_id)):
            start = surface.as_of.astimezone(timezone.utc)
            end = start + timedelta(microseconds=1)
            rows.append({
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "event_time": end.isoformat(),
                "available_time": end.isoformat(),
                "surface_id": surface.surface_id,
                "underlying_id": surface.underlying_id.value,
                "model": surface.model,
                "model_version": surface.model_version,
                "input_hash": surface.input_hash,
                "calibration_status": surface.calibration_status.value,
                "diagnostics_passed": surface.diagnostics.passed,
                "surface_json": json.dumps(to_primitive(surface), sort_keys=True, separators=(",", ":")),
            })
        release_id = content_release_id(spec, rows)
        target = self.root / release_path(spec, release_id)
        manifest = write_intraday_dataset(
            target,
            rows,
            dataset_id=release_id,
            schema={
                "schema_id": spec.schema_id,
                "schema_version": 1,
                "primary_key": ["surface_id"],
            },
            lineage={
                "producer": {"name": type(self).__name__, "transform": "surface_snapshot_to_feature", "version": 1},
                "inputs": [{
                    "release_id": input_release.release_id,
                    "dataset_id": str(input_release.product_key),
                    "content_hash": input_release.content_hash,
                }],
                "point_in_time_safe": True,
                "contains_forward_labels": False,
            },
            interval=timedelta(minutes=1),
            capabilities=capabilities_payload(spec, release_id),
        )
        release = publish_release(
            self.root,
            spec,
            release_id,
            manifest,
            provider="internal",
            venue=None,
            transform_id="surface_snapshot_to_feature",
            transform_version="1",
            quality_level=QualityLevel.WORKSPACE,
        )
        assessment = DatasetQualityService(self.root).assess(release.release_id)
        if not assessment.passed:
            raise RuntimeError(f"surface Feature Release failed typed quality: {release.release_id}")
        return DataCatalog(self.root).release(release.release_id)


def load_surface_features(root: str | Path, dataset: str) -> tuple[SurfaceSnapshot, ...]:
    rows = DatasetClient(root).load_rows(dataset)
    return tuple(from_primitive(json.loads(str(row["surface_json"])), SurfaceSnapshot) for row in rows)
