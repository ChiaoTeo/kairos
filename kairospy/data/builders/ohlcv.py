from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Literal
from uuid import uuid4

from kairospy.data.acquisition_primitives import AcquisitionEstimate
from kairospy.data.columnar_publishing import publish_intraday_staging_parquet
from kairospy.data.contracts import DataProductContract, DatasetRelease, QualityLevel
from kairospy.data.products import capabilities_payload
from kairospy.data.publishing import DatasetPublisher, content_release_id_from_rows
from kairospy.data.publishing import merge_release_rows
from kairospy.infrastructure.storage.data_lake import write_json

from .planning import DataProductTaskPlan, TaskRangePlan, UniversePlan


AggregateRequestFactory = Callable[[str, int, str, date, date, bool], object]
DiscoverSymbols = Callable[[], tuple[str, ...]]
EstimateSymbolCount = Callable[[], int]


@dataclass(frozen=True, slots=True)
class EquityOhlcvSourceBinding:
    product: DataProductContract
    provider: str
    venue: str
    view: str
    adjusted: bool
    interval: Literal["P1D", "PT1H"]
    timespan: Literal["day", "hour"]
    aggregate_request: AggregateRequestFactory
    source_dataset: str
    transform_id: str
    producer_transform: str
    request_multiplier: int = 1
    transform_version: str = "1"
    producer_version: str = "1"
    cost_class: str = "entitled-rest"
    estimate_mode: Literal["ranges", "days"] = "ranges"

    def __post_init__(self) -> None:
        for name in ("provider", "venue", "view", "source_dataset", "transform_id", "producer_transform"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"equity OHLCV source binding {name} cannot be empty")
        if self.request_multiplier <= 0:
            raise ValueError("equity OHLCV source binding request_multiplier must be positive")


class EquityOhlcvDataProductBuilder:
    """Build governed US equity OHLCV datasets from a provider aggregate-bars service."""

    def __init__(
        self,
        root: str | Path,
        market_data_service: object,
        binding: EquityOhlcvSourceBinding,
        *,
        discover_symbols: DiscoverSymbols,
        estimate_symbol_count: EstimateSymbolCount,
        calendar: object | None = None,
    ) -> None:
        self.root = Path(root)
        self.market_data = market_data_service
        self.binding = binding
        self._discover_symbols = discover_symbols
        self._estimate_symbol_count = estimate_symbol_count
        self.calendar = calendar

    @property
    def provider(self) -> str:
        return self.binding.provider

    @property
    def product(self) -> DataProductContract:
        return self.binding.product

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(self.product.key)

    def estimate(self, request: object) -> AcquisitionEstimate:
        missing = tuple(getattr(request, "missing"))
        instruments = tuple(getattr(request, "instruments", ()))
        symbol_count = len(instruments) if instruments else self._estimate_symbol_count()
        units = sum(self._estimate_units(item) for item in missing)
        return AcquisitionEstimate(units * symbol_count, cost_class=self.binding.cost_class, instruments=symbol_count)

    def task_plan(self, request: object) -> dict[str, object]:
        symbols = self._symbols(request)
        ranges = []
        for missing in tuple(getattr(request, "missing")):
            range_total = range_cached = 0
            start_date, end_date = self._request_dates(missing)
            for symbol in symbols:
                range_total += 1
                if self._cached(symbol, start_date, end_date):
                    range_cached += 1
            ranges.append(TaskRangePlan(missing.start, missing.end, range_total, range_cached))
        return DataProductTaskPlan(
            self.provider,
            "rest-paginated-aggregate",
            tuple(ranges),
            universe=UniversePlan(
                "bounded" if tuple(getattr(request, "instruments", ())) else "full-market",
                len(symbols),
            ),
            metadata={
                "view": self.binding.view,
                "interval": self.binding.interval,
                "resume_supported": True,
            },
        ).to_primitive()

    def acquire(self, request: object) -> DatasetRelease:
        self._validate_request(request)
        if self.binding.interval == "P1D":
            return self._acquire_daily(request)
        if self.binding.interval == "PT1H":
            return self._acquire_intraday(request)
        raise RuntimeError(f"unsupported equity OHLCV interval {self.binding.interval!r}")

    def discover_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({equity_symbol(item) for item in self._discover_symbols()}))

    def _validate_request(self, request: object) -> None:
        if not self.supports(str(getattr(request, "logical_key"))) or getattr(getattr(request, "source"), "provider") != self.provider:
            raise ValueError(f"{self.provider} equity OHLCV builder received an unsupported acquisition request")
        if not tuple(getattr(request, "missing")):
            raise ValueError(f"{self.provider} equity OHLCV builder requires a non-empty acquisition window")

    def _acquire_daily(self, request: object) -> DatasetRelease:
        rows, receipts, symbols = self._load_rows(request)
        if not rows:
            raise RuntimeError(f"{self.provider} equity daily OHLCV source returned no rows")
        rows = merge_equity_ohlcv_rows(self.root, getattr(request, "base_release_id", None), rows)
        release_id = content_release_id_from_rows(self.product, rows)
        publisher = DatasetPublisher(self.root)
        target = publisher.path(self.product, release_id)
        if not target.exists():
            write_equity_ohlcv_dataset(
                target,
                rows,
                release_id=release_id,
                schema=equity_ohlcv_schema(self.product.schema_id, self.binding.interval),
                lineage=self._lineage(request, symbols, receipts),
                capabilities=capabilities_payload(self.product, release_id),
            )
        release = publisher.publish(
            self.product,
            release_id,
            json.loads((target / "manifest.json").read_text(encoding="utf-8")),
            provider=self.provider,
            venue=self.binding.venue,
            transform_id=self.binding.transform_id,
            transform_version=self.binding.transform_version,
            quality_level=QualityLevel.WORKSPACE,
        )
        from kairospy.data.release_metadata import ensure_release_metadata

        ensure_release_metadata(self.root, release.release_id)
        return release

    def _acquire_intraday(self, request: object) -> DatasetRelease:
        symbols = self._symbols(request)
        staging = self.root / "tmp" / "columnar" / f"{self.provider}-equity-ohlcv-{self.binding.interval.lower()}-{uuid_hex()}"
        try:
            stats = self._stage_intraday(symbols, request, staging)
            if int(stats["rows"]) <= 0:
                raise RuntimeError(f"{self.provider} equity intraday OHLCV source returned no rows")
            lineage = self._lineage(
                request,
                symbols,
                tuple(str(item) for item in stats["source_receipts"]),
                extra={"publishing": {"engine": "duckdb-parquet", "staged_files": stats["staged_files"]}},
            )
            result = publish_intraday_staging_parquet(
                self.root,
                self.product,
                staging,
                schema=equity_ohlcv_schema(self.product.schema_id, self.binding.interval),
                lineage=lineage,
                interval=self._interval_delta(),
                capabilities=capabilities_payload(self.product, "pending"),
                provider=self.provider,
                venue=self.binding.venue,
                transform_id=self.binding.transform_id,
                transform_version=self.binding.transform_version,
                quality_level=QualityLevel.WORKSPACE,
                primary_key=("venue", "instrument_id", "period_start", "interval"),
                order_by=("period_start", "instrument_id"),
            )
            return result.release
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _load_rows(self, request: object) -> tuple[list[dict[str, object]], tuple[str, ...], tuple[str, ...]]:
        rows: list[dict[str, object]] = []
        receipts: list[str] = []
        symbols = self._symbols(request)
        for missing in tuple(getattr(request, "missing")):
            start_date, end_date = self._request_dates(missing)
            for symbol in symbols:
                artifact = self._fetch(symbol, start_date, end_date)
                receipt = getattr(getattr(artifact, "artifact", None), "receipt_path", None)
                if receipt is not None:
                    receipts.append(str(receipt))
                raw_rows = self.market_data.iter_aggregate_bar_results(artifact)
                if self.binding.interval == "P1D":
                    if self.calendar is None:
                        raise RuntimeError("daily equity OHLCV builder requires a trading calendar")
                    rows.extend(equity_daily_ohlcv_rows(symbol, self.binding.view, raw_rows, missing.start, missing.end, self.calendar))
                else:
                    rows.extend(equity_hourly_ohlcv_rows(symbol, self.binding.view, raw_rows, missing.start, missing.end))
        return rows, tuple(receipts), symbols

    def _stage_intraday(self, symbols: tuple[str, ...], request: object, staging: Path) -> dict[str, object]:
        pa, pq = _pyarrow()
        total_rows = 0
        staged_files = 0
        receipts: list[str] = []
        for missing in tuple(getattr(request, "missing")):
            start_date, end_date = self._request_dates(missing)
            for symbol in symbols:
                artifact = self._fetch(symbol, start_date, end_date)
                receipt = getattr(getattr(artifact, "artifact", None), "receipt_path", None)
                if receipt is not None:
                    receipts.append(str(receipt))
                rows = list(equity_hourly_ohlcv_rows(
                    symbol,
                    self.binding.view,
                    self.market_data.iter_aggregate_bar_results(artifact),
                    missing.start,
                    missing.end,
                ))
                if not rows:
                    continue
                partition = staging / f"event_year={rows[0]['period_start'].year:04d}" / f"event_month={rows[0]['period_start'].month:02d}"
                partition.mkdir(parents=True, exist_ok=True)
                fingerprint = getattr(getattr(artifact, "artifact", None), "request_fingerprint", "")
                target = partition / f"{symbol}-{str(fingerprint)[:16] or uuid_hex()}.parquet"
                pq.write_table(pa.Table.from_pylist(rows, schema=equity_ohlcv_arrow_schema(pa)), target, compression="zstd")
                total_rows += len(rows)
                staged_files += 1
        return {"rows": total_rows, "staged_files": staged_files, "source_receipts": receipts}

    def _lineage(
        self,
        request: object,
        symbols: tuple[str, ...],
        receipts: tuple[str, ...],
        *,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        lineage = {
            "lineage_version": 2,
            "producer": {
                "name": type(self).__name__,
                "transform": self.binding.producer_transform,
                "version": self.binding.producer_version,
            },
            "source": {
                "provider": self.provider,
                "venue": self.binding.venue,
                "dataset": self.binding.source_dataset,
                "transport": "rest",
                "authentication": "api-key",
            },
            "request_windows": [
                {"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                for item in tuple(getattr(request, "missing"))
            ],
            "universe": {
                "kind": "bounded" if tuple(getattr(request, "instruments", ())) else "full-market",
                "symbols": list(symbols),
                "selection": "explicit acquisition instruments"
                if tuple(getattr(request, "instruments", ()))
                else f"{self.provider} active US equity reference tickers",
            },
            "view": self.binding.view,
            "adjusted": self.binding.adjusted,
            "point_in_time_safe": not self.binding.adjusted,
            "source_receipts": list(receipts),
        }
        if extra:
            lineage.update(extra)
        return lineage

    def _fetch(self, symbol: str, start: date, end: date):
        return self.market_data.fetch_aggregate_bars(self._aggregate_request(symbol, start, end))

    def _cached(self, symbol: str, start: date, end: date) -> bool:
        return bool(self.market_data.aggregate_bars_cached(self._aggregate_request(symbol, start, end)))

    def _aggregate_request(self, symbol: str, start: date, end: date) -> object:
        return self.binding.aggregate_request(
            symbol,
            self.binding.request_multiplier,
            self.binding.timespan,
            start,
            end,
            self.binding.adjusted,
        )

    def _symbols(self, request: object) -> tuple[str, ...]:
        values = tuple(getattr(request, "instruments", ())) or self.discover_symbols()
        symbols = tuple(sorted({equity_symbol(item) for item in values if str(item).strip()}))
        if not symbols:
            raise RuntimeError(f"{self.provider} equity OHLCV acquisition has no symbols")
        return symbols

    def _request_dates(self, missing: object) -> tuple[date, date]:
        return missing.start.date(), (missing.end - timedelta(microseconds=1)).date()

    def _estimate_units(self, missing: object) -> int:
        if self.binding.estimate_mode == "days":
            return max(1, (missing.end.date() - missing.start.date()).days + 1)
        return 1

    def _interval_delta(self) -> timedelta:
        if self.binding.interval == "PT1H":
            return timedelta(hours=1)
        if self.binding.interval == "P1D":
            return timedelta(days=1)
        raise RuntimeError(f"unsupported equity OHLCV interval {self.binding.interval!r}")


class OptionHourlyOhlcvDataProductBuilder(EquityOhlcvDataProductBuilder):
    """Build bounded US option hourly OHLCV datasets from provider aggregate bars."""

    def discover_symbols(self) -> tuple[str, ...]:
        return ()

    def estimate(self, request: object) -> AcquisitionEstimate:
        missing = tuple(getattr(request, "missing"))
        instruments = tuple(getattr(request, "instruments", ()))
        symbol_count = len({option_symbol(item) for item in instruments if str(item).strip()})
        return AcquisitionEstimate(
            len(missing) * symbol_count,
            cost_class=self.binding.cost_class,
            instruments=symbol_count,
        )

    def task_plan(self, request: object) -> dict[str, object]:
        instruments = tuple(getattr(request, "instruments", ()))
        if not instruments:
            ranges = tuple(TaskRangePlan(item.start, item.end, 0, 0) for item in tuple(getattr(request, "missing")))
            return DataProductTaskPlan(
                self.provider,
                "rest-paginated-aggregate",
                ranges,
                universe=UniversePlan("explicit-instruments-required", 0),
                metadata={
                    "view": self.binding.view,
                    "interval": self.binding.interval,
                    "resume_supported": True,
                    "requires_instruments": True,
                    "instrument_format": "O:ROOTYYMMDDC/P######## or option:us:ROOTYYMMDDC/P########",
                },
            ).to_primitive()
        return super().task_plan(request)

    def _validate_request(self, request: object) -> None:
        super()._validate_request(request)
        if self.binding.interval != "PT1H" or self.binding.timespan != "hour":
            raise ValueError("option OHLCV builder currently supports hourly bars only")
        if not tuple(getattr(request, "instruments", ())):
            raise ValueError("Massive option hourly OHLCV acquisition requires at least one --instrument O:... ticker")

    def _stage_intraday(self, symbols: tuple[str, ...], request: object, staging: Path) -> dict[str, object]:
        pa, pq = _pyarrow()
        total_rows = 0
        staged_files = 0
        receipts: list[str] = []
        for missing in tuple(getattr(request, "missing")):
            start_date, end_date = self._request_dates(missing)
            for symbol in symbols:
                artifact = self._fetch(symbol, start_date, end_date)
                receipt = getattr(getattr(artifact, "artifact", None), "receipt_path", None)
                if receipt is not None:
                    receipts.append(str(receipt))
                rows = list(option_hourly_ohlcv_rows(
                    symbol,
                    self.binding.view,
                    self.market_data.iter_aggregate_bar_results(artifact),
                    missing.start,
                    missing.end,
                ))
                if not rows:
                    continue
                partition = staging / f"event_year={rows[0]['period_start'].year:04d}" / f"event_month={rows[0]['period_start'].month:02d}"
                partition.mkdir(parents=True, exist_ok=True)
                fingerprint = getattr(getattr(artifact, "artifact", None), "request_fingerprint", "")
                target = partition / f"{_safe_filename(symbol)}-{str(fingerprint)[:16] or uuid_hex()}.parquet"
                pq.write_table(pa.Table.from_pylist(rows, schema=equity_ohlcv_arrow_schema(pa)), target, compression="zstd")
                total_rows += len(rows)
                staged_files += 1
        return {"rows": total_rows, "staged_files": staged_files, "source_receipts": receipts}

    def _lineage(
        self,
        request: object,
        symbols: tuple[str, ...],
        receipts: tuple[str, ...],
        *,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        lineage = {
            "lineage_version": 2,
            "producer": {
                "name": type(self).__name__,
                "transform": self.binding.producer_transform,
                "version": self.binding.producer_version,
            },
            "source": {
                "provider": self.provider,
                "venue": self.binding.venue,
                "dataset": self.binding.source_dataset,
                "transport": "rest",
                "authentication": "api-key",
            },
            "request_windows": [
                {"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                for item in tuple(getattr(request, "missing"))
            ],
            "universe": {
                "kind": "bounded",
                "symbols": list(symbols),
                "selection": "explicit Massive option tickers from acquisition instruments",
            },
            "view": self.binding.view,
            "adjusted": self.binding.adjusted,
            "point_in_time_safe": True,
            "source_receipts": list(receipts),
        }
        if extra:
            lineage.update(extra)
        return lineage

    def _symbols(self, request: object) -> tuple[str, ...]:
        symbols = tuple(sorted({option_symbol(item) for item in tuple(getattr(request, "instruments", ())) if str(item).strip()}))
        if not symbols:
            raise RuntimeError("Massive option hourly OHLCV acquisition requires explicit option instruments")
        return symbols


def equity_hourly_ohlcv_rows(symbol: str, view: str, raw_rows, start: datetime, end: datetime):
    for raw in raw_rows:
        period_start = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc)
        if not start <= period_start < end:
            continue
        period_end = period_start + timedelta(hours=1)
        yield equity_ohlcv_row(symbol, view, raw, period_start, period_end, "PT1H")


def option_hourly_ohlcv_rows(symbol: str, view: str, raw_rows, start: datetime, end: datetime):
    ticker = option_symbol(symbol)
    for raw in raw_rows:
        period_start = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc)
        if not start <= period_start < end:
            continue
        period_end = period_start + timedelta(hours=1)
        yield option_ohlcv_row(ticker, view, raw, period_start, period_end, "PT1H")


def equity_daily_ohlcv_rows(symbol: str, view: str, raw_rows, start: datetime, end: datetime, calendar):
    for raw in raw_rows:
        provider_start = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc)
        trading_day = provider_start.astimezone(calendar.timezone).date()
        if not start.date() <= trading_day < end.date():
            continue
        if not calendar.is_trading_day(trading_day):
            continue
        session = calendar.session(trading_day)
        period_start = session.opens_at.astimezone(timezone.utc)
        period_end = session.closes_at.astimezone(timezone.utc)
        yield equity_ohlcv_row(symbol, view, raw, period_start, period_end, "P1D")


def equity_ohlcv_row(
    symbol: str,
    view: str,
    raw: dict[str, object],
    period_start: datetime,
    period_end: datetime,
    interval: str,
) -> dict[str, object]:
    return {
        "period_start": period_start,
        "period_end": period_end,
        "event_time": period_end,
        "available_time": period_end,
        "venue": "us-securities",
        "instrument_id": f"equity:us:{symbol}",
        "symbol": symbol,
        "product": "equity",
        "interval": interval,
        "price_view": view,
        "open": float(raw["o"]),
        "high": float(raw["h"]),
        "low": float(raw["l"]),
        "close": float(raw["c"]),
        "volume": float(raw.get("v", 0)),
        "trade_count": int(raw.get("n", 0)),
        "vwap": float(raw["vw"]) if raw.get("vw") is not None else None,
    }


def option_ohlcv_row(
    symbol: str,
    view: str,
    raw: dict[str, object],
    period_start: datetime,
    period_end: datetime,
    interval: str,
) -> dict[str, object]:
    ticker = option_symbol(symbol)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "event_time": period_end,
        "available_time": period_end,
        "venue": "opra",
        "instrument_id": f"option:us:{ticker.removeprefix('O:')}",
        "symbol": ticker,
        "product": "option",
        "interval": interval,
        "price_view": view,
        "open": float(raw["o"]),
        "high": float(raw["h"]),
        "low": float(raw["l"]),
        "close": float(raw["c"]),
        "volume": float(raw.get("v", 0)),
        "trade_count": int(raw.get("n", 0)),
        "vwap": float(raw["vw"]) if raw.get("vw") is not None else None,
    }


def equity_symbol(value: object) -> str:
    text = str(value).strip().upper()
    if text.startswith("EQUITY:US:"):
        text = text.split(":", 2)[2]
    return text


def option_symbol(value: object) -> str:
    text = str(value).strip().upper()
    if text.startswith("OPTION:US:"):
        text = text.split(":", 2)[2]
    if not text.startswith("O:"):
        text = f"O:{text}"
    if len(text) <= 2:
        raise ValueError("Massive option ticker cannot be empty")
    return text


def merge_equity_ohlcv_rows(root: Path, base_release_id: str | None, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return merge_release_rows(
        root,
        base_release_id,
        rows,
        primary_key=("venue", "instrument_id", "period_start", "interval"),
        order_by=("period_start", "instrument_id"),
    )


def write_equity_ohlcv_dataset(
    target: Path,
    rows: list[dict[str, object]],
    *,
    release_id: str,
    schema: dict[str, object],
    lineage: dict[str, object],
    capabilities: dict[str, object],
) -> None:
    pa, pq = _pyarrow()
    target.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    total_rows = 0
    for (year, month), partition_rows in _partition_rows(rows).items():
        partition = target / f"event_year={year:04d}" / f"event_month={month:02d}"
        partition.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(partition_rows, schema=equity_ohlcv_arrow_schema(pa))
        path = partition / "part-00000.parquet"
        pq.write_table(table, path, compression="zstd")
        content = path.read_bytes()
        files.append({
            "path": path.relative_to(target).as_posix(),
            "rows": len(partition_rows),
            "bytes": len(content),
            "sha256": sha256(content).hexdigest(),
        })
        total_rows += len(partition_rows)
    if total_rows <= 0:
        raise RuntimeError("equity OHLCV writer produced no rows")
    periods = sorted({row["period_start"] for row in rows})
    content_hash = _rows_hash(rows)
    manifest = {
        "manifest_version": 1,
        "dataset_id": release_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_id": schema["schema_id"],
        "partitioning": ["event_year", "event_month"],
        "files": files,
        "rows": total_rows,
        "dataset_sha256": content_hash,
    }
    coverage = {
        "dataset_id": release_id,
        "time_basis": "period_start",
        "timezone": "UTC",
        "boundary": "[start,end)",
        "coverage": {
            "start": periods[0].isoformat(),
            "end": (periods[-1] + timedelta(days=1)).isoformat(),
            "observed_snapshots": len(periods),
            "rows": total_rows,
            "latest_complete_period_end": max(row["period_end"] for row in rows).isoformat(),
        },
        "symbols": sorted({str(row["symbol"]) for row in rows}),
        "incomplete_partitions": [],
    }
    write_json(target / "schema.json", schema)
    write_json(target / "lineage.json", {**lineage, "dataset_id": release_id})
    write_json(target / "coverage.json", coverage)
    write_json(target / "manifest.json", manifest)
    write_json(target / "capabilities.json", {**capabilities, "dataset_id": release_id})
    write_json(target / "quality.json", {
        "quality_schema_version": 1,
        "dataset_id": release_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "checks": [{"name": "non_empty", "passed": True, "value": total_rows, "minimum": 1}],
        "metrics": {"rows": total_rows},
    })


def equity_hourly_ohlcv_schema(schema_id: str) -> dict[str, object]:
    return equity_ohlcv_schema(schema_id, "PT1H")


def equity_ohlcv_schema(schema_id: str, interval: str) -> dict[str, object]:
    return {
        "schema_id": schema_id,
        "schema_version": 1,
        "time_boundary": "[period_start,period_end)",
        "primary_key": ["venue", "instrument_id", "period_start", "interval"],
        "interval": interval,
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


def equity_hourly_ohlcv_arrow_schema(pa):
    return equity_ohlcv_arrow_schema(pa)


def equity_ohlcv_arrow_schema(pa):
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


def _partition_rows(rows: list[dict[str, object]]) -> dict[tuple[int, int], list[dict[str, object]]]:
    result: dict[tuple[int, int], list[dict[str, object]]] = {}
    for row in sorted(rows, key=lambda item: (item["period_start"], item["instrument_id"])):
        period = row["period_start"]
        result.setdefault((period.year, period.month), []).append(row)
    return result


def _rows_hash(rows: list[dict[str, object]]) -> str:
    payload = json.dumps(rows, default=_json_default, sort_keys=True, separators=(",", ":")).encode()
    return sha256(payload).hexdigest()


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("equity OHLCV dataset writing requires the 'data' optional dependency") from error
    return pa, pq


def _json_default(value: object):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value) or "symbol"


def uuid_hex() -> str:
    return uuid4().hex
