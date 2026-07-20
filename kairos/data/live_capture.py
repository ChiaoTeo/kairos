from __future__ import annotations

import json
from pathlib import Path

from kairos.storage.data_lake import write_json

from .catalog import DataCatalog
from .contracts import (
    DataProductContract,
    DataProductDefinition,
    DataReleaseManifest,
    DataSetContractArtifact,
    DatasetKey,
    DatasetLayer,
    DatasetRelease,
    DatasetStatus,
    DatasetStorageKind,
    QualityLevel,
    SourceBinding,
)


def register_live_capture_release(
    root: str | Path,
    *,
    dataset_id: str,
    capture_manifest_path: str | Path,
    run_id: str,
    live_view_id: str,
    provider: str,
    venue: str | None = None,
    primary_time: str = "available_time",
    quality_level: QualityLevel = QualityLevel.INTEGRITY,
) -> DatasetRelease:
    lake = Path(root)
    manifest_path = Path(capture_manifest_path)
    try:
        directory = manifest_path.parent.resolve().relative_to(lake.resolve())
    except ValueError as error:
        raise ValueError("live capture release storage must be inside the governed lake root") from error
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    content_hash = str(payload.get("content_sha256") or "")
    if not content_hash:
        raise ValueError("live capture manifest must contain content_sha256")
    release_id = f"{dataset_id}:live-capture:{content_hash[:16]}"
    published_at = str(payload.get("finalized_at") or "")
    product = DataProductDefinition(
        DatasetKey(dataset_id),
        dataset_id,
        _dataset_layer(dataset_id),
        description="Canonical live capture replay artifact",
        dimensions={"source": "live-capture", "provider": provider},
        primary_time=primary_time,
        sources=(SourceBinding(provider, venue, 100, quality_level, ("live-capture",)),),
    )
    spec = DataProductContract(
        product,
        directory.as_posix(),
        "canonical_capture.v1",
        storage_kind=DatasetStorageKind.MARKET_EVENTS,
        quality_profile="live-capture",
        minimum_publication_level=quality_level,
    )
    release = DatasetRelease(
        release_id,
        product.key,
        str(payload.get("session_id") or release_id),
        spec.schema_id,
        "1",
        "live.canonical_capture",
        "1",
        directory.as_posix(),
        "canonical-jsonl",
        content_hash,
        provider,
        venue,
        (),
        DatasetStatus.APPROVED_FOR_STUDY,
        quality_level,
        published_at,
        DatasetStorageKind.MARKET_EVENTS,
        "1",
    )
    release_manifest = DataReleaseManifest(
        dataset_id,
        release_id,
        DataSetContractArtifact.from_product_contract(spec).contract_hash,
        content_hash,
        primary_time,
        ("message_id", "available_time", "event_time", "instrument_id", "kind", "payload"),
        quality_level,
        {
            "provider": provider,
            "venue": venue,
            "transform_id": release.transform_id,
            "transform_version": release.transform_version,
            "run_id": run_id,
            "live_view_id": live_view_id,
            "capture_manifest": str(manifest_path.relative_to(lake)),
        },
        published_at,
    )
    target = lake / directory
    write_json(target / "data_release_manifest.json", release_manifest.to_primitive())
    write_json(target / "release.json", {
        "release_schema_version": 1,
        "release_id": release.release_id,
        "logical_key": dataset_id,
        "release_version": release.release_version,
        "schema_id": release.schema_id,
        "schema_version": release.schema_version,
        "transform_id": release.transform_id,
        "transform_version": release.transform_version,
        "content_hash": release.content_hash,
        "contract_hash": release_manifest.contract_hash,
        "data_release_manifest_hash": release_manifest.manifest_hash,
        "artifact_ref": release_manifest.artifact_ref,
        "provider": provider,
        "venue": venue,
        "status": release.status.value,
        "quality_level": release.quality_level.value,
        "published_at": release.published_at,
        "run_id": run_id,
        "live_view_id": live_view_id,
    })
    write_json(target / "manifest.json", {
        "manifest_version": 1,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "source_capture_manifest": str(manifest_path.relative_to(lake)),
        "segments": payload.get("segments", []),
        "event_count": payload.get("event_count", 0),
        "content_sha256": content_hash,
    })
    write_json(target / "schema.json", {
        "schema_id": release.schema_id,
        "schema_version": release.schema_version,
        "primary_time": primary_time,
        "fields": ["message_id", "available_time", "event_time", "instrument_id", "kind", "payload"],
    })
    write_json(target / "lineage.json", {
        "lineage_version": 1,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "producer": {"name": "live.canonical_capture", "version": "1"},
        "source": {"provider": provider, "venue": venue, "run_id": run_id, "live_view_id": live_view_id},
        "point_in_time_safe": True,
    })
    write_json(target / "coverage.json", {
        "dataset_id": dataset_id,
        "release_id": release_id,
        "timezone": "UTC",
        "boundary": "[first,last]",
        "coverage": {
            "start": payload.get("first_available_time"),
            "end": payload.get("last_available_time"),
            "events": payload.get("event_count", 0),
            "segments": payload.get("segment_count", 0),
        },
    })
    write_json(target / "quality.json", {
        "quality_schema_version": 1,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "passed": int(payload.get("event_count") or 0) > 0,
        "checks": [
            {"name": "non_empty_capture", "passed": int(payload.get("event_count") or 0) > 0, "value": payload.get("event_count", 0)},
            {"name": "content_hash_present", "passed": bool(content_hash), "value": content_hash},
        ],
    })
    write_json(target / "capabilities.json", {
        "capability_schema_version": 1,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "replayable_canonical_events": True,
        "source": "live-capture",
    })
    write_json(target / "usage.json", {
        "usage_schema_version": 1,
        "logical_key": dataset_id,
        "default_view": "raw-as-received",
        "known_limitations": ["live capture coverage is bounded by the runtime session duration"],
    })
    catalog = DataCatalog(lake)
    catalog.register_product_spec(spec, enrich=True)
    catalog.register_release(release)
    catalog.save()
    return release


def _dataset_layer(dataset_id: str) -> DatasetLayer:
    head = dataset_id.split(".", 1)[0]
    values = {item.value: item for item in DatasetLayer}
    return values.get(head, DatasetLayer.CANONICAL)
