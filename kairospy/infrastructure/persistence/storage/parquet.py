from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path


def write_parquet_rows(path: str | Path, rows: Iterable[Mapping[str, object]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Parquet storage requires pyarrow") from error
    values = [dict(row) for row in rows]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not values:
        if target.exists():
            target.unlink()
        return
    pq.write_table(pa.Table.from_pylist(values), target)


def read_parquet_rows(path: str | Path) -> list[dict[str, object]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Parquet storage requires pyarrow") from error
    return [dict(row) for row in pq.read_table(Path(path)).to_pylist()]


__all__ = ["read_parquet_rows", "write_parquet_rows"]
