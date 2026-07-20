from __future__ import annotations

import json
from pathlib import Path

from kairos.backtest.feed import (
    InstrumentLifecycleSnapshot, DatasetManifest, MarketReplayDataset, MarketSnapshot, build_manifest,
)
from kairos.reference.repository import instrument_from_primitive, instrument_to_primitive
from kairos.reference.contracts import EconomicProduct, InstrumentReference, SettlementTermsDefinition
from kairos.storage.codec import from_primitive, to_primitive


class MarketSnapshotStorageDriver:
    """Internal physical driver for governed MarketSnapshot Releases."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, dataset: MarketReplayDataset, *, directory_name: str | None = None) -> Path:
        directory = self.root / (directory_name or dataset.manifest.dataset_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "storage_version": 2,
            "format": "parquet",
            "manifest": to_primitive(dataset.manifest),
            "contracts": to_primitive(dataset.contracts),
            "definitions": [instrument_to_primitive(item) for item in dataset.definitions],
            "products": to_primitive(dataset.products),
            "references": to_primitive(dataset.references),
            "settlements": to_primitive(dataset.settlements),
        }
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            payload = {**payload, "storage_version": 1, "format": "json", "slices": to_primitive(dataset.slices)}
        else:
            rows = [{
                "timestamp": item.timestamp,
                "sequence": item.sequence,
                "slice_json": json.dumps(to_primitive(item), sort_keys=True, separators=(",", ":")),
            } for item in dataset.slices]
            table = pa.Table.from_pylist(rows, schema=pa.schema([
                ("timestamp", pa.timestamp("ns", tz="UTC")),
                ("sequence", pa.int64()),
                ("slice_json", pa.string()),
            ]))
            temporary = directory / "slices.parquet.tmp"
            pq.write_table(table, temporary, compression="zstd")
            temporary.replace(directory / "slices.parquet")
        temporary = directory / "dataset.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(directory / "dataset.json")
        return directory

    def load(self, release_directory: str | Path) -> MarketReplayDataset:
        directory = Path(release_directory)
        if not directory.exists():
            directory = self.root / directory
        value = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
        manifest = from_primitive(value["manifest"], DatasetManifest)
        if manifest.schema_version != 1:
            raise ValueError(f"unsupported dataset schema version: {manifest.schema_version}")
        contracts = tuple(from_primitive(item, InstrumentLifecycleSnapshot) for item in value["contracts"])
        definitions = tuple(instrument_from_primitive(item) for item in value["definitions"])
        products = tuple(from_primitive(item, EconomicProduct) for item in value.get("products", ()))
        references = tuple(from_primitive(item, InstrumentReference) for item in value.get("references", ()))
        settlements = tuple(from_primitive(item, SettlementTermsDefinition) for item in value.get("settlements", ()))
        if value.get("format") == "parquet":
            try:
                import pyarrow.parquet as pq
            except ImportError as error:
                raise RuntimeError("loading Parquet MarketSnapshot Release requires the data optional dependency") from error
            table = pq.read_table(directory / "slices.parquet", columns=["slice_json"])
            slices = tuple(from_primitive(json.loads(item), MarketSnapshot) for item in table.column("slice_json").to_pylist())
        else:
            slices = tuple(from_primitive(item, MarketSnapshot) for item in value["slices"])
        dataset = MarketReplayDataset(manifest, slices, contracts, definitions, products, references, settlements)
        rebuilt = build_manifest(
            manifest.dataset_id,
            slices,
            contracts,
            definitions,
            sampling_seconds=manifest.sampling_seconds,
            source=manifest.source,
            market_data_type=manifest.market_data_type,
            code_version=manifest.code_version,
            split=manifest.split,
            synthetic=manifest.synthetic,
            products=products, references=references, settlements=settlements,
        )
        if rebuilt.content_hash != manifest.content_hash:
            raise ValueError("market snapshot release content hash mismatch")
        return dataset
