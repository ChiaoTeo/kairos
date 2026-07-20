from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from kairospy.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.data.columnar_publishing import publish_intraday_staging_parquet
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairospy.data.products import (
    US_EQUITY_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,
    capabilities_payload,
)
from kairospy.storage.data_lake import write_json

from .client import MassiveClient
from .equity_daily_ohlcv import MassiveEquityDailyOhlcvPipeline
from .pipeline import MassiveOptionDataPipeline
from .reference_pipeline import MassiveReferencePipeline
from .vendor_archive import MassiveVendorArchiveClient, request_fingerprint


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
            (f"{product.key}@latest-study",), DatasetStatus.APPROVED_FOR_STUDY,
            QualityLevel.STUDY, published_at, DatasetStorageKind.TABULAR, "1",
        )
        catalog.register_release(release); catalog.save()
        from kairospy.data.release_metadata import ensure_release_metadata
        ensure_release_metadata(self.root, release.release_id)
        return release


class MassiveEquityHourlyOhlcvDatasetConnector:
    provider = "massive"

    def __init__(self, root: str | Path, client: MassiveClient, *, view: str = "adjusted") -> None:
        view = "adjusted" if view == "vendor_adjusted" else view
        if view not in {"raw", "adjusted"}:
            raise ValueError("Massive equity hourly view must be 'raw' or 'adjusted'")
        self.root = Path(root)
        self.client = client
        self.view = view
        self.source = MassiveVendorArchiveClient(root, client)

    @property
    def product(self):
        return US_EQUITY_MASSIVE_RAW_HOURLY if self.view == "raw" else US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(self.product.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        ranges = max(1, len(request.missing))
        instruments = len(request.instruments) if request.instruments else _estimated_equity_symbol_count(self.root)
        return AcquisitionEstimate(ranges * instruments, cost_class="entitled-rest-full-market-hourly", instruments=instruments)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        symbols = tuple({_equity_symbol(item) for item in request.instruments}) if request.instruments else self.discover_symbols()
        adjusted = self.view == "adjusted"
        ranges = []
        total = cached = 0
        for missing in request.missing:
            range_total = range_cached = 0
            start_date = missing.start.date()
            end_date = (missing.end - timedelta(microseconds=1)).date()
            for symbol in symbols:
                range_total += 1
                resource = f"/v2/aggs/ticker/{symbol}/range/1/hour/{start_date.isoformat()}/{end_date.isoformat()}"
                params = {"adjusted": adjusted, "sort": "asc", "limit": 50000}
                if _archived_rest_receipt_exists(self.root, resource, params):
                    range_cached += 1
            total += range_total
            cached += range_cached
            ranges.append({
                "start": missing.start.isoformat(),
                "end": missing.end.isoformat(),
                "tasks": range_total,
                "cached": range_cached,
                "uncached": range_total - range_cached,
            })
        return {
            "provider": "massive",
            "task_type": "rest-paginated-aggregate",
            "universe": "bounded" if request.instruments else "full-market",
            "symbols": len(symbols),
            "view": self.view,
            "total_tasks": total,
            "cached_tasks": cached,
            "uncached_tasks": total - cached,
            "resume_supported": True,
            "ranges": ranges,
        }

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Massive equity hourly connector received an unsupported acquisition request")
        if not request.missing:
            raise ValueError("Massive equity hourly connector requires a non-empty acquisition window")
        symbols = tuple(request.instruments) or self.discover_symbols()
        symbols = tuple(sorted({_equity_symbol(item) for item in symbols if str(item).strip()}))
        if not symbols:
            raise RuntimeError("Massive equity hourly acquisition has no symbols")
        adjusted = self.view == "adjusted"
        staging = self.root / "tmp" / "columnar" / f"massive-equity-1h-{uuid4().hex}"
        try:
            stats = self._stage(symbols, request, staging, adjusted=adjusted)
            if int(stats["rows"]) <= 0:
                raise RuntimeError("Massive equity hourly archive returned no rows")
            lineage = {
                "lineage_version": 2,
                "producer": {"name": type(self).__name__, "transform": "massive_equity_hourly_aggregate_to_ohlcv", "version": "1"},
                "source": {
                    "provider": "massive", "venue": "us-securities", "dataset": "stocks_hourly_aggregates",
                    "transport": "rest", "authentication": "api-key",
                },
                "request_windows": [
                    {"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                    for item in request.missing
                ],
                "universe": {
                    "kind": "bounded" if request.instruments else "full-market",
                    "symbols": list(symbols),
                    "selection": "explicit acquisition instruments" if request.instruments else "Massive active US common stock reference tickers",
                },
                "view": self.view,
                "adjusted": adjusted,
                "point_in_time_safe": not adjusted,
                "publishing": {"engine": "duckdb-parquet", "staged_files": stats["staged_files"]},
                "source_receipts": stats["source_receipts"],
            }
            result = publish_intraday_staging_parquet(
                self.root, self.product, staging,
                schema=_equity_hourly_schema(self.product.schema_id), lineage=lineage,
                interval=timedelta(hours=1),
                capabilities=capabilities_payload(self.product, "pending"),
                provider="massive", venue="us-securities",
                transform_id="massive.equity_hourly_ohlcv", transform_version="1",
                quality_level=QualityLevel.STUDY,
                primary_key=("venue", "instrument_id", "period_start", "interval"),
                order_by=("period_start", "instrument_id"),
            )
            return result.release
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def discover_symbols(self) -> tuple[str, ...]:
        discover = getattr(self.source, "discover_symbols", None)
        if discover is not None:
            return tuple(str(item).upper() for item in discover(self.root / "source"))
        records_file = _latest_equity_ticker_records(self.root)
        if records_file is None:
            manifest = MassiveReferencePipeline(self.root, self.client).sync_equity_tickers(include_inactive=False)
            records_file = Path(str(manifest["records_file"]))
        records = json.loads(records_file.read_text(encoding="utf-8"))
        return tuple(sorted({
            str(item["ticker"]).upper()
            for item in records
            if isinstance(item, dict) and item.get("ticker") and item.get("active", True)
        }))

    def _stage(self, symbols: tuple[str, ...], request: AcquisitionRequest, staging: Path, *, adjusted: bool) -> dict[str, object]:
        pa, pq = _pyarrow()
        total_rows = 0
        staged_files = 0
        receipts: list[str] = []
        for missing in request.missing:
            start_date = missing.start.date()
            end_date = (missing.end - timedelta(microseconds=1)).date()
            for symbol in symbols:
                archive = self.source.fetch_pages(
                    f"/v2/aggs/ticker/{symbol}/range/1/hour/{start_date.isoformat()}/{end_date.isoformat()}",
                    {"adjusted": adjusted, "sort": "asc", "limit": 50000},
                )
                receipts.append(str((archive.directory / "receipt.json").relative_to(self.root)))
                rows = list(_equity_hourly_rows(symbol, self.view, self.source.iter_results(archive), missing.start, missing.end))
                if not rows:
                    continue
                partition = staging / f"event_year={rows[0]['period_start'].year:04d}" / f"event_month={rows[0]['period_start'].month:02d}"
                partition.mkdir(parents=True, exist_ok=True)
                target = partition / f"{symbol}-{archive.fingerprint[:16]}.parquet"
                pq.write_table(pa.Table.from_pylist(rows, schema=_equity_hourly_arrow_schema(pa)), target, compression="zstd")
                total_rows += len(rows)
                staged_files += 1
        return {"rows": total_rows, "staged_files": staged_files, "source_receipts": receipts}


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


def _equity_hourly_rows(symbol: str, view: str, raw_rows, start: datetime, end: datetime):
    for raw in raw_rows:
        period_start = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc)
        if not start <= period_start < end:
            continue
        period_end = period_start + timedelta(hours=1)
        yield {
            "period_start": period_start,
            "period_end": period_end,
            "event_time": period_end,
            "available_time": period_end,
            "venue": "us-securities",
            "instrument_id": f"equity:us:{symbol}",
            "symbol": symbol,
            "product": "equity",
            "interval": "PT1H",
            "price_view": view,
            "open": float(raw["o"]),
            "high": float(raw["h"]),
            "low": float(raw["l"]),
            "close": float(raw["c"]),
            "volume": float(raw.get("v", 0)),
            "trade_count": int(raw.get("n", 0)),
            "vwap": float(raw["vw"]) if raw.get("vw") is not None else None,
        }


def _equity_symbol(value: object) -> str:
    text = str(value).strip().upper()
    if text.startswith("EQUITY:US:"):
        text = text.split(":", 2)[2]
    return text


def _equity_hourly_schema(schema_id: str) -> dict[str, object]:
    return {
        "schema_id": schema_id,
        "schema_version": 1,
        "time_boundary": "[period_start,period_end)",
        "primary_key": ["venue", "instrument_id", "period_start", "interval"],
        "columns": {
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "venue": {"type": "string"},
            "instrument_id": {"type": "string"},
            "symbol": {"type": "string"},
            "product": {"type": "string"},
            "interval": {"type": "duration"},
            "price_view": {"type": "string"},
            "open": {"type": "number"},
            "high": {"type": "number"},
            "low": {"type": "number"},
            "close": {"type": "number"},
            "volume": {"type": "number"},
            "trade_count": {"type": "integer"},
            "vwap": {"type": "number"},
        },
    }


def _equity_hourly_arrow_schema(pa):
    return pa.schema([
        pa.field("period_start", pa.timestamp("us")),
        pa.field("period_end", pa.timestamp("us")),
        pa.field("event_time", pa.timestamp("us")),
        pa.field("available_time", pa.timestamp("us")),
        pa.field("venue", pa.string()),
        pa.field("instrument_id", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("product", pa.string()),
        pa.field("interval", pa.string()),
        pa.field("price_view", pa.string()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("trade_count", pa.int64()),
        pa.field("vwap", pa.float64()),
    ])


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


def _archived_rest_receipt_exists(root: Path, resource: str, params: dict[str, object]) -> bool:
    directory = root / "source" / "provider=massive" / f"resource={_safe_resource(resource)}" / f"request_id={request_fingerprint(resource, params)}"
    receipt = directory / "receipt.json"
    if not receipt.exists():
        return False
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return value.get("status") == "complete"


def _safe_resource(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value.strip("/"))[:160] or "root"


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Massive equity hourly OHLCV requires the 'data' optional dependency") from error
    return pa, pq
