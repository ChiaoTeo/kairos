from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kairospy.backtest.feed import MarketReplayDataset, build_manifest
from kairospy.storage.data_lake import write_json

from .catalog import DataCatalog
from .client import DatasetClient
from .market_snapshot_storage import MarketSnapshotStorageDriver
from .contracts import DataProductContract, DatasetStorageKind, QualityLevel
from .publishing import register_market_replay_dataset
from .quality import DatasetQualityService


def curate_complete_market_snapshots(
    root: str | Path,
    source_release_id: str,
    *,
    input_event_release_id: str,
):
    lake = Path(root)
    catalog = DataCatalog(lake)
    source_release = catalog.release(source_release_id)
    event_release = catalog.release(input_event_release_id)
    source = DatasetClient(lake).replay_snapshots(source_release.release_id).dataset
    slices = tuple(
        market for market in source.slices
        if not any(issue.severity == "error" for issue in market.quality_issues)
        and all(snapshot.quote is not None for snapshot in market.instruments)
    )
    if len(slices) < 2:
        raise ValueError("curated MarketSnapshot Release requires at least two complete slices")
    provisional = build_manifest(
        "pending",
        slices,
        source.contracts,
        source.definitions,
        sampling_seconds=source.manifest.sampling_seconds,
        source=source.manifest.source,
        market_data_type=source.manifest.market_data_type,
        code_version=source.manifest.code_version,
        split=source.manifest.split,
        synthetic=source.manifest.synthetic,
        products=source.products, references=source.references, settlements=source.settlements,
    )
    release_id = f"ds_{provisional.content_hash[:24]}"
    manifest = replace(provisional, dataset_id=release_id)
    dataset = MarketReplayDataset(
        manifest, slices, source.contracts, source.definitions,
        source.products, source.references, source.settlements,
    )
    product = catalog.product(source_release.product_key)
    base = Path("curated") / "market_snapshots" / f"product={product.key}"
    try:
        spec = catalog.product_spec(product)
    except KeyError:
        spec = DataProductContract(
            product, base.as_posix(), "historical_dataset.v2", {}, DatasetStorageKind.MARKET_SNAPSHOTS,
            "1", "market_snapshot", QualityLevel.BACKTEST,
        )
        catalog.register_product_spec(spec, enrich=True)
    else:
        if spec.quality_profile != "market_snapshot" or spec.relative_path != base.as_posix():
            catalog.update_product_spec(
                replace(spec, quality_profile="market_snapshot", relative_path=base.as_posix()),
                actor="market-snapshot-curation",
                reason="publish complete point-in-time MarketSnapshot Release",
            )
    catalog.save()
    directory = MarketSnapshotStorageDriver(lake / base).save(dataset, directory_name=f"release={release_id}")
    write_json(directory / "lineage.json", {
        "lineage_version": 2,
        "producer": {"name": "curate_complete_market_snapshots", "version": 1},
        "inputs": [
            {"release_id": source_release.release_id, "dataset_id": str(source_release.product_key),
             "content_hash": source_release.content_hash},
            {"release_id": event_release.release_id, "dataset_id": str(event_release.product_key),
             "content_hash": event_release.content_hash},
        ],
        "filters": {"error_severity_issues": 0, "requires_quote_for_all_snapshots": True},
        "point_in_time_safe": True,
        "contains_forward_labels": False,
    })
    release = register_market_replay_dataset(
        lake,
        dataset,
        directory,
        product,
        provider=source_release.provider or "internal",
        venue=source_release.venue,
        synthetic=source.manifest.synthetic,
    )
    assessment = DatasetQualityService(lake).assess(release.release_id)
    if not assessment.passed or assessment.level is not QualityLevel.BACKTEST:
        raise RuntimeError(f"curated MarketSnapshot Release failed Q3 quality: {release.release_id}")
    return DataCatalog(lake).release(release.release_id)
