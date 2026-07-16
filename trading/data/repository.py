from __future__ import annotations

import csv
import json
from pathlib import Path

from .catalog import DataCatalog


class CanonicalDatasetRepository:
    def __init__(self, root: str | Path = "data") -> None:
        self.catalog = DataCatalog(root)

    def load_rows(self, dataset_id: str) -> list[dict[str, str]]:
        return list(self.iter_rows(dataset_id))

    def iter_rows(self, dataset_id: str):
        root = self.catalog.path(dataset_id)
        if not (root / "manifest.json").exists():
            raise FileNotFoundError(f"dataset {dataset_id!r} is not prepared; run 'trader data prepare-btc-options'")
        for path in sorted(root.glob("event_year=*/event_month=*/part-*.csv")):
            with path.open(newline="", encoding="utf-8") as handle:
                yield from csv.DictReader(handle)

    def metadata(self, dataset_id: str) -> dict[str, object]:
        root = self.catalog.path(dataset_id)
        return {name: json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
                for name in ("schema", "lineage", "coverage", "manifest", "capabilities")}
