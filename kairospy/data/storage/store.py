from __future__ import annotations

from datetime import datetime, timezone
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

    def delete_data(
        self,
        dataset: object,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        time_field: str | None = None,
        all_data: bool = False,
    ) -> dict[str, object]:
        if not all_data and (start is None or end is None):
            raise ValueError("delete_data requires --start and --end unless all_data=True")
        dataset_id = self.resolve(dataset)
        data_root = self.data_path(dataset_id)
        if not data_root.exists():
            return {"dataset": str(dataset_id), "deleted_rows": 0, "remaining_rows": 0, "removed_path": None}
        if all_data:
            shutil.rmtree(data_root)
            return {"dataset": str(dataset_id), "deleted_rows": None, "remaining_rows": 0, "removed_path": str(data_root)}

        from .reader import DatasetReader
        from .writer import DatasetWriter

        rows = DatasetReader(self).read(dataset_id, output="rows")
        kept, deleted = [], 0
        lower, upper = _datetime(start), _datetime(end)
        assert lower is not None and upper is not None
        for row in rows:
            value = _row_time(row, time_field)
            if value is not None and lower <= value < upper:
                deleted += 1
            else:
                kept.append(row)
        shutil.rmtree(data_root)
        written = ()
        if kept:
            written = DatasetWriter(self).append(
                dataset_id,
                kept,
                partition_by=("event_day",),
                time_field=time_field,
            )
        return {
            "dataset": str(dataset_id),
            "deleted_rows": deleted,
            "remaining_rows": len(kept),
            "removed_path": str(data_root) if not kept else None,
            "written": [str(path) for path in written],
        }

    def replace_window(
        self,
        dataset: object,
        frame: object,
        *,
        start: datetime | str,
        end: datetime | str,
        time_field: str | None = None,
        partition_by: Iterable[str] | None = None,
    ) -> dict[str, object]:
        dataset_id = self.resolve(dataset)
        lower, upper = _datetime(start), _datetime(end)
        assert lower is not None and upper is not None
        from .reader import DatasetReader
        from .writer import DatasetWriter, _to_table

        table = _to_table(frame)
        replacement = table.to_pylist()
        for row in replacement:
            value = _row_time(row, time_field)
            if value is None or not lower <= value < upper:
                raise ValueError("replacement rows must all fall inside the replace window")
        data_root = self.data_path(dataset_id)
        existing = DatasetReader(self).read(dataset_id, output="rows") if data_root.exists() else []
        kept = [
            row for row in existing
            if (value := _row_time(row, time_field)) is None or not lower <= value < upper
        ]
        replaced_rows = len(existing) - len(kept)
        if data_root.exists():
            shutil.rmtree(data_root)
        combined = sorted([*kept, *replacement], key=lambda row: _row_time(row, time_field) or lower)
        written = ()
        self.ensure_dataset(dataset_id)
        if combined:
            written = DatasetWriter(self).append(
                dataset_id,
                combined,
                partition_by=partition_by or ("event_day",),
                time_field=time_field,
            )
        return {
            "dataset": str(dataset_id),
            "replaced_rows": replaced_rows,
            "inserted_rows": len(replacement),
            "remaining_rows": len(combined),
            "written": [str(path) for path in written],
        }

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


def _row_time(row: dict[str, object], time_field: str | None) -> datetime | None:
    field = time_field or next((name for name in ("event_time", "timestamp", "period_start", "available_time") if name in row), None)
    if field is None:
        return None
    return _datetime(row.get(field))


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("dataset time filters must be timezone-aware")
    return result.astimezone(timezone.utc)
