from __future__ import annotations

from pathlib import Path

from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.storage.data_lake import write_json

from .catalog import DataCatalog
from .contracts import DataReleaseManifest, DataSetContractArtifact, DatasetStorageKind, QualityLevel


REQUIRED_RELEASE_METADATA = (
    "schema.json", "lineage.json", "coverage.json", "quality.json", "manifest.json",
    "data_release_manifest.json", "capabilities.json", "usage.json", "release.json",
)


def ensure_release_metadata(root: str | Path, release_id: str) -> dict[str, object]:
    """Complete and verify metadata for one release produced by the current publishers."""
    lake = Path(root)
    catalog = DataCatalog(lake)
    release = catalog.release(release_id)
    product = catalog.product(release.product_key)
    directory = lake / release.relative_path
    if not directory.exists():
        raise FileNotFoundError(f"release directory is missing: {directory}")
    payloads = _market_replay_payloads(directory, release, product) if (directory / "dataset.json").exists() else _common_payloads(release, product)
    written = []
    for name, payload in payloads.items():
        target = directory / name
        if not target.exists():
            write_json(target, payload)
            written.append(name)
    missing = tuple(name for name in REQUIRED_RELEASE_METADATA if not (directory / name).exists())
    if missing:
        raise RuntimeError(f"release metadata is incomplete: {release_id}: {', '.join(missing)}")
    return {"release_id": release_id, "written": tuple(written), "complete": True}


def verify_release_metadata(root: str | Path, release_id: str) -> dict[str, object]:
    lake = Path(root)
    release = DataCatalog(lake).release(release_id)
    directory = lake / release.relative_path
    missing = tuple(name for name in REQUIRED_RELEASE_METADATA if not (directory / name).exists())
    return {"release_id": release_id, "missing": missing, "complete": not missing}


def _market_replay_payloads(directory: Path, release, product) -> dict[str, object]:
    dataset = MarketSnapshotStorageDriver(directory.parent).load(directory)
    parquet = directory / "slices.parquet"
    files = []
    if parquet.exists():
        from kairospy.storage.data_lake import sha256_bytes
        files.append({
            "path": parquet.name, "bytes": parquet.stat().st_size,
            "sha256": sha256_bytes(parquet.read_bytes()), "rows": dataset.manifest.slice_count,
        })
    checks = (
        {"name": "content_hash", "passed": dataset.manifest.content_hash == release.content_hash,
         "value": dataset.manifest.content_hash},
        {"name": "non_empty", "passed": dataset.manifest.slice_count > 0, "value": dataset.manifest.slice_count},
        {"name": "storage_file", "passed": bool(files), "value": len(files)},
    )
    payloads = {
        "schema.json": {"schema_id": release.schema_id or "market_replay_dataset.v2", "schema_version": 2,
                        "primary_key": ["timestamp", "sequence"], "primary_time": "timestamp"},
        "lineage.json": {"lineage_version": 2, "dataset_id": release.release_id,
                         "producer": {"name": release.transform_id or "market_replay_dataset",
                                      "version": dataset.manifest.code_version},
                         "source": {"provider": release.provider or dataset.manifest.source},
                         "point_in_time_safe": True},
        "coverage.json": {"dataset_id": release.release_id, "timezone": "UTC", "boundary": "[start,end)",
                          "coverage": {"start": dataset.manifest.start.isoformat(), "end": dataset.manifest.end.isoformat(),
                                       "slices": dataset.manifest.slice_count,
                                       "contract_coverage": str(dataset.manifest.contract_coverage),
                                       "quote_coverage": str(dataset.manifest.quote_coverage),
                                       "stale_rate": str(dataset.manifest.stale_rate)}},
        "quality.json": {"quality_schema_version": 1, "dataset_id": release.release_id,
                         "passed": all(item["passed"] for item in checks), "checks": checks},
        "manifest.json": {"manifest_version": 2, "dataset_id": release.release_id,
                          "files": files, "rows": dataset.manifest.slice_count,
                          "dataset_sha256": dataset.manifest.content_hash},
        "capabilities.json": {"capability_schema_version": 2, "dataset_id": release.release_id,
                              "point_in_time_universe": True, "synchronous_quotes": True,
                              "top_of_book": True, "maximum_validation_level": 2},
    }
    common = _common_payloads(release, product, fields=("timestamp", "sequence", "slice_json"))
    common.pop("capabilities.json")
    payloads.update(common)
    return payloads


def _common_payloads(release, product, *, fields: tuple[str, ...] = ()) -> dict[str, object]:
    release_manifest = _data_release_manifest(release, product, fields=fields)
    return {
        "capabilities.json": {"capability_schema_version": 2, "dataset_id": release.release_id,
                              "point_in_time_universe": True, "maximum_validation_level": 2},
        "usage.json": {"usage_schema_version": 1, "logical_key": str(release.product_key),
                       "default_view": "raw-as-received", "known_limitations": []},
        "data_release_manifest.json": release_manifest.to_primitive(),
        "release.json": {"release_schema_version": 1, "release_id": release.release_id,
                         "logical_key": str(release.product_key), "content_hash": release.content_hash,
                         "schema_id": release.schema_id, "schema_version": release.schema_version,
                         "transform_id": release.transform_id, "transform_version": release.transform_version,
                         "contract_hash": release_manifest.contract_hash,
                         "data_release_manifest_hash": release_manifest.manifest_hash,
                         "artifact_ref": release_manifest.artifact_ref,
                         "provider": release.provider, "venue": release.venue, "status": release.status.value,
                         "quality_level": release.quality_level.value, "published_at": release.published_at},
    }


def _data_release_manifest(release, product, *, fields: tuple[str, ...]) -> DataReleaseManifest:
    contract = DataSetContractArtifact(
        dataset_id=str(release.product_key),
        title=product.title,
        layer=product.layer,
        primary_time=product.primary_time,
        schema_id=release.schema_id,
        storage_kind=release.storage_kind or DatasetStorageKind.TABULAR,
        layout_version=release.layout_version,
        quality_profile="market_snapshot" if release.storage_kind is DatasetStorageKind.MARKET_SNAPSHOTS else "generic",
        minimum_publication_level=release.quality_level if isinstance(release.quality_level, QualityLevel) else QualityLevel.WORKSPACE,
    )
    return DataReleaseManifest(
        str(release.product_key),
        release.release_id,
        contract.contract_hash,
        release.content_hash or "",
        product.primary_time,
        fields,
        release.quality_level,
        {"provider": release.provider, "venue": release.venue, "transform_id": release.transform_id, "transform_version": release.transform_version},
        release.published_at or "",
    )
