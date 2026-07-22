from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from .store import DatasetStore


OutputName = Literal["arrow", "rows", "pandas", "polars"]


class DatasetReader:
    """Read simplified Dataset directories as logical tables."""

    def __init__(self, store: DatasetStore | str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.store = store if isinstance(store, DatasetStore) else DatasetStore(store)

    def scan(self, dataset: object, *, start: datetime | str | None = None,
             end: datetime | str | None = None) -> tuple[Path, ...]:
        root = self.store.data_path(dataset)
        if not root.exists():
            raise FileNotFoundError(f"Dataset has no historical data directory: {root}")
        files = sorted(path for pattern in ("**/*.parquet", "**/*.csv") for path in root.glob(pattern)
                       if not _under_tmp(path, root))
        return tuple(path for path in files if _path_overlaps(path, root, start, end))

    def read(self, dataset: object, *, start: datetime | str | None = None,
             end: datetime | str | None = None,
             instruments: Iterable[object] | None = None,
             columns: Iterable[object] | None = None,
             output: OutputName | str = "arrow",
             time_field: str | None = None):
        files = self.scan(dataset, start=start, end=end)
        table = _read_files(files)
        table = _filter_table(
            table,
            start=start,
            end=end,
            instruments=tuple(str(item) for item in instruments or ()),
            columns=tuple(str(item) for item in columns) if columns is not None else None,
            time_field=time_field,
        )
        return _convert(table, str(output))


def _read_files(paths: tuple[Path, ...]):
    pa, csv, ds = _arrow()
    parquet = [path for path in paths if path.suffix == ".parquet"]
    csv_files = [path for path in paths if path.suffix == ".csv"]
    tables = []
    if parquet:
        tables.append(ds.dataset([str(path) for path in parquet], format="parquet").to_table())
    if csv_files:
        tables.extend(csv.read_csv(path) for path in csv_files)
    if not tables:
        return pa.table({})
    return pa.concat_tables(tables, promote_options="default")


def _filter_table(table, *, start, end, instruments: tuple[str, ...],
                  columns: tuple[str, ...] | None, time_field: str | None):
    import pyarrow.compute as pc

    selected_time = time_field or _first_present(table.column_names, ("event_time", "timestamp", "period_start", "available_time"))
    mask = None
    if selected_time is not None and start is not None:
        value = pc.greater_equal(table[selected_time], _scalar(table[selected_time].type, start))
        mask = value
    if selected_time is not None and end is not None:
        value = pc.less(table[selected_time], _scalar(table[selected_time].type, end))
        mask = value if mask is None else pc.and_(mask, value)
    instrument_field = _first_present(table.column_names, ("instrument_id", "instrument", "symbol"))
    if instruments and instrument_field is not None:
        value = pc.is_in(table[instrument_field], value_set=_arrow()[0].array(instruments))
        mask = value if mask is None else pc.and_(mask, value)
    if mask is not None:
        table = table.filter(mask)
    if columns is not None:
        missing = sorted(set(columns) - set(table.column_names))
        if missing:
            raise KeyError(f"columns not found: {', '.join(missing)}")
        table = table.select(list(columns))
    return table


def _path_overlaps(path: Path, root: Path, start, end) -> bool:
    bounds = _partition_bounds(path, root)
    if bounds is None:
        return True
    lower, upper = bounds
    start_value, end_value = _datetime(start), _datetime(end)
    return (start_value is None or upper > start_value) and (end_value is None or lower < end_value)


def _partition_bounds(path: Path, root: Path) -> tuple[datetime, datetime] | None:
    from datetime import timedelta

    parts = {key: value for part in path.relative_to(root).parts[:-1]
             if "=" in part for key, value in (part.split("=", 1),)}
    try:
        if "event_day" in parts:
            lower = datetime.fromisoformat(parts["event_day"]).replace(tzinfo=timezone.utc)
            if "event_hour" in parts:
                lower = lower + timedelta(hours=int(parts["event_hour"]))
                return lower, lower + timedelta(hours=1)
            return lower, lower + timedelta(days=1)
        if "event_month" in parts:
            year, month = (int(item) for item in parts["event_month"].split("-", 1))
            lower = datetime(year, month, 1, tzinfo=timezone.utc)
            upper = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)
            return lower, upper
        if "event_year" in parts:
            year = int(parts["event_year"])
            return datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return None


def _first_present(names: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    existing = set(names)
    return next((name for name in candidates if name in existing), None)


def _scalar(data_type, value):
    import pyarrow as pa

    if pa.types.is_timestamp(data_type):
        return pa.scalar(_datetime(value), type=data_type)
    return pa.scalar(str(value), type=data_type)


def _datetime(value):
    if value is None or isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result is None:
        return None
    if result.tzinfo is None:
        raise ValueError("dataset time filters must be timezone-aware")
    return result.astimezone(timezone.utc)


def _under_tmp(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part == "tmp" or part.startswith(".tmp") for part in parts)


def _convert(table, output: str):
    if output == "arrow":
        return table
    if output == "rows":
        return table.to_pylist()
    if output == "pandas":
        return table.to_pandas()
    if output == "polars":
        try:
            import polars as pl
        except ImportError as error:
            raise RuntimeError("Polars output requires the 'query' optional dependency") from error
        return pl.from_arrow(table)
    raise ValueError(f"unsupported output: {output}")


def _arrow():
    try:
        import pyarrow as pa
        import pyarrow.csv as csv
        import pyarrow.dataset as ds
    except ImportError as error:
        raise RuntimeError("Dataset reads require the 'data' optional dependency") from error
    return pa, csv, ds
