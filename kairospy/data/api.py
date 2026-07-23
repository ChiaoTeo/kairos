from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from .live.services import LiveDataService
from .protocols import HistoricalDataRequest
from .storage.metadata import DatasetMetadataInference
from .storage.reader import DatasetReader
from .storage.store import DatasetStore
from .storage.writer import DatasetWriter
from .streams import DataStreamResolver


class DataApi:
    """Small code-facing API for the simplified Dataset store."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)
        self.store = DatasetStore(self.root)
        self.reader = DatasetReader(self.store)
        self.writer = DatasetWriter(self.store)
        self.streams = DataStreamResolver(self.store)
        self.historical = HistoricalDataService(self.root)
        self.live_service = LiveDataService(self.root)

    def read(self, dataset: object, **query: object):
        ref = self.resolve_stream(dataset)
        return self.reader.read(ref.dataset_id, **query)

    def read_many(self, streams: Iterable[object], **query: object) -> dict[str, object]:
        return {
            str(ref.stream_id): self.reader.read(ref.dataset_id, **query)
            for ref in (self.resolve_stream(stream) for stream in streams)
        }

    def read_pattern(self, pattern: object, **query: object) -> dict[str, object]:
        return {
            str(ref.stream_id): self.reader.read(ref.dataset_id, **query)
            for ref in self.streams.match(pattern)
        }

    def live(self, dataset: object, *, view: str = "default") -> Path:
        ref = self.resolve_stream(dataset)
        return self.store.live_path(ref.dataset_id) / view

    def resolve_stream(self, stream_or_dataset: object):
        return self.streams.resolve(stream_or_dataset)

    def use(self, product: str, *, instruments: Iterable[object] = (), **selector: object) -> dict[str, object]:
        raise RuntimeError("Data Product use is owned by surface/integrations, not DataApi")

    def connect(self, product: str, *, instruments: Iterable[object] = (), **selector: object) -> dict[str, object]:
        raise RuntimeError("Data Product connect is owned by surface/integrations, not DataApi")

    def alias(self, dataset: object, alias: object) -> Path:
        return self.store.alias(dataset, alias)

    def append(self, dataset: object, frame: object, **kwargs: object) -> tuple[Path, ...]:
        ref = self.resolve_stream(dataset)
        return self.writer.append(ref.dataset_id, frame, **kwargs)

    def add(self, args) -> dict[str, object]:
        return self.historical.add(args)

    def upsert(self, dataset: object, frame: object, *, key: Iterable[str], **kwargs: object) -> tuple[Path, ...]:
        ref = self.resolve_stream(dataset)
        return self.writer.upsert(ref.dataset_id, frame, key=key, **kwargs)

    def delete_data(self, stream: object, **kwargs: object) -> dict[str, object]:
        ref = self.resolve_stream(stream)
        result = self.store.delete_data(ref.dataset_id, **kwargs)
        return {
            "stream": str(ref.stream_id),
            "dataset": str(ref.dataset_id),
            **result,
        }

    def replace_window(self, stream: object, frame: object, **kwargs: object) -> dict[str, object]:
        ref = self.resolve_stream(stream)
        result = self.store.replace_window(ref.dataset_id, frame, **kwargs)
        return {
            "stream": str(ref.stream_id),
            "dataset": str(ref.dataset_id),
            **result,
        }


def _service_args(product: str, *, instruments: Iterable[object], **selector: object) -> SimpleNamespace:
    values = dict(selector)
    values.setdefault("dry_run", False)
    values.setdefault("for_use", None)
    values.setdefault("as_dataset", None)
    values["key"] = product
    values["source"] = product
    values["instrument"] = [str(item) for item in instruments]
    return SimpleNamespace(**values)


class HistoricalDataService:
    """User-facing historical Data service for file and protocol onboarding."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def add(self, args) -> dict[str, object]:
        request = _with_lake_root(args, self.root)
        source = _materialize_historical_protocol(request) if _is_historical_protocol_add(request) else Path(request.source)
        dataset_id = str(request.name)
        if not _is_historical_protocol_add(request):
            _validate_data_add_file_source(source, dataset_id)
        metadata = DatasetMetadataInference().infer_file(
            source,
            dataset_id=dataset_id,
            time_field=getattr(request, "time", None),
            source_kind="user_defined",
        )
        store = DatasetStore(self.root)
        store.ensure_dataset(dataset_id, metadata={
            "primary_time": metadata.primary_time,
            "fields": list(metadata.field_names),
            "source": metadata.source_summary or {},
        })
        DatasetWriter(store).append(dataset_id, source)
        return {
            "product": "data",
            "operation": "add",
            "dataset": dataset_id,
            "time": metadata.primary_time,
            "fields": list(metadata.field_names),
            "source_kind": metadata.source_kind,
            "historical": {
                "status": "ready",
                "ready_for": ["read"],
                "blocked_for": [],
                "issues": [],
            },
            "live": {
                "status": "not_configured",
                "ready_for": [],
                "blocked_for": ["shadow", "paper", "live"],
                "issues": [],
            },
        }


def _with_lake_root(args, root: Path):
    try:
        return replace(args, lake_root=root)
    except TypeError:
        setattr(args, "lake_root", root)
        return args


def _validate_data_add_file_source(source: Path, dataset_id: str) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if not source.is_file():
        raise ValueError(f"Data add source for {dataset_id!r} is not a file: {source}")
    if source.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError(f"Data add supports CSV and Parquet files, got: {source}")


def _is_historical_protocol_add(args) -> bool:
    protocol = getattr(args, "protocol", None)
    source = Path(args.source)
    return protocol == "historical" or protocol is None and source.suffix == ".py"


def _materialize_historical_protocol(args) -> Path:
    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(source)
    module_hash = sha256(source.read_bytes()).hexdigest()
    module = _load_user_module(source, f"kairospy_user_historical_data_{module_hash[:12]}")
    protocol = _historical_protocol_object(module)
    request = HistoricalDataRequest(
        dataset_id=str(args.name),
        start=_optional_datetime(getattr(args, "start", None)),
        end=_optional_datetime(getattr(args, "end", None)),
        instruments=tuple(str(item) for item in getattr(args, "instrument", ()) or ()),
    )
    rows = _protocol_rows(protocol.load(request), "HistoricalDataProtocol.load")
    target = (
        Path(args.lake_root)
        / "source"
        / "user_defined"
        / "historical_protocol"
        / str(args.name).replace(".", "/")
        / module_hash[:12]
        / "rows.csv"
    )
    _write_protocol_rows_csv(target, rows)
    return target


def _load_user_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Python module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _historical_protocol_object(module):
    for name in ("PROTOCOL", "protocol", "SOURCE", "source", "ADAPTER", "adapter"):
        protocol = getattr(module, name, None)
        if protocol is not None and hasattr(protocol, "load") and callable(protocol.load):
            return protocol
    factory = getattr(module, "get_protocol", None)
    if callable(factory):
        protocol = factory()
        if hasattr(protocol, "load") and callable(protocol.load):
            return protocol
    legacy_factory = getattr(module, "get_adapter", None)
    if callable(legacy_factory):
        protocol = legacy_factory()
        if hasattr(protocol, "load") and callable(protocol.load):
            return protocol
    load = getattr(module, "load", None)
    if callable(load):
        class _FunctionProtocol:
            def load(self, request):
                return load(request)
        return _FunctionProtocol()
    raise ValueError("historical protocol module must define load(request), PROTOCOL.load(request), or get_protocol().load(request)")


def _protocol_rows(value: object, label: str) -> list[dict[str, object]]:
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        value = value.to_dict(orient="records")
    rows = list(value or [])
    if not rows:
        raise ValueError(f"{label} returned no rows")
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{label} row {index} must be a mapping")
        result.append({str(key): _protocol_cell(value) for key, value in row.items()})
    return result


def _protocol_cell(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _write_protocol_rows_csv(target: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    seen = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
