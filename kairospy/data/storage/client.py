from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Literal

from kairospy.identity import InstrumentId
from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from kairospy.data.contracts import DatasetLike, FieldLike, OutputFormat
from .reader import DatasetReader
from .store import DatasetStore


class DataUnavailableError(RuntimeError):
    """Raised when a Dataset path exists but has no readable historical data."""


@dataclass(frozen=True, slots=True)
class DataQuery:
    """Lazy query over one Dataset.

    The query resolves directly against the Dataset Store at collection time.
    It has no release, workspace, backtest, or production contract.
    """

    client: "DatasetClient"
    dataset: DatasetLike
    start: datetime | str | None = None
    end: datetime | str | None = None
    instruments: tuple[str | InstrumentId, ...] = ()
    columns: tuple[str, ...] | None = None
    time_field: str | None = None

    def collect(self, output: OutputFormat | str = OutputFormat.ARROW):
        return self.client.read(
            self.dataset,
            start=self.start,
            end=self.end,
            instruments=self.instruments,
            columns=self.columns,
            output=output,
            time_field=self.time_field,
        )

    def stream(self, *, batch_size: int = 65536) -> Iterator[object]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        table = self.collect(OutputFormat.ARROW)
        yield from table.to_batches(max_chunksize=batch_size)

    def explain(self) -> dict[str, object]:
        metadata = self.client.metadata(self.dataset)
        files = self.client.reader.scan(self.dataset, start=self.start, end=self.end)
        return {
            "dataset": str(self.client.store.resolve(self.dataset)),
            "physical_root": str(self.client.store.dataset_path(self.dataset)),
            "data_root": str(self.client.store.data_path(self.dataset)),
            "start": str(self.start) if self.start is not None else None,
            "end": str(self.end) if self.end is not None else None,
            "boundary": "[start,end)",
            "time_field": self.time_field or metadata.get("primary_time"),
            "columns": list(self.columns) if self.columns is not None else None,
            "files": [str(path) for path in files],
            "file_count": len(files),
        }


class DatasetClient:
    """Consumption-side API for Dataset Store data.

    DatasetClient knows where Datasets are stored and how to read them.  It does
    not own data governance, workspace state, backtest mode, production mode,
    publication, releases, or immutable version selection.
    """

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)
        self.store = DatasetStore(self.root)
        self.reader = DatasetReader(self.store)

    def query(self, dataset: DatasetLike, *, start: datetime | str | None = None,
              end: datetime | str | None = None,
              instruments: Iterable[str | InstrumentId] | None = None,
              columns: Iterable[FieldLike] | None = None,
              time_field: str | None = None) -> DataQuery:
        return DataQuery(
            self,
            dataset,
            start=start,
            end=end,
            instruments=tuple(instruments or ()),
            columns=tuple(str(item) for item in columns) if columns is not None else None,
            time_field=time_field,
        )

    def read(self, dataset: DatasetLike, *, start: datetime | str | None = None,
             end: datetime | str | None = None,
             instruments: Iterable[str | InstrumentId] | None = None,
             columns: Iterable[FieldLike] | None = None,
             output: OutputFormat | str = OutputFormat.ARROW,
             time_field: str | None = None):
        return self.reader.read(
            dataset,
            start=start,
            end=end,
            instruments=instruments,
            columns=tuple(str(item) for item in columns) if columns is not None else None,
            output=_output_name(output),
            time_field=time_field,
        )

    def live(self, dataset: DatasetLike, *, view: str = "default") -> Path:
        return self.store.live_path(dataset) / view

    def alias(self, dataset: DatasetLike, alias: str) -> Path:
        return self.store.alias(dataset, alias)

    def list(self) -> list[dict[str, object]]:
        return [
            self.metadata(dataset)
            for dataset in self.store.list_datasets()
        ]

    def metadata(self, dataset: DatasetLike) -> dict[str, object]:
        import json

        dataset_id = self.store.resolve(dataset)
        root = self.store.dataset_path(dataset_id)
        if not root.exists():
            raise KeyError(f"unknown Dataset: {dataset_id}")
        metadata_path = self.store.layout.dataset_json_path(dataset_id)
        payload: dict[str, object] = {}
        if metadata_path.exists():
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                payload.update(value)
        data_files = sorted(
            path for pattern in ("**/*.parquet", "**/*.csv")
            for path in self.store.data_path(dataset_id).glob(pattern)
            if path.is_file()
        )
        live_states = sorted(self.store.live_path(dataset_id).glob("*/state.json"))
        return {
            "dataset": str(dataset_id),
            "path": str(root),
            "primary_time": payload.get("primary_time"),
            "fields": list(payload.get("fields") or ()),
            "source": payload.get("source") or {},
            "data_product": payload.get("data_product"),
            "provider": payload.get("provider"),
            "venue": payload.get("venue"),
            "historical": {
                "configured": bool(data_files),
                "data_root": str(self.store.data_path(dataset_id)),
                "file_count": len(data_files),
                "files": [str(path) for path in data_files],
            },
            "live": {
                "configured": bool(live_states),
                "live_root": str(self.store.live_path(dataset_id)),
                "views": [path.parent.name for path in live_states],
            },
        }

    def load_rows(self, dataset: DatasetLike, **kwargs) -> list[dict[str, object]]:
        return self.read(dataset, output=OutputFormat.ROWS, **kwargs)

    def iter_rows(self, dataset: DatasetLike, **kwargs):
        batch_size = int(kwargs.pop("batch_size", 65536))
        for batch in self.query(dataset, **kwargs).stream(batch_size=batch_size):
            yield from batch.to_pylist()

    def sql(self, query: str, *, datasets: dict[str, str],
            output: Literal["arrow", "pandas", "rows"] = "arrow"):
        """Run DuckDB SQL with Dataset Store data exposed as Arrow views."""
        try:
            import duckdb
        except ImportError as error:
            raise RuntimeError("SQL dataset queries require the 'query' optional dependency") from error
        connection = duckdb.connect(database=":memory:")
        try:
            for view_name, dataset in datasets.items():
                if not view_name.replace("_", "").isalnum():
                    raise ValueError(f"unsafe SQL view name: {view_name!r}")
                connection.register(view_name, self.read(dataset, output=OutputFormat.ARROW))
            table = connection.execute(query).to_arrow_table()
            if output == "arrow":
                return table
            if output == "pandas":
                return table.to_pandas()
            if output == "rows":
                return table.to_pylist()
            raise ValueError(f"unsupported SQL output: {output}")
        finally:
            connection.close()


def _output_name(output: OutputFormat | str) -> str:
    return output.value if isinstance(output, OutputFormat) else str(output)
