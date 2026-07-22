from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from .ids import DatasetId, normalize_alias, normalize_dataset_id


@dataclass(frozen=True, slots=True)
class DatasetLayout:
    """Path contract for the simplified Data store."""

    root: Path

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        object.__setattr__(self, "root", Path(root))

    @property
    def datasets_root(self) -> Path:
        return self.root / "datasets"

    @property
    def aliases_root(self) -> Path:
        return self.root / "aliases"

    @property
    def index_root(self) -> Path:
        return self.root / "index"

    def dataset_path(self, dataset: DatasetId | object) -> Path:
        dataset_id = normalize_dataset_id(dataset)
        return self.datasets_root.joinpath(*dataset_id.parts)

    def data_path(self, dataset: DatasetId | object) -> Path:
        return self.dataset_path(dataset) / "data"

    def live_path(self, dataset: DatasetId | object) -> Path:
        return self.dataset_path(dataset) / "live"

    def tmp_path(self, dataset: DatasetId | object) -> Path:
        return self.dataset_path(dataset) / "tmp"

    def dataset_json_path(self, dataset: DatasetId | object) -> Path:
        return self.dataset_path(dataset) / "dataset.json"

    def alias_path(self, alias: object) -> Path:
        return self.aliases_root / f"{normalize_alias(alias)}.ref"

    def dataset_id_from_path(self, path: str | Path) -> DatasetId:
        relative = Path(path).relative_to(self.datasets_root)
        return DatasetId(".".join(relative.parts))

