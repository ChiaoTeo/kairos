from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
import shutil
from typing import Iterable
from uuid import uuid4

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from .store import DatasetStore


class DatasetWriter:
    """Write rows into a single Dataset data directory."""

    def __init__(self, store: DatasetStore | str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.store = store if isinstance(store, DatasetStore) else DatasetStore(store)

    def append(self, dataset: object, frame: object, *, partition_by: Iterable[str] | None = None,
               time_field: str | None = None) -> tuple[Path, ...]:
        table = _to_table(frame)
        if table.num_rows == 0:
            self.store.ensure_dataset(dataset)
            return ()
        rows = table.to_pylist()
        dataset_id = self.store.resolve(dataset)
        self.store.ensure_dataset(dataset_id)
        written = []
        for relative, group in _group_rows(rows, partition_by=partition_by, time_field=time_field).items():
            target = self.store.data_path(dataset_id).joinpath(*relative)
            target.mkdir(parents=True, exist_ok=True)
            path = target / f"part-{uuid4().hex}.parquet"
            _write_rows(path, group)
            written.append(path)
        return tuple(written)

    def upsert(self, dataset: object, frame: object, *, key: Iterable[str],
               partition_by: Iterable[str] | None = None, time_field: str | None = None) -> tuple[Path, ...]:
        key_fields = tuple(str(item) for item in key)
        if not key_fields:
            raise ValueError("upsert key cannot be empty")
        table = _to_table(frame)
        if table.num_rows == 0:
            self.store.ensure_dataset(dataset)
            return ()
        rows = table.to_pylist()
        for field in key_fields:
            if field not in table.column_names:
                raise KeyError(f"upsert key field not found: {field}")
        dataset_id = self.store.resolve(dataset)
        self.store.ensure_dataset(dataset_id)
        written = []
        for relative, new_rows in _group_rows(rows, partition_by=partition_by, time_field=time_field).items():
            leaf = self.store.data_path(dataset_id).joinpath(*relative)
            existing_rows = _read_existing_rows(leaf)
            merged = _merge_rows(existing_rows, new_rows, key_fields, time_field=time_field)
            tmp_leaf = self.store.tmp_path(dataset_id) / f"upsert-{uuid4().hex}" / "data" / Path(*relative)
            tmp_leaf.mkdir(parents=True, exist_ok=True)
            tmp_file = tmp_leaf / "part-00000.parquet"
            _write_rows(tmp_file, merged)
            if leaf.exists():
                shutil.rmtree(leaf) if leaf.is_dir() else leaf.unlink()
            leaf.parent.mkdir(parents=True, exist_ok=True)
            tmp_leaf.rename(leaf)
            _remove_empty_parents(tmp_leaf.parent, stop=self.store.tmp_path(dataset_id))
            written.append(leaf / "part-00000.parquet")
        return tuple(written)

    def compact(self, dataset: object, partitions: Iterable[str | Path] | None = None) -> tuple[Path, ...]:
        dataset_id = self.store.resolve(dataset)
        data_root = self.store.data_path(dataset_id)
        if not data_root.exists():
            return ()
        if partitions is None:
            leaves = sorted({path.parent for path in data_root.rglob("*.parquet")})
        else:
            leaves = [data_root / Path(partition) for partition in partitions]
        written = []
        for leaf in leaves:
            rows = _read_existing_rows(leaf)
            if not rows:
                continue
            tmp_leaf = self.store.tmp_path(dataset_id) / f"compact-{uuid4().hex}" / leaf.relative_to(data_root)
            tmp_leaf.mkdir(parents=True, exist_ok=True)
            _write_rows(tmp_leaf / "part-00000.parquet", rows)
            shutil.rmtree(leaf)
            tmp_leaf.rename(leaf)
            written.append(leaf / "part-00000.parquet")
        return tuple(written)

    def clean_tmp(self, dataset: object | None = None) -> tuple[Path, ...]:
        return self.store.clean_tmp(dataset)


def _to_table(frame):
    pa = _arrow()[0]
    if isinstance(frame, pa.Table):
        return frame
    if isinstance(frame, pa.RecordBatch):
        return pa.Table.from_batches([frame])
    if isinstance(frame, (str, Path)):
        path = Path(frame)
        if path.suffix.lower() == ".parquet":
            return _arrow()[2].dataset([str(path)], format="parquet").to_table()
        if path.suffix.lower() == ".csv":
            import pyarrow.csv as csv

            return csv.read_csv(path)
        raise ValueError(f"unsupported dataset file format: {path.suffix}")
    if hasattr(frame, "to_arrow"):
        return frame.to_arrow()
    if hasattr(frame, "__arrow_c_stream__"):
        return pa.table(frame)
    if hasattr(frame, "to_dict") and frame.__class__.__module__.startswith("pandas"):
        return pa.Table.from_pandas(frame, preserve_index=False)
    if isinstance(frame, dict):
        return pa.table(frame)
    return pa.Table.from_pylist(list(frame))


def _group_rows(rows: list[dict[str, object]], *, partition_by: Iterable[str] | None,
                time_field: str | None) -> dict[tuple[str, ...], list[dict[str, object]]]:
    fields = tuple(str(item) for item in partition_by or ())
    result: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        relative = tuple(f"{field}={_partition_value(row, field, time_field)}" for field in fields)
        result[relative].append(row)
    return result


def _partition_value(row: dict[str, object], field: str, time_field: str | None) -> str:
    if field in row and row[field] is not None:
        return str(row[field])
    timestamp = _row_time(row, time_field)
    if field == "event_year":
        return f"{timestamp.year:04d}"
    if field == "event_month":
        return f"{timestamp.year:04d}-{timestamp.month:02d}"
    if field in {"event_day", "event_date"}:
        return timestamp.date().isoformat()
    if field == "event_hour":
        return f"{timestamp.hour:02d}"
    if field == "instrument_bucket":
        instrument = _row_instrument(row)
        return sha1(instrument.encode("utf-8")).hexdigest()[:2]
    raise KeyError(f"cannot derive partition field {field!r}")


def _row_time(row: dict[str, object], time_field: str | None) -> datetime:
    field = time_field or next((name for name in ("event_time", "timestamp", "period_start", "available_time") if name in row), None)
    if field is None:
        raise KeyError("time field is required to derive time partitions")
    value = row[field]
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("dataset timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _row_instrument(row: dict[str, object]) -> str:
    for field in ("instrument_id", "instrument", "symbol"):
        value = row.get(field)
        if value is not None:
            return str(value)
    raise KeyError("instrument field is required to derive instrument_bucket")


def _read_existing_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    pa, _, ds = _arrow()
    files = [str(item) for item in sorted(path.rglob("*.parquet"))]
    if not files:
        return []
    return ds.dataset(files, format="parquet").to_table().to_pylist()


def _merge_rows(existing: list[dict[str, object]], new: list[dict[str, object]],
                key_fields: tuple[str, ...], *, time_field: str | None) -> list[dict[str, object]]:
    merged = {_row_key(row, key_fields): row for row in existing}
    for row in new:
        merged[_row_key(row, key_fields)] = row
    rows = list(merged.values())
    sort_field = time_field or next((field for field in ("event_time", "timestamp", "period_start", "available_time") if any(field in row for row in rows)), None)
    if sort_field is not None:
        rows.sort(key=lambda row: tuple(str(row.get(field, "")) for field in (sort_field, *key_fields)))
    return rows


def _row_key(row: dict[str, object], fields: tuple[str, ...]) -> tuple[object, ...]:
    try:
        return tuple(row[field] for field in fields)
    except KeyError as error:
        raise KeyError(f"upsert row is missing key field: {error.args[0]}") from error


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    pa, pq, _ = _arrow()
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    stop = stop.resolve()
    current = path
    while current.exists() and current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _arrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pyarrow.dataset as ds
    except ImportError as error:
        raise RuntimeError("Dataset writes require the 'data' optional dependency") from error
    return pa, pq, ds
