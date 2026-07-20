from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Iterable, Iterator, Literal

from kairos.domain.identity import InstrumentId
from kairos.market_data import MarketEventType, ParquetMarketEventRepository
from kairos.data.market_snapshot_storage import MarketSnapshotStorageDriver

from .catalog import DataCatalog
from .acquisition import (
    AcquisitionEstimate, AcquisitionLimits, AcquisitionPlan, AcquisitionRequest, CoveragePlanner, ProviderRegistry,
)
from .feed import ReplayEventFeed, ReplaySnapshotFeed, replay_spec
from .contracts import (
    AcquirePolicy, DataView, DatasetLike, DataProductDefinition, DatasetStatus, DatasetStorageKind, FieldLike, OutputFormat,
    QualityLevel, RunMode,
)
from .snapshot import StudyInputSnapshot, write_study_snapshot


class DataUnavailableError(RuntimeError):
    def __init__(self, plan: AcquisitionPlan) -> None:
        self.plan = plan
        ranges = ", ".join(f"[{item.start.isoformat()},{item.end.isoformat()})" for item in plan.missing)
        source = ""
        command = ""
        if plan.selected is not None:
            source = f"; selected provider={plan.selected.provider!r}, venue={plan.selected.venue!r}"
            command = (
                f"; next: kairos data acquire --dataset {plan.logical_key} "
                f"--start {plan.requested.start.isoformat()} --end {plan.requested.end.isoformat()} "
                f"--provider {plan.selected.provider}"
                + (f" --venue {plan.selected.venue}" if plan.selected.venue else "")
            )
        super().__init__(f"dataset {plan.logical_key!r} is not locally complete; missing: {ranges or 'unknown'}{source}{command}")


class DataQuery:
    """Lazy, reusable dataset query resolved against one immutable release at collection time."""

    def __init__(self, client: DatasetClient, dataset: DatasetLike, release_id: str, *, version: str | None,
                 start: datetime | str | None, end: datetime | str | None,
                 instruments: Iterable[str | InstrumentId] | None,
                 event_types: Iterable[str | MarketEventType] | None, fields: Iterable[FieldLike] | None,
                 view: DataView) -> None:
        self.client, self.dataset, self.release_id, self.version = client, dataset, release_id, version
        self.start, self.end = start, end
        self.instruments = tuple(instruments or ())
        self.event_types = tuple(event_types or ())
        self.fields = tuple(str(item) for item in fields) if fields is not None else None
        self.view = view

    def collect(self, output: OutputFormat | str = OutputFormat.ARROW):
        return self.client._execute(self, OutputFormat(output))

    def stream(self, *, batch_size: int = 65536) -> Iterator[object]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        yield from self.client._stream(self, batch_size)

    def explain(self) -> dict[str, object]:
        release = self.client.resolve(self.release_id)
        root = self.client.root / release.relative_path
        parquet = _parquet_files(root, None, None)
        selected_parquet = _parquet_files(root, self.start, self.end)
        return {
            "logical_name": str(release.product_key), "release_id": release.release_id,
            "release_version": release.release_version, "format": release.format,
            "physical_root": str(root), "start": str(self.start) if self.start is not None else None,
            "end": str(self.end) if self.end is not None else None, "boundary": "[start,end)",
            "time_field": self.client.catalog.product(release.product_key).primary_time,
            "fields": list(self.fields) if self.fields is not None else None,
            "predicate_pushdown": release.format == "parquet" or any(root.glob("**/*.parquet")),
            "partition_pruning": {
                "total_files": len(parquet), "selected_files": len(selected_parquet),
            } if parquet else None,
        }

    def snapshot(self) -> StudyInputSnapshot:
        release = self.client.catalog.release(self.release_id)
        if release.content_hash is None:
            raise ValueError(f"release {release.release_id!r} has no frozen content hash")
        return StudyInputSnapshot(
            str(release.product_key), release.release_id, release.content_hash, release.schema_version,
            release.transform_id, release.transform_version, release.provider, release.venue,
            release.quality_level.value, self.client.catalog.product(release.product_key).source_policy_version,
            self.view.value,
            str(self.start) if self.start is not None else None, str(self.end) if self.end is not None else None,
            "[start,end)", self.fields, tuple(str(item) for item in self.instruments),
            tuple(item.value if isinstance(item, MarketEventType) else str(item) for item in self.event_types),
        )


class DatasetClient:
    """One point-in-time-safe entry point for governed dataset releases.

    Users address datasets by catalog ID/logical alias and never need to know
    whether the physical source is typed Parquet, event Parquet, or a
    MarketReplayDataset.  Arrow is the native return type; pandas/polars are conversion formats.
    """

    def __init__(self, root: str | Path = "data", *, catalog_path: str | Path | None = None,
                 dataset_root: str | Path | None = None, providers: ProviderRegistry | None = None,
                 run_mode: RunMode | str = RunMode.STUDY, acquisition_limits: AcquisitionLimits = AcquisitionLimits()) -> None:
        self.root = Path(root)
        self.catalog = DataCatalog(self.root, catalog_path)
        self.events = ParquetMarketEventRepository(self.root / "canonical" / "market")
        self.dataset_root = Path(dataset_root) if dataset_root is not None else self.root / "curated"
        self.providers = providers or ProviderRegistry()
        self.run_mode = RunMode(run_mode)
        self.acquisition_limits = acquisition_limits

    def resolve(self, dataset: DatasetLike, *, version: str | None = None):
        name = str(dataset.key) if isinstance(dataset, DataProductDefinition) else str(dataset)
        release = self.catalog.resolve(name, version=version)
        if release.status not in {
            DatasetStatus.APPROVED_FOR_RESEARCH, DatasetStatus.APPROVED_FOR_BACKTEST,
            DatasetStatus.APPROVED_FOR_PRODUCTION,
        }:
            raise PermissionError(
                f"dataset release {release.release_id!r} has status {release.status.value!r} and is not approved for governed use"
            )
        return release

    def search(self, **dimensions: str) -> tuple[DataProductDefinition, ...]:
        return self.catalog.search(**dimensions)

    def describe(self, dataset: DatasetLike) -> dict[str, object]:
        product = self.catalog.product(dataset)
        releases = self.catalog.releases(product)
        selected = self.catalog.release(product) if releases else None
        return {
            "logical_key": str(product.key), "title": product.title, "description": product.description,
            "layer": product.layer.value, "dimensions": dict(product.dimensions),
            "primary_time": product.primary_time, "default_view": product.default_view.value,
            "sources": [{"provider": item.provider, "venue": item.venue, "priority": item.priority,
                         "quality_level": item.quality_level.value, "acquisition_modes": list(item.acquisition_modes)}
                        for item in product.sources],
            "selected_release": _release_summary(selected) if selected else None,
            "releases": [_release_summary(item) for item in releases],
        }

    def coverage(self, dataset: DatasetLike, *, provider: str | None = None,
                 venue: str | None = None) -> dict[str, object]:
        release = self.catalog.release(dataset, provider=provider, venue=venue)
        metadata = self.metadata(release.release_id)
        return {"release": _release_summary(release), "coverage": metadata.get("coverage", {})}

    def compare(self, first: DatasetLike, second: DatasetLike) -> dict[str, object]:
        left, right = self.catalog.release(first), self.catalog.release(second)
        left_metadata, right_metadata = self.metadata(left.release_id), self.metadata(right.release_id)
        fields = (
            "product_key", "release_version", "schema_id", "schema_version", "transform_id",
            "transform_version", "content_hash", "provider", "venue", "status", "quality_level",
        )
        identity = {}
        for field in fields:
            left_value, right_value = getattr(left, field), getattr(right, field)
            if hasattr(left_value, "value"): left_value = left_value.value
            if hasattr(right_value, "value"): right_value = right_value.value
            identity[field] = {"first": str(left_value) if left_value is not None else None,
                               "second": str(right_value) if right_value is not None else None,
                               "equal": left_value == right_value}
        return {
            "first": left.release_id, "second": right.release_id, "identity": identity,
            "schema": _document_comparison(left_metadata.get("schema"), right_metadata.get("schema")),
            "schema_compatibility": _schema_compatibility(left_metadata.get("schema"), right_metadata.get("schema")),
            "coverage": _document_comparison(left_metadata.get("coverage"), right_metadata.get("coverage")),
            "quality": _document_comparison(left_metadata.get("quality"), right_metadata.get("quality")),
            "lineage": _document_comparison(left_metadata.get("lineage"), right_metadata.get("lineage")),
        }

    def get(self, dataset: DatasetLike, *, version: str | None = None,
            start: datetime | str | None = None, end: datetime | str | None = None,
            instruments: Iterable[str | InstrumentId] | None = None,
            event_types: Iterable[str | MarketEventType] | None = None,
            fields: Iterable[FieldLike] | None = None,
            view: DataView | str = DataView.RAW_AS_RECEIVED,
            acquire: AcquirePolicy | str = AcquirePolicy.NEVER,
            provider: str | None = None, venue: str | None = None) -> DataQuery:
        policy = AcquirePolicy(acquire)
        if policy is not AcquirePolicy.NEVER:
            if start is None or end is None:
                raise ValueError("data acquisition planning requires start and end")
            plan = self.plan(dataset, start=_datetime(start), end=_datetime(end), provider=provider, venue=venue)
            if policy is AcquirePolicy.PLAN and not plan.complete:
                raise DataUnavailableError(plan)
            if policy in {AcquirePolicy.IF_MISSING, AcquirePolicy.REFRESH} and (not plan.complete or policy is AcquirePolicy.REFRESH):
                self.acquire(plan, instruments=instruments, fields=fields, refresh=policy is AcquirePolicy.REFRESH)
        release = self.catalog.release(dataset, version=version, provider=provider, venue=venue)
        self._require_release_for_mode(release)
        frozen = self.resolve(release.release_id)
        return DataQuery(self, dataset, frozen.release_id, version=version, start=start, end=end, instruments=instruments,
                         event_types=event_types, fields=fields, view=DataView(view))

    def plan(self, dataset: DatasetLike, *, start: datetime, end: datetime,
             provider: str | None = None, venue: str | None = None) -> AcquisitionPlan:
        from dataclasses import replace
        plan = CoveragePlanner(self.catalog, self.metadata).plan(dataset, start, end, provider=provider, venue=venue)
        if plan.selected is None or not self.providers.available(plan.selected.provider, plan.logical_key):
            return plan
        connector = self.providers.get(plan.selected.provider, plan.logical_key)
        request = AcquisitionRequest(plan.logical_key, plan.missing, plan.selected, base_release_id=plan.local_release_id)
        estimate = connector.estimate(request) if hasattr(connector, "estimate") else AcquisitionEstimate(len(plan.missing))
        return replace(plan, connector_available=True, estimate=estimate)

    def acquire(self, plan: AcquisitionPlan, *, instruments=(), fields=(), refresh: bool = False):
        if self.run_mode is RunMode.BACKTEST:
            raise RuntimeError("backtest mode forbids data acquisition; prepare and freeze a release before running")
        if plan.complete and not refresh:
            return self.catalog.release(plan.logical_key)
        if plan.selected is None:
            raise DataUnavailableError(plan)
        missing = (plan.requested,) if refresh else plan.missing
        connector = self.providers.get(plan.selected.provider, plan.logical_key)
        request = AcquisitionRequest(
            plan.logical_key, missing, plan.selected, tuple(str(item) for item in instruments or ()),
            tuple(str(item) for item in fields or ()), None if refresh else plan.local_release_id,
        )
        estimate = connector.estimate(request) if hasattr(connector, "estimate") else AcquisitionEstimate(len(missing))
        self._check_acquisition_limits(request, estimate)
        release = connector.acquire(request)
        if str(release.product_key) != plan.logical_key:
            raise ValueError("provider connector returned a release for a different logical product")
        if release.provider is not None and release.provider != plan.selected.provider:
            raise ValueError("provider connector returned a release with a mismatched provider")
        self.catalog.register_release(release)
        self.catalog.save()
        return release

    def _check_acquisition_limits(self, request: AcquisitionRequest, estimate: AcquisitionEstimate) -> None:
        limits = self.acquisition_limits
        if len(request.missing) > limits.maximum_ranges:
            raise RuntimeError(f"acquisition has {len(request.missing)} ranges; limit is {limits.maximum_ranges}")
        if len(request.instruments) > limits.maximum_instruments:
            raise RuntimeError(f"acquisition has {len(request.instruments)} instruments; limit is {limits.maximum_instruments}")
        if estimate.requests > limits.maximum_requests:
            raise RuntimeError(f"acquisition estimates {estimate.requests} requests; limit is {limits.maximum_requests}")
        if limits.maximum_bytes is not None and estimate.bytes is not None and estimate.bytes > limits.maximum_bytes:
            raise RuntimeError(f"acquisition estimates {estimate.bytes} bytes; limit is {limits.maximum_bytes}")

    def replay(self, dataset: DatasetLike, start: datetime, end: datetime, *,
               version: str | None = None, instruments: Iterable[str | InstrumentId] = (),
               event_types: Iterable[str | MarketEventType] = (),
               view: DataView | str = DataView.RAW_AS_RECEIVED,
               provider: str | None = None, venue: str | None = None) -> ReplayEventFeed:
        release = self.catalog.release(dataset, version=version, provider=provider, venue=venue)
        self._require_release_for_mode(release)
        if release.storage_kind is not DatasetStorageKind.MARKET_EVENTS:
            raise ValueError(f"release {release.release_id!r} is not an event dataset")
        return ReplayEventFeed(self.events, replay_spec(
            release, start, end, instruments=instruments, event_types=event_types, view=view,
        ))

    def metadata(self, dataset: str, *, version: str | None = None) -> dict[str, object]:
        release = self.catalog.release(dataset, version=version)
        release = self.catalog.resolve(release.release_id)
        root = self.root / release.relative_path
        if release.storage_kind is DatasetStorageKind.MARKET_EVENTS:
            return self.events.metadata(release.release_id)
        result: dict[str, object] = {"catalog": _release_metadata(release, self.catalog.product(release.product_key))}
        for name in ("schema", "lineage", "coverage", "quality", "manifest", "capabilities", "collection"):
            path = root / f"{name}.json"
            if path.exists():
                result[name] = json.loads(path.read_text(encoding="utf-8"))
        dataset_json = root / "dataset.json"
        if dataset_json.exists() and "manifest" not in result:
            result["manifest"] = json.loads(dataset_json.read_text(encoding="utf-8")).get("manifest", {})
        return result

    def _execute(self, query: DataQuery, output: OutputFormat):
        release = self.resolve(query.release_id)
        root = self.root / release.relative_path
        if release.storage_kind is DatasetStorageKind.MARKET_EVENTS:
            table = self._load_events(release.release_id, query.start, query.end, query.instruments,
                                      query.event_types, query.view.value)
            if query.fields is not None:
                table = _select(table, query.fields, release.release_id)
        else:
            primary_time = self.catalog.product(release.product_key).primary_time
            table = self._load_files(root, start=query.start, end=query.end, instruments=query.instruments,
                                     columns=query.fields, primary_time=primary_time)
        return _convert(table, output.value)

    def _stream(self, query: DataQuery, batch_size: int):
        release = self.resolve(query.release_id)
        root = self.root / release.relative_path
        if release.storage_kind is DatasetStorageKind.MARKET_EVENTS:
            start, end = self._event_window(release.release_id, query.start, query.end)
            ids = tuple(item if isinstance(item, InstrumentId) else InstrumentId(item) for item in query.instruments)
            types = tuple(item if isinstance(item, MarketEventType) else MarketEventType(item) for item in query.event_types)
            rows = []
            for event in self.events.scan(release.release_id, start, end, instruments=ids or None,
                                          event_types=types or None, view=query.view.value):
                rows.append(_event_row(event))
                if len(rows) >= batch_size:
                    table = _arrow()[0].Table.from_pylist(rows)
                    if query.fields is not None:
                        table = _select(table, query.fields, release.release_id)
                    yield from table.to_batches()
                    rows = []
            if rows:
                table = _arrow()[0].Table.from_pylist(rows)
                if query.fields is not None:
                    table = _select(table, query.fields, release.release_id)
                yield from table.to_batches()
            return
        parquet = _parquet_files(root, query.start, query.end)
        if parquet:
            _, _, ds = _arrow()
            dataset = _parquet_dataset(ds, parquet)
            primary_time = self.catalog.product(release.product_key).primary_time
            _ensure_columns(query.fields, set(dataset.schema.names), release.release_id)
            expression = _dataset_filter(ds, dataset.schema, primary_time, query.start, query.end, query.instruments)
            yield from dataset.scanner(columns=list(query.fields) if query.fields is not None else None,
                                       filter=expression, batch_size=batch_size).to_batches()
            return
        if any(root.glob("**/*.parquet")):
            return
        yield from self._execute(query, OutputFormat.ARROW).to_batches(max_chunksize=batch_size)

    def load_rows(self, dataset: DatasetLike, **kwargs) -> list[dict[str, object]]:
        return self.get(dataset, **kwargs).collect(OutputFormat.ROWS)

    def iter_rows(self, dataset: DatasetLike, **kwargs):
        batch_size = int(kwargs.pop("batch_size", 65536))
        for batch in self.get(dataset, **kwargs).stream(batch_size=batch_size):
            yield from batch.to_pylist()

    def replay_snapshots(self, dataset: DatasetLike, *, version: str | None = None,
                         provider: str | None = None, venue: str | None = None) -> ReplaySnapshotFeed:
        release = self.catalog.release(dataset, version=version, provider=provider, venue=venue)
        self._require_release_for_mode(release)
        release = self.resolve(release.release_id)
        directory = self.root / release.relative_path
        if release.storage_kind not in {DatasetStorageKind.MARKET_SNAPSHOTS, DatasetStorageKind.MARKET_SLICES}:
            raise ValueError(f"release {release.release_id!r} is not a MarketReplayDataset")
        historical = MarketSnapshotStorageDriver(directory.parent).load(directory)
        return ReplaySnapshotFeed(release, historical)

    def replay_slices(self, dataset: DatasetLike, *, version: str | None = None,
                      provider: str | None = None, venue: str | None = None) -> ReplaySnapshotFeed:
        return self.replay_snapshots(dataset, version=version, provider=provider, venue=venue)

    def collection(self, dataset: DatasetLike, *, version: str | None = None):
        release = self.catalog.release(dataset, version=version)
        self._require_release_for_mode(release)
        path = self.root / release.relative_path / "collection.json"
        if not path.exists():
            return None
        from kairos.study_platform.data_store import CollectionManifest
        from kairos.storage.codec import from_primitive
        return from_primitive(json.loads(path.read_text(encoding="utf-8")), CollectionManifest)

    @staticmethod
    def freeze_study(path: str | Path, study_id: str, queries: Iterable[DataQuery], *,
                     code_version: str, environment_hash: str | None = None) -> Path:
        return write_study_snapshot(path, study_id, (query.snapshot() for query in queries),
                                    code_version=code_version, environment_hash=environment_hash)

    def freeze_products(self, path: str | Path, study_id: str, datasets: Iterable[DatasetLike], *,
                        code_version: str, environment_hash: str | None = None) -> Path:
        queries = tuple(self.get(dataset) for dataset in datasets)
        return self.freeze_study(path, study_id, queries, code_version=code_version,
                                 environment_hash=environment_hash)

    def _require_release_for_mode(self, release) -> None:
        if self.run_mode in {RunMode.BACKTEST, RunMode.HISTORICAL_SIMULATION}:
            if release.status not in {DatasetStatus.APPROVED_FOR_BACKTEST, DatasetStatus.APPROVED_FOR_PRODUCTION}:
                raise PermissionError(f"{self.run_mode.value} requires an approved_for_backtest release")
            if release.quality_level not in {QualityLevel.BACKTEST, QualityLevel.PRODUCTION}:
                raise PermissionError(f"{self.run_mode.value} requires Q3 or Q4 data")
        if self.run_mode in {RunMode.PAPER_TRADING, RunMode.LIVE}:
            if release.status is not DatasetStatus.APPROVED_FOR_PRODUCTION or release.quality_level is not QualityLevel.PRODUCTION:
                raise PermissionError(f"{self.run_mode.value} requires approved_for_production Q4 data")
        if self.run_mode is not RunMode.STUDY and release.content_hash is None:
            raise ValueError(f"{self.run_mode.value} requires a frozen release content hash")

    def sql(self, query: str, *, datasets: dict[str, str], output: Literal["arrow", "pandas", "rows"] = "arrow"):
        """Run DuckDB SQL with catalog datasets exposed as named Arrow views."""
        try:
            import duckdb
        except ImportError as error:
            raise RuntimeError("SQL research queries require the 'query' optional dependency") from error
        connection = duckdb.connect(database=":memory:")
        try:
            for view_name, dataset in datasets.items():
                if not view_name.replace("_", "").isalnum():
                    raise ValueError(f"unsafe SQL view name: {view_name!r}")
                connection.register(view_name, self.get(dataset).collect(OutputFormat.ARROW))
            table = connection.execute(query).to_arrow_table()
            return _convert(table, output)
        finally:
            connection.close()

    def _load_events(self, dataset_id, start, end, instruments, event_types, view):
        start_value, end_value = self._event_window(dataset_id, start, end)
        ids = tuple(item if isinstance(item, InstrumentId) else InstrumentId(item) for item in instruments or ())
        types = tuple(item if isinstance(item, MarketEventType) else MarketEventType(item) for item in event_types or ())
        events = self.events.scan(dataset_id, start_value, end_value, instruments=ids or None, event_types=types or None, view=view)
        rows = []
        for event in events:
            rows.append(_event_row(event))
        pa, _, _ = _arrow()
        return pa.Table.from_pylist(rows) if rows else pa.table({})

    def _event_window(self, dataset_id, start, end):
        metadata = self.events.metadata(dataset_id)
        window = metadata.get("coverage", {}).get("requested_window") or metadata.get("coverage", {}).get("observed_window", {})
        start_value = _datetime(start or window.get("start") or window.get("minimum_event_time"))
        end_value = _datetime(end or window.get("end") or _exclusive_end(window.get("maximum_event_time")))
        if start_value is None or end_value is None:
            raise ValueError("event datasets require start/end when coverage has no usable window")
        return start_value, end_value

    @staticmethod
    def _load_files(root: Path, *, start, end, instruments, columns, primary_time):
        pa, csv, ds = _arrow()
        parquet = _parquet_files(root, start, end)
        if parquet:
            dataset = _parquet_dataset(ds, parquet)
            names = set(dataset.schema.names)
            selected = list(columns) if columns is not None else None
            _ensure_columns(selected, names, root.name)
            if _requires_post_filter(parquet, primary_time):
                required = list(dict.fromkeys([*(selected or dataset.schema.names), primary_time,
                                               *(["instrument_id"] if instruments and "instrument_id" in names else [])]))
                table = dataset.to_table(columns=required)
                table = _filter_table(table, start=start, end=end, instruments=instruments, primary_time=primary_time)
                return _select(table, selected, root.name) if selected is not None else table
            expression = _dataset_filter(ds, dataset.schema, primary_time, start, end, instruments)
            return dataset.to_table(columns=selected, filter=expression)
        all_parquet = _parquet_files(root, None, None)
        if all_parquet:
            dataset = _parquet_dataset(ds, all_parquet)
            selected = list(columns) if columns is not None else list(dataset.schema.names)
            _ensure_columns(selected, set(dataset.schema.names), root.name)
            return dataset.to_table(columns=selected).slice(0, 0)
        csv_files = sorted(root.glob("event_year=*/event_month=*/event_day=*/part-*.csv")) or sorted(root.glob("event_year=*/event_month=*/part-*.csv"))
        if csv_files:
            table = pa.concat_tables([csv.read_csv(path) for path in csv_files], promote_options="default")
            table = _filter_table(table, start=start, end=end, instruments=instruments, primary_time=primary_time)
            return _select(table, columns, root.name) if columns is not None else table
        raise FileNotFoundError(f"no research-readable data files under {root}")


def _parquet_files(root: Path, start, end) -> list[Path]:
    files = [path for path in sorted(root.glob("**/*.parquet"))
             if not any(part.startswith("release=") for part in path.relative_to(root).parts[:-1])]
    if start is None and end is None:
        return files
    start_value, end_value = _datetime(start), _datetime(end)
    selected = []
    for path in files:
        parts = {key: value for part in path.relative_to(root).parts[:-1]
                 if "=" in part for key, value in (part.split("=", 1),)}
        try:
            year = int(parts["event_year"]); month = int(parts.get("event_month", 1))
            day = int(parts.get("event_day", 1))
            lower = datetime(year, month, day, tzinfo=timezone.utc)
            if "event_day" in parts:
                upper = lower + timedelta(days=1)
            elif "event_month" in parts:
                upper = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)
            else:
                upper = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            selected.append(path); continue
        if (start_value is None or upper > start_value) and (end_value is None or lower < end_value):
            selected.append(path)
    return selected


def _parquet_dataset(ds, paths: list[Path]):
    import pyarrow as pa
    import pyarrow.parquet as pq
    schemas = [pq.read_schema(path) for path in paths]
    try:
        schema = pa.unify_schemas(schemas, promote_options="permissive")
    except pa.ArrowTypeError:
        fields = []
        names = tuple(dict.fromkeys(name for schema in schemas for name in schema.names))
        for name in names:
            types = [schema.field(name).type for schema in schemas if name in schema.names]
            non_null = [value for value in types if not pa.types.is_null(value)]
            if not non_null:
                field_type = pa.null()
            elif all(value == non_null[0] for value in non_null):
                field_type = non_null[0]
            elif any(pa.types.is_timestamp(value) for value in non_null) and all(
                    pa.types.is_string(value) or pa.types.is_large_string(value) or pa.types.is_timestamp(value)
                    or pa.types.is_date(value) for value in non_null):
                field_type = next(value for value in non_null if pa.types.is_timestamp(value))
            elif all(pa.types.is_integer(value) or pa.types.is_floating(value) for value in non_null):
                field_type = pa.float64()
            else:
                rendered = ", ".join(sorted({str(value) for value in non_null}))
                raise TypeError(f"incompatible Parquet partition types for {name}: {rendered}")
            fields.append(pa.field(name, field_type, nullable=True))
        schema = pa.schema(fields)
    return ds.dataset([str(path) for path in paths], format="parquet", schema=schema)


def _requires_post_filter(paths: list[Path], primary_time: str) -> bool:
    import pyarrow.parquet as pq
    types = {str(schema.field(primary_time).type) for path in paths
             for schema in (pq.read_schema(path),) if primary_time in schema.names}
    return len(types) > 1


def _filter_table(table, *, start, end, instruments, primary_time="available_time"):
    import pyarrow.compute as pc
    time_column = primary_time if primary_time in table.column_names else None
    mask = None
    if time_column and start is not None:
        mask = pc.greater_equal(table[time_column], _coerce_scalar(table[time_column].type, start))
    if time_column and end is not None:
        value = pc.less(table[time_column], _coerce_scalar(table[time_column].type, end))
        mask = value if mask is None else pc.and_(mask, value)
    if instruments and "instrument_id" in table.column_names:
        value = pc.is_in(table["instrument_id"], value_set=_arrow()[0].array([str(item) for item in instruments]))
        mask = value if mask is None else pc.and_(mask, value)
    return table.filter(mask) if mask is not None else table


def _dataset_filter(ds, schema, primary_time, start, end, instruments):
    names = set(schema.names)
    expression = None
    if (start is not None or end is not None) and primary_time not in names:
        raise ValueError(f"dataset declares primary time {primary_time!r}, but that field is absent")
    if start is not None:
        value = ds.field(primary_time) >= _dataset_scalar(schema.field(primary_time).type, start)
        expression = value
    if end is not None:
        value = ds.field(primary_time) < _dataset_scalar(schema.field(primary_time).type, end)
        expression = value if expression is None else expression & value
    if instruments:
        if "instrument_id" not in names:
            raise ValueError("instrument filter requested for dataset without instrument_id")
        value = ds.field("instrument_id").isin([str(item) for item in instruments])
        expression = value if expression is None else expression & value
    return expression


def _dataset_scalar(data_type, value):
    import pyarrow as pa
    if pa.types.is_timestamp(data_type):
        result = _datetime(value)
        if result is None:
            return None
        timezone_name = getattr(data_type, "tz", None)
        if timezone_name is None:
            return result.astimezone(timezone.utc).replace(tzinfo=None)
        return result.astimezone(timezone.utc)
    return str(value)


def _ensure_columns(columns, names, dataset_id):
    if columns is None:
        return
    missing = sorted(set(columns) - set(names))
    if missing:
        raise KeyError(f"columns not found in {dataset_id}: {', '.join(missing)}")


def _select(table, columns, dataset_id):
    selected = list(columns)
    _ensure_columns(selected, table.column_names, dataset_id)
    return table.select(selected)


def _coerce_scalar(data_type, value):
    pa = _arrow()[0]
    if pa.types.is_timestamp(data_type):
        return pa.scalar(_datetime(value), type=data_type)
    return pa.scalar(str(value), type=data_type)


def _convert(table, output):
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


def _datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("research time filters must be timezone-aware")
    return parsed


def _exclusive_end(value):
    if value is None:
        return None
    from datetime import timedelta
    return _datetime(value) + timedelta(microseconds=1)


def _release_metadata(release, product):
    return {"release_id": release.release_id, "logical_key": str(release.product_key),
            "release_version": release.release_version, "format": release.format,
            "layer": product.layer.value, "schema_id": release.schema_id,
            "schema_version": release.schema_version, "transform_id": release.transform_id,
            "transform_version": release.transform_version, "content_hash": release.content_hash,
            "provider": release.provider, "venue": release.venue,
            "aliases": list(release.aliases), "status": release.status.value,
            "quality_level": release.quality_level.value, "published_at": release.published_at}


def _release_summary(value):
    return {"release_id": value.release_id, "version": value.release_version,
            "content_hash": value.content_hash, "provider": value.provider, "venue": value.venue,
            "status": value.status.value, "quality_level": value.quality_level.value,
            "published_at": value.published_at, "aliases": list(value.aliases)}


def _document_comparison(first, second):
    return {"equal": first == second, "first": first, "second": second}


def _schema_compatibility(first, second):
    if not isinstance(first, dict) or not isinstance(second, dict) or not first or not second:
        return {"status": "unknown", "reasons": ["schema metadata is missing"]}
    reasons = []
    if tuple(first.get("primary_key", ())) != tuple(second.get("primary_key", ())):
        reasons.append("primary_key changed")
    old_columns = first.get("columns", {}) if isinstance(first.get("columns"), dict) else {}
    new_columns = second.get("columns", {}) if isinstance(second.get("columns"), dict) else {}
    for name, old in old_columns.items():
        if name not in new_columns:
            reasons.append(f"column removed: {name}"); continue
        old_type = old.get("type") if isinstance(old, dict) else old
        new = new_columns[name]
        new_type = new.get("type") if isinstance(new, dict) else new
        if old_type != new_type:
            reasons.append(f"column type changed: {name} ({old_type} -> {new_type})")
    return {"status": "incompatible" if reasons else "compatible", "reasons": reasons}


def _event_row(event):
    payload = {key: (Decimal(value) if key in _DECIMAL_FIELDS and value is not None else value)
               for key, value in event.payload.items()}
    return {
        "instrument_id": str(event.instrument_id), "event_time": event.event_time,
        "receive_time": event.receive_time, "available_time": event.available_time,
        "ingested_at": event.ingested_at, "source": event.source,
        "source_namespace": event.source_namespace, "source_instrument_id": event.source_instrument_id,
        "record_type": event.record_type.value, "source_order": event.source_order,
        "publisher_id": event.publisher_id, "flags": list(event.flags), **payload,
    }


def _arrow():
    try:
        import pyarrow as pa
        import pyarrow.csv as csv
        import pyarrow.dataset as ds
    except ImportError as error:
        raise RuntimeError("DatasetClient requires the 'data' optional dependency") from error
    return pa, csv, ds


_DECIMAL_FIELDS = frozenset({"bid", "ask", "bid_size", "ask_size", "price", "size", "open", "high", "low",
                             "close", "volume", "vwap", "last_trade_price", "last_trade_size",
                             "vendor_implied_volatility", "vendor_open_interest", "vendor_fmv"})


ResearchDataClient = DatasetClient
