from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import shutil
from uuid import uuid4

from kairospy.products.common import TradingCalendar
from kairospy.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.data.catalog import DataCatalog
from kairospy.data.columnar_publishing import publish_intraday_staging_parquet
from kairospy.data.contracts import DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairospy.data.products import (
    US_OPTION_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_RAW_DAILY,
    US_EQUITY_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,
)
from kairospy.data.products import capabilities_payload
from kairospy.data.builders import (
    EquityOhlcvDataProductBuilder,
    EquityOhlcvSourceBinding,
    OptionHourlyOhlcvDataProductBuilder,
    equity_ohlcv_arrow_schema,
    equity_ohlcv_schema,
    option_ohlcv_row,
)
from kairospy.infrastructure.storage.data_lake import write_json

from .client import MassiveClient
from .equity_daily_ohlcv import MassiveEquityDailyOhlcvPipeline
from .market_data import MassiveAggregateBarsRequest, MassiveHistoricalMarketDataService
from .pipeline import MassiveOptionDataPipeline
from .vendor_archive import MassiveFlatFileBatchDownloader, MassiveFlatFileClient, MassiveVendorArchiveClient


@dataclass(frozen=True, slots=True)
class MassiveOptionProductConfig:
    logical_key: str
    underlying: str
    option_tickers: tuple[str, ...]
    underlying_reference_ticker: str | None = None

    def __post_init__(self) -> None:
        if not self.logical_key or not self.underlying or not self.option_tickers:
            raise ValueError("Massive option connector requires product, underlying and explicit option tickers")
        if any(not ticker.startswith("O:") for ticker in self.option_tickers):
            raise ValueError("Massive option tickers must use the O: namespace")


@dataclass(frozen=True, slots=True)
class MassiveEquityDailyOhlcvProductConfig:
    logical_key: str
    ticker: str
    view: str = "vendor_adjusted"

    def __post_init__(self) -> None:
        if not self.logical_key or not self.ticker:
            raise ValueError("Massive equity connector requires product and ticker")
        if self.view not in {"raw", "vendor_adjusted"}:
            raise ValueError("Massive equity view must be 'raw' or 'vendor_adjusted'")


class MassiveEquityDailyOhlcvDatasetConnector:
    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, config: MassiveEquityDailyOhlcvProductConfig) -> None:
        self.root, self.config = Path(root), config
        self.pipeline = MassiveEquityDailyOhlcvPipeline(root, client)

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days, cost_class="entitled-rest-bounded-ticker")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Massive equity connector received an unsupported acquisition request")
        if not request.missing:
            raise ValueError("Massive equity connector requires a non-empty acquisition window")
        start = min(item.start for item in request.missing).date()
        end = max(item.end for item in request.missing).date()
        staging_id = f"staging_{uuid4().hex}"
        manifest = self.pipeline.prepare(
            staging_id, self.config.ticker, start, end, view=self.config.view,
        )
        final_id = f"ds_{str(manifest['content_sha256'])[:24]}"
        staging = (
            self.root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d"
            / f"view={self.config.view}" / f"dataset={staging_id}"
        )
        final = staging.with_name(f"dataset={final_id}")
        if final.exists():
            existing = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("content_sha256") != manifest["content_sha256"]:
                raise RuntimeError("content-addressed Massive equity release collision")
            shutil.rmtree(staging)
        else:
            staging.replace(final)
            for name in ("manifest", "lineage", "coverage", "quality", "schema"):
                path = final / f"{name}.json"
                value = json.loads(path.read_text(encoding="utf-8"))
                _replace_dataset_id(value, staging_id, final_id)
                write_json(path, value)
        catalog = DataCatalog(self.root)
        product = catalog.product(request.logical_key)
        published_at = str(manifest.get("generated_at") or datetime.now().astimezone().isoformat())
        release = DatasetRelease(
            final_id, product.key, published_at, "market.ohlcv.equity.us.1d.v1", "1",
            "massive.equity_daily_ohlcv", "1", str(final.relative_to(self.root)), "parquet",
            str(manifest["content_sha256"]), "massive", "us-securities",
            (f"{product.key}@latest-workspace",), DatasetStatus.APPROVED_FOR_WORKSPACE,
            QualityLevel.WORKSPACE, published_at, DatasetStorageKind.TABULAR, "1",
        )
        catalog.register_release(release); catalog.save()
        from kairospy.data.release_metadata import ensure_release_metadata
        ensure_release_metadata(self.root, release.release_id)
        return release


class MassiveEquityDailyMarketOhlcvDatasetConnector:
    """Compatibility wrapper for Massive built-in daily US equity OHLCV."""

    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, *, view: str = "vendor_adjusted") -> None:
        if view == "adjusted":
            view = "vendor_adjusted"
        if view not in {"raw", "vendor_adjusted"}:
            raise ValueError("Massive equity daily view must be 'raw' or 'vendor_adjusted'")
        self.root = Path(root)
        self.client = client
        self.view = view
        self.market_data = MassiveHistoricalMarketDataService(self.root, client)
        self.calendar = TradingCalendar()
        self.builder = EquityOhlcvDataProductBuilder(
            self.root,
            self.market_data,
            EquityOhlcvSourceBinding(
                product=self.product,
                provider=self.provider,
                venue="us-securities",
                view=self.view,
                adjusted=self.view == "vendor_adjusted",
                interval="P1D",
                timespan="day",
                aggregate_request=MassiveAggregateBarsRequest,
                source_dataset="stocks_daily_aggregates",
                transform_id="massive.equity_daily_ohlcv",
                producer_transform="massive_equity_daily_aggregate_to_ohlcv",
                cost_class="entitled-rest-full-market-daily",
                estimate_mode="days",
            ),
            discover_symbols=self.market_data.discover_equity_symbols,
            estimate_symbol_count=lambda: _estimated_equity_symbol_count(self.root),
            calendar=self.calendar,
        )

    @property
    def product(self):
        return US_EQUITY_MASSIVE_RAW_DAILY if self.view == "raw" else US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY

    @property
    def source(self):
        return self.market_data.source

    @source.setter
    def source(self, value) -> None:
        self.market_data.source = value

    def supports(self, logical_key: str) -> bool:
        return self.builder.supports(logical_key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return self.builder.estimate(request)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        return self.builder.task_plan(request)

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        return self.builder.acquire(request)

    def discover_symbols(self) -> tuple[str, ...]:
        return self.builder.discover_symbols()


class MassiveEquityHourlyOhlcvDatasetConnector:
    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, *, view: str = "adjusted") -> None:
        view = "adjusted" if view == "vendor_adjusted" else view
        if view not in {"raw", "adjusted"}:
            raise ValueError("Massive equity hourly view must be 'raw' or 'adjusted'")
        self.root = Path(root)
        self.client = client
        self.view = view
        self.market_data = MassiveHistoricalMarketDataService(self.root, client)
        self.builder = EquityOhlcvDataProductBuilder(
            self.root,
            self.market_data,
            EquityOhlcvSourceBinding(
                product=self.product,
                provider=self.provider,
                venue="us-securities",
                view=self.view,
                adjusted=self.view == "adjusted",
                interval="PT1H",
                timespan="hour",
                aggregate_request=MassiveAggregateBarsRequest,
                source_dataset="stocks_hourly_aggregates",
                transform_id="massive.equity_hourly_ohlcv",
                producer_transform="massive_equity_hourly_aggregate_to_ohlcv",
                cost_class="entitled-rest-full-market-hourly",
                estimate_mode="ranges",
            ),
            discover_symbols=self.market_data.discover_equity_symbols,
            estimate_symbol_count=lambda: _estimated_equity_symbol_count(self.root),
        )

    @property
    def product(self):
        return US_EQUITY_MASSIVE_RAW_HOURLY if self.view == "raw" else US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY

    @property
    def source(self):
        return self.market_data.source

    @source.setter
    def source(self, value) -> None:
        self.market_data.source = value

    def supports(self, logical_key: str) -> bool:
        return self.builder.supports(logical_key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return self.builder.estimate(request)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        return self.builder.task_plan(request)

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        return self.builder.acquire(request)

    def discover_symbols(self) -> tuple[str, ...]:
        return self.builder.discover_symbols()


class MassiveOptionHourlyOhlcvDatasetConnector:
    provider = "massive"
    minute_aggs_prefix = "us_options_opra/minute_aggs_v1"

    def __init__(self, root: str | Path, client: MassiveClient) -> None:
        self.root = Path(root)
        self.client = client
        self.market_data = MassiveHistoricalMarketDataService(self.root, client)
        self.flat_files = MassiveFlatFileClient(self.root, client)
        self.flat_file_batch = MassiveFlatFileBatchDownloader(self.flat_files, prefix=self.minute_aggs_prefix)
        self.calendar = TradingCalendar()
        self.builder = OptionHourlyOhlcvDataProductBuilder(
            self.root,
            self.market_data,
            EquityOhlcvSourceBinding(
                product=US_OPTION_MASSIVE_RAW_HOURLY,
                provider=self.provider,
                venue="opra",
                view="raw",
                adjusted=False,
                interval="PT1H",
                timespan="hour",
                aggregate_request=MassiveAggregateBarsRequest,
                source_dataset="options_rest_aggregate_bars",
                transform_id="massive.option_hourly_ohlcv",
                producer_transform="massive_option_hourly_aggregate_to_ohlcv",
                cost_class="entitled-rest-explicit-option-hourly",
            ),
            discover_symbols=lambda: (),
            estimate_symbol_count=lambda: 0,
        )

    @property
    def product(self):
        return US_OPTION_MASSIVE_RAW_HOURLY

    @property
    def source(self):
        return self.market_data.source

    @source.setter
    def source(self, value) -> None:
        self.market_data.source = value

    def supports(self, logical_key: str) -> bool:
        return self.builder.supports(logical_key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        if not request.instruments:
            days = sum(len(self._trading_days(item.start.date(), item.end.date())) for item in request.missing)
            return AcquisitionEstimate(days, cost_class="entitled-flat-file-opra-minute-aggs")
        return self.builder.estimate(request)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        if not request.instruments:
            ranges = []
            for missing in request.missing:
                range_total = range_cached = 0
                for trading_day in self._trading_days(missing.start.date(), missing.end.date()):
                    range_total += 1
                    if self.flat_files.local_file(self.flat_file_batch.key_for(trading_day)) is not None:
                        range_cached += 1
                from kairospy.data.builders import DataProductTaskPlan, TaskRangePlan, UniversePlan

                ranges.append(TaskRangePlan(missing.start, missing.end, range_total, range_cached))
            return DataProductTaskPlan(
                self.provider,
                "flat-file-minute-aggregate-to-hour",
                tuple(ranges),
                universe=UniversePlan("full-market-opra", 0, {"symbol_count_unknown": True}),
                metadata={
                    "source_prefix": self.minute_aggs_prefix,
                    "interval": "PT1H",
                    "resume_supported": True,
                    "requires_instruments": False,
                    "view": "raw",
                },
            ).to_primitive()
        return self.builder.task_plan(request)

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not request.instruments:
            return self._acquire_full_market_flat_file(request)
        return self.builder.acquire(request)

    def _acquire_full_market_flat_file(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Massive option hourly OHLCV connector received an unsupported acquisition request")
        if not request.missing:
            raise ValueError("Massive option hourly OHLCV connector requires a non-empty acquisition window")
        staging = self.root / "tmp" / "columnar" / f"massive-option-ohlcv-pt1h-{uuid4().hex}"
        try:
            stats = self._stage_full_market_flat_files(request, staging)
            if int(stats["rows"]) <= 0:
                raise RuntimeError("Massive OPRA minute aggregate Flat Files produced no option hourly OHLCV rows")
            lineage = {
                "lineage_version": 2,
                "producer": {
                    "name": type(self).__name__,
                    "transform": "massive_opra_minute_aggregate_to_option_hourly_ohlcv",
                    "version": "1",
                },
                "source": {
                    "provider": self.provider,
                    "venue": "opra",
                    "dataset": self.minute_aggs_prefix,
                    "transport": "flat-file",
                    "authentication": "api-key",
                },
                "request_windows": [
                    {"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                    for item in request.missing
                ],
                "universe": {
                    "kind": "full-market",
                    "selection": "all OPRA option tickers present in Massive minute aggregate Flat Files",
                },
                "view": "raw",
                "adjusted": False,
                "point_in_time_safe": True,
                "source_receipts": list(stats["source_receipts"]),
                "publishing": {"engine": "duckdb-parquet", "staged_files": stats["staged_files"]},
            }
            result = publish_intraday_staging_parquet(
                self.root,
                US_OPTION_MASSIVE_RAW_HOURLY,
                staging,
                schema=equity_ohlcv_schema(US_OPTION_MASSIVE_RAW_HOURLY.schema_id, "PT1H"),
                lineage=lineage,
                interval=timedelta(hours=1),
                capabilities=capabilities_payload(US_OPTION_MASSIVE_RAW_HOURLY, "pending"),
                provider=self.provider,
                venue="opra",
                transform_id="massive.option_hourly_ohlcv.flat_file",
                transform_version="1",
                quality_level=QualityLevel.WORKSPACE,
                primary_key=("venue", "instrument_id", "period_start", "interval"),
                order_by=("period_start", "instrument_id"),
            )
            return result.release
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _stage_full_market_flat_files(self, request: AcquisitionRequest, staging: Path) -> dict[str, object]:
        pa, pq = _pyarrow()
        staged_files = 0
        total_rows = 0
        receipts: list[str] = []
        for trading_day in self._request_trading_days(request):
            key = self.flat_file_batch.key_for(trading_day)
            path = self.flat_files.download(key)
            receipt_path = path.parent / "receipt.json"
            receipts.append(_relative_to(receipt_path, self.root))
            rows = _hourly_rows_from_minute_file(path, request.missing)
            if not rows:
                continue
            partition = staging / f"event_year={trading_day.year:04d}" / f"event_month={trading_day.month:02d}"
            partition.mkdir(parents=True, exist_ok=True)
            target = partition / f"opra-minute-aggs-{trading_day.isoformat()}.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=equity_ohlcv_arrow_schema(pa)), target, compression="zstd")
            staged_files += 1
            total_rows += len(rows)
        return {"rows": total_rows, "staged_files": staged_files, "source_receipts": receipts}

    def _request_trading_days(self, request: AcquisitionRequest) -> tuple[date, ...]:
        days: set[date] = set()
        for missing in request.missing:
            days.update(self._trading_days(missing.start.date(), missing.end.date()))
        return tuple(sorted(days))

    def _trading_days(self, start: date, end: date) -> tuple[date, ...]:
        if start >= end:
            end = start + timedelta(days=1)
        return tuple(self.calendar.trading_days_between(start, end - timedelta(days=1)))


class MassiveOptionEventsDatasetConnector:
    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, config: MassiveOptionProductConfig, *,
                 catalog_path: str | Path | None = None, mapping_path: str | Path | None = None) -> None:
        self.root, self.config = Path(root), config
        self.pipeline = MassiveOptionDataPipeline(root, client, catalog_path=catalog_path, mapping_path=mapping_path)

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days * len(self.config.option_tickers) * 3 + 6, cost_class="entitled")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Massive connector received an unsupported acquisition request")
        if not request.missing:
            raise ValueError("Massive connector requires a non-empty acquisition window")
        start = min(item.start for item in request.missing)
        end = max(item.end for item in request.missing)
        if request.base_release_id is not None:
            coverage = DataCatalog(self.root).path(request.base_release_id) / "coverage.json"
            value = json.loads(coverage.read_text(encoding="utf-8"))
            window = value.get("requested_window") or value.get("observed_window") or {}
            previous_start = window.get("start") or window.get("minimum_event_time")
            if previous_start:
                start = min(start, datetime.fromisoformat(str(previous_start).replace("Z", "+00:00")))
        staging_id = f"staging_{uuid4().hex}"
        manifest = self.pipeline.prepare_options(
            dataset_id=staging_id, underlying=self.config.underlying,
            underlying_reference_ticker=self.config.underlying_reference_ticker,
            option_tickers=self.config.option_tickers, start=start, end=end, register=False,
        )
        final_id = f"ds_{str(manifest['dataset_sha256'])[:24]}"
        staging = self.root / "canonical" / "market" / f"dataset={staging_id}"
        final = self.root / "canonical" / "market" / f"dataset={final_id}"
        if final.exists():
            existing = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("dataset_sha256") != manifest["dataset_sha256"]:
                raise RuntimeError("content-addressed Massive release collision")
            shutil.rmtree(staging)
        else:
            staging.replace(final)
            for name in ("manifest", "lineage", "coverage", "quality", "schema"):
                path = final / f"{name}.json"
                if not path.exists():
                    continue
                value = json.loads(path.read_text(encoding="utf-8"))
                _replace_dataset_id(value, staging_id, final_id)
                write_json(path, value)
        catalog = DataCatalog(self.root)
        product = catalog.product(request.logical_key)
        release = DatasetRelease(
            final_id, product.key, str(manifest["generated_at"]), "market.event_envelope.v1", "1",
            "massive.option_events", "2", f"canonical/market/dataset={final_id}", "parquet",
            str(manifest["dataset_sha256"]), "massive", "opra", (f"{product.key}@latest-validated",),
            DatasetStatus.APPROVED_FOR_BACKTEST, QualityLevel.BACKTEST, str(manifest["generated_at"]),
            DatasetStorageKind.MARKET_EVENTS, "1",
        )
        catalog.register_release(release); catalog.save()
        from kairospy.data.release_metadata import ensure_release_metadata
        ensure_release_metadata(self.root, release.release_id)
        return release


def _hourly_rows_from_minute_file(path: Path, ranges: tuple) -> list[dict[str, object]]:
    required = {"ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"}
    groups: dict[tuple[str, datetime], dict[str, object]] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not required <= set(reader.fieldnames or ()):
            raise ValueError(f"Massive OPRA minute aggregate schema mismatch: {path}")
        for row in reader:
            ticker = str(row["ticker"]).strip().upper()
            if not ticker:
                continue
            minute_start = _timestamp_from_provider_ns(row["window_start"])
            if not any(item.start <= minute_start < item.end for item in ranges):
                continue
            hour_start = _opra_hour_start(minute_start)
            key = (ticker, hour_start)
            volume = float(row.get("volume") or 0)
            transactions = int(float(row.get("transactions") or 0))
            group = groups.setdefault(key, {
                "first_time": minute_start,
                "last_time": minute_start,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": 0.0,
                "transactions": 0,
                "vwap_numerator": 0.0,
                "vwap_volume": 0.0,
            })
            if minute_start < group["first_time"]:
                group["first_time"] = minute_start
                group["open"] = float(row["open"])
            if minute_start >= group["last_time"]:
                group["last_time"] = minute_start
                group["close"] = float(row["close"])
            group["high"] = max(float(group["high"]), float(row["high"]))
            group["low"] = min(float(group["low"]), float(row["low"]))
            group["volume"] = float(group["volume"]) + volume
            group["transactions"] = int(group["transactions"]) + transactions
            raw_vwap = row.get("vwap")
            if raw_vwap not in {None, ""} and volume > 0:
                group["vwap_numerator"] = float(group["vwap_numerator"]) + float(raw_vwap) * volume
                group["vwap_volume"] = float(group["vwap_volume"]) + volume
    rows = []
    for (ticker, hour_start), group in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0])):
        vwap = None
        if float(group["vwap_volume"]) > 0:
            vwap = float(group["vwap_numerator"]) / float(group["vwap_volume"])
        raw = {
            "t": int(hour_start.timestamp() * 1000),
            "o": group["open"],
            "h": group["high"],
            "l": group["low"],
            "c": group["close"],
            "v": group["volume"],
            "n": group["transactions"],
            "vw": vwap,
        }
        rows.append(option_ohlcv_row(ticker, "raw", raw, hour_start, hour_start + timedelta(hours=1), "PT1H"))
    return rows


def _timestamp_from_provider_ns(value: object) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)


def _opra_hour_start(value: datetime) -> datetime:
    base = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if base.minute >= 30:
        return base.replace(minute=30)
    return (base - timedelta(hours=1)).replace(minute=30)


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Massive option hourly OHLCV requires the 'data' optional dependency") from error
    return pa, pq


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _replace_dataset_id(value, previous: str, current: str) -> None:
    if isinstance(value, dict):
        for key, item in tuple(value.items()):
            if key == "dataset_id" and item == previous:
                value[key] = current
            else:
                _replace_dataset_id(item, previous, current)
    elif isinstance(value, list):
        for item in value:
            _replace_dataset_id(item, previous, current)


def _latest_equity_ticker_records(root: Path) -> Path | None:
    versions = sorted((root / "reference" / "provider=massive" / "equity_tickers").glob("version=*/records.json"))
    return versions[-1] if versions else None


def _estimated_equity_symbol_count(root: Path) -> int:
    records_file = _latest_equity_ticker_records(root)
    if records_file is None:
        return 8000
    try:
        records = json.loads(records_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 8000
    return max(1, sum(1 for item in records if isinstance(item, dict) and item.get("ticker") and item.get("active", True)))
