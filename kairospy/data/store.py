from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import json
from pathlib import Path
from typing import Iterable, Literal, Mapping
from pandas import DataFrame

from .ids import DataId, normalize_alias, normalize_data_id
from .query import DataQuery, OutputFormat


@dataclass(frozen=True, slots=True)
class DataStore:
    root: Path
    storage_format: str

    def __init__(self, root: str | Path = ".kairos/data", *, storage_format: Literal["parquet", "jsonl"] = "parquet") -> None:
        if storage_format not in {"parquet", "jsonl"}:
            raise ValueError("storage_format must be parquet or jsonl")
        object.__setattr__(self, "root", Path(root).expanduser())
        object.__setattr__(self, "storage_format", storage_format)

    @property
    def datasets_root(self) -> Path:
        return self.root / "datasets"

    @property
    def aliases_root(self) -> Path:
        return self.root / "aliases"

    def path(self, dataset: object) -> Path:
        data_id = self.resolve(dataset)
        return self.datasets_root.joinpath(*data_id.parts)

    def data_path(self, dataset: object) -> Path:
        return self.path(dataset) / f"data.{self.storage_format}"

    def resolve(self, dataset_or_alias: object) -> DataId:
        name = str(dataset_or_alias).strip()
        alias_path = self.aliases_root / f"{normalize_alias(name)}.ref"
        if alias_path.exists():
            return normalize_data_id(alias_path.read_text(encoding="utf-8").strip())
        return normalize_data_id(dataset_or_alias)

    def alias(self, dataset: object, alias: object) -> Path:
        data_id = self.resolve(dataset)
        alias_name = normalize_alias(alias)
        path = self.aliases_root / f"{alias_name}.ref"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{data_id}\n", encoding="utf-8")
        return path

    def list(self) -> tuple[DataId, ...]:
        if not self.datasets_root.exists():
            return ()
        values: list[DataId] = []
        for path in (*self.datasets_root.rglob("data.parquet"), *self.datasets_root.rglob("data.jsonl")):
            try:
                values.append(DataId(".".join(path.parent.relative_to(self.datasets_root).parts)))
            except ValueError:
                continue
        return tuple(sorted(set(values), key=str))

    def aliases(self) -> dict[str, str]:
        if not self.aliases_root.exists():
            return {}
        return {
            path.stem: path.read_text(encoding="utf-8").strip()
            for path in sorted(self.aliases_root.glob("*.ref"))
        }

    def write(self, dataset: object, rows: Iterable[Mapping[str, object]], *, mode: str = "append") -> Path:
        if mode not in {"append", "replace"}:
            raise ValueError("write mode must be append or replace")
        data_id = self.resolve(dataset)
        incoming = [_normalize_row(row) for row in rows]
        if not incoming:
            raise ValueError("write requires at least one row")
        existing = [] if mode == "replace" else self._read_all(data_id)
        combined = sorted([*existing, *incoming], key=lambda row: row["time"])
        path = self.data_path(data_id)
        _write_rows(path, combined, self.storage_format)
        return path

    def read(
        self,
        dataset: object,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        output: str | OutputFormat = OutputFormat.ROWS,
    ) -> "DataFrame":
        return self.read_frame(
            dataset,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
            output=output,
        )

    def read_frame(
        self,
        dataset: object,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        output: str | OutputFormat = OutputFormat.ROWS,
    ) -> "DataFrame":
        return _dataframe(self.read_rows(dataset, start=start, end=end, columns=columns, limit=limit, output=output))

    def read_rows(
        self,
        dataset: object,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        output: str | OutputFormat = OutputFormat.ROWS,
    ) -> list[dict[str, object]]:
        query = DataQuery.from_values(start=start, end=end, columns=columns, limit=limit, output=output)
        if query.output is not OutputFormat.ROWS:
            raise ValueError("only rows output is supported")
        lower = _optional_time(query.start)
        upper = _optional_time(query.end)
        rows = []
        for row in self._read_all(self.resolve(dataset)):
            row_time = _parse_time(row["time"])
            if lower is not None and row_time < lower:
                continue
            if upper is not None and row_time >= upper:
                continue
            rows.append(_select_columns(row, query.columns))
            if query.limit is not None and len(rows) >= query.limit:
                break
        return rows

    def read_many(self, datasets: Iterable[object], **query: object) -> dict[str, "DataFrame"]:
        return {str(item): self.read(item, **query) for item in datasets}

    def read_pattern(self, pattern: object, **query: object) -> dict[str, "DataFrame"]:
        text = str(pattern)
        names = {str(item) for item in self.list() if fnmatchcase(str(item), text)}
        names.update(alias for alias in self.aliases() if fnmatchcase(alias, text))
        return {name: self.read(name, **query) for name in sorted(names)}

    def delete_window(self, dataset: object, *, start: object, end: object) -> dict[str, object]:
        data_id = self.resolve(dataset)
        lower = _parse_time(start)
        upper = _parse_time(end)
        if lower >= upper:
            raise ValueError("delete_window requires start before end")
        kept: list[dict[str, object]] = []
        deleted = 0
        for row in self._read_all(data_id):
            row_time = _parse_time(row["time"])
            if lower <= row_time < upper:
                deleted += 1
            else:
                kept.append(row)
        _write_rows(self.data_path(data_id), kept, self.storage_format)
        return {"dataset": str(data_id), "deleted_rows": deleted, "remaining_rows": len(kept)}

    def replace_window(
        self,
        dataset: object,
        rows: Iterable[Mapping[str, object]],
        *,
        start: object,
        end: object,
    ) -> dict[str, object]:
        data_id = self.resolve(dataset)
        lower = _parse_time(start)
        upper = _parse_time(end)
        if lower >= upper:
            raise ValueError("replace_window requires start before end")
        incoming = [_normalize_row(row) for row in rows]
        for row in incoming:
            row_time = _parse_time(row["time"])
            if not lower <= row_time < upper:
                raise ValueError("replacement rows must all fall inside the replace window")
        kept = []
        replaced = 0
        for row in self._read_all(data_id):
            row_time = _parse_time(row["time"])
            if lower <= row_time < upper:
                replaced += 1
            else:
                kept.append(row)
        combined = sorted([*kept, *incoming], key=lambda row: row["time"])
        _write_rows(self.data_path(data_id), combined, self.storage_format)
        return {
            "dataset": str(data_id),
            "replaced_rows": replaced,
            "inserted_rows": len(incoming),
            "remaining_rows": len(combined),
        }

    def _read_all(self, dataset: object) -> list[dict[str, object]]:
        path = self._existing_data_path(dataset)
        if path is None:
            return []
        return [_normalize_row(row) for row in _read_rows(path)]

    def _existing_data_path(self, dataset: object) -> Path | None:
        preferred = self.data_path(dataset)
        if preferred.exists():
            return preferred
        directory = self.path(dataset)
        for name in ("data.parquet", "data.jsonl"):
            path = directory / name
            if path.exists():
                return path
        return None


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]], storage_format: str) -> None:
    if storage_format == "parquet":
        _write_parquet(path, rows)
    elif storage_format == "jsonl":
        _write_jsonl(path, rows)
    else:
        raise ValueError("storage_format must be parquet or jsonl")


def _read_rows(path: Path) -> list[dict[str, object]]:
    if path.suffix == ".parquet":
        return _read_parquet(path)
    if path.suffix == ".jsonl":
        return _read_jsonl(path)
    raise ValueError(f"unsupported data file format: {path}")


def _write_parquet(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Parquet storage requires pyarrow") from error
    values = [dict(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not values:
        if path.exists():
            path.unlink()
        return
    pq.write_table(pa.Table.from_pylist(values), path)


def _read_parquet(path: Path) -> list[dict[str, object]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Parquet storage requires pyarrow") from error
    return [dict(row) for row in pq.read_table(path).to_pylist()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"data row {line_number} in {path} is not an object")
        rows.append(dict(value))
    return rows


def _normalize_row(row: Mapping[str, object]) -> dict[str, object]:
    if "time" not in row:
        raise ValueError("data row requires a time field")
    result = dict(row)
    result["time"] = _parse_time(result["time"]).astimezone(timezone.utc).isoformat()
    return result


def _optional_time(value: object | None) -> datetime | None:
    return None if value is None else _parse_time(value)


def _parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError(f"time must be timezone-aware datetime or ISO-8601 text: {value!r}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"time must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def _select_columns(row: Mapping[str, object], columns: tuple[str, ...]) -> dict[str, object]:
    if not columns:
        return dict(row)
    return {name: row[name] for name in columns if name in row}


def _dataframe(rows: list[dict[str, object]]) -> "DataFrame":
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("DataStore.read requires pandas") from error
    return pd.DataFrame.from_records(rows)
