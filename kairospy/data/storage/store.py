from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
from typing import Iterable

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from kairospy.data.ids import DatasetId, normalize_alias, normalize_dataset_id
from kairospy.data.layout import DatasetLayout


class DatasetStore:
    """Filesystem-backed Dataset store.

    The file tree is the source of truth.  Optional JSON files and SQLite
    indexes can aid discovery, but they are not required for reads.
    """

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)
        self.layout = DatasetLayout(self.root)

    def resolve(self, dataset_or_alias: object) -> DatasetId:
        name = str(dataset_or_alias).strip()
        alias_path = self.layout.alias_path(name)
        if alias_path.exists():
            return normalize_dataset_id(alias_path.read_text(encoding="utf-8").strip())
        return normalize_dataset_id(dataset_or_alias)

    def dataset_path(self, dataset: object) -> Path:
        return self.layout.dataset_path(self.resolve(dataset))

    def data_path(self, dataset: object) -> Path:
        return self.layout.data_path(self.resolve(dataset))

    def live_path(self, dataset: object) -> Path:
        return self.layout.live_path(self.resolve(dataset))

    def tmp_path(self, dataset: object) -> Path:
        return self.layout.tmp_path(self.resolve(dataset))

    def ensure_dataset(self, dataset: object, *, metadata: dict[str, object] | None = None) -> Path:
        dataset_id = self.resolve(dataset)
        path = self.layout.dataset_path(dataset_id)
        path.mkdir(parents=True, exist_ok=True)
        if metadata is not None:
            payload = {"dataset": str(dataset_id), **metadata}
            self.layout.dataset_json_path(dataset_id).write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        return path

    def alias(self, dataset: object, alias: object) -> Path:
        dataset_id = normalize_dataset_id(dataset)
        alias_name = normalize_alias(alias)
        path = self.layout.alias_path(alias_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{dataset_id}\n", encoding="utf-8")
        return path

    def list_datasets(self) -> tuple[DatasetId, ...]:
        root = self.layout.datasets_root
        if not root.exists():
            return ()
        candidates: set[Path] = set()
        for path in root.rglob("dataset.json"):
            candidates.add(path.parent)
        for directory_name in ("data", "live"):
            for path in root.rglob(directory_name):
                if path.is_dir():
                    candidates.add(path.parent)
        values = []
        for path in candidates:
            try:
                values.append(self.layout.dataset_id_from_path(path))
            except ValueError:
                continue
        return tuple(sorted(values, key=str))

    def aliases(self) -> dict[str, str]:
        root = self.layout.aliases_root
        if not root.exists():
            return {}
        result = {}
        for path in sorted(root.glob("*.ref")):
            result[path.stem] = path.read_text(encoding="utf-8").strip()
        return result

    def clean_tmp(self, dataset: object | None = None) -> tuple[Path, ...]:
        paths: Iterable[Path]
        if dataset is None:
            root = self.layout.datasets_root
            paths = root.rglob("tmp") if root.exists() else ()
        else:
            paths = (self.tmp_path(dataset),)
        removed = []
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
                removed.append(path)
        return tuple(removed)

    def rebuild_index(self) -> Path:
        """Recreate the optional SQLite discovery cache from the file tree."""
        path = self.layout.index_root / "cache.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "create table datasets (dataset text primary key, path text not null, has_data integer not null, has_live integer not null)"
            )
            connection.execute(
                "create table aliases (alias text primary key, dataset text not null)"
            )
            rows = []
            for dataset in self.list_datasets():
                rows.append((
                    str(dataset),
                    str(self.dataset_path(dataset)),
                    int(self.data_path(dataset).exists()),
                    int(self.live_path(dataset).exists()),
                ))
            connection.executemany("insert into datasets values (?, ?, ?, ?)", rows)
            connection.executemany(
                "insert into aliases values (?, ?)",
                sorted(self.aliases().items()),
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def append(self, dataset: object, frame: object, **kwargs: object) -> tuple[Path, ...]:
        from .writer import DatasetWriter

        return DatasetWriter(self).append(dataset, frame, **kwargs)

    def upsert(self, dataset: object, frame: object, *, key: Iterable[str], **kwargs: object) -> tuple[Path, ...]:
        from .writer import DatasetWriter

        return DatasetWriter(self).upsert(dataset, frame, key=key, **kwargs)
