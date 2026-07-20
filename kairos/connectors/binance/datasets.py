from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
import shutil
from uuid import uuid4
from zipfile import BadZipFile

from kairos.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairos.data.columnar_publishing import publish_intraday_staging_parquet
from kairos.data.http import download
from kairos.data.contracts import DatasetRelease, QualityLevel
from kairos.data.products import (
    BINANCE_USDM_PERPETUAL_HOURLY, BTC_OPTION_QUOTES_HOURLY, BTC_SPOT_DAILY, capabilities_payload,
)
from kairos.data.publishing import (
    content_release_id, content_release_id_from_rows, merge_release_rows, publish_release, release_path,
)
from kairos.storage.data_lake import utc_midnight, write_daily_dataset, write_intraday_dataset, write_json

from .historical_archive import (
    BinanceSpotArchiveProvider, BinanceUsdmPerpetualHourlyArchiveProvider, GracefulShutdown,
    _receipt, _record_payload, _zipped_rows,
)
from .options_archive import BinanceOptionsEohArchiveProvider, normalize_eoh_rows


class BinanceSpotDatasetConnector:
    provider = "binance"

    def __init__(self, root: str | Path = "data", archive: BinanceSpotArchiveProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or BinanceSpotArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_SPOT_DAILY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Binance spot connector received an unsupported acquisition request")
        values = {}
        for missing in request.missing:
            last = (missing.end - timedelta(microseconds=1)).date()
            values.update(self.archive.fetch_daily("BTCUSDT", missing.start.date(), last, self.root / "source"))
        if not values:
            raise RuntimeError("Binance spot archive returned no rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, _ohlcv(values),
            primary_key=("venue", "instrument_id", "period_start", "interval"), order_by=("period_start", "instrument_id"),
        )
        release_id = content_release_id(BTC_SPOT_DAILY, rows)
        lineage = {
            "lineage_version": 2, "dataset_id": release_id,
            "producer": {"name": type(self).__name__, "transform": "binance_spot_kline_to_market_ohlcv", "version": "2"},
            "source": {"provider": "binance", "venue": "binance", "dataset": "spot_klines",
                       "transport": "public_archive", "authentication": "none"},
            "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                                for item in request.missing],
            "point_in_time_safe": True,
        }
        manifest = write_daily_dataset(
            self.root / release_path(BTC_SPOT_DAILY, release_id), rows, dataset_id=release_id,
            schema=_schema(), lineage=lineage, capabilities=capabilities_payload(BTC_SPOT_DAILY, release_id),
        )
        return publish_release(
            self.root, BTC_SPOT_DAILY, release_id, manifest, provider="binance", venue="binance",
            transform_id="binance.spot_kline.ohlcv", transform_version="2", quality_level=QualityLevel.BACKTEST,
        )


class BinanceUsdmPerpetualHourlyDatasetConnector:
    provider = "binance"

    def __init__(self, root: str | Path = "data",
                 archive: BinanceUsdmPerpetualHourlyArchiveProvider | None = None) -> None:
        self.root = Path(root)
        self.archive = archive or BinanceUsdmPerpetualHourlyArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BINANCE_USDM_PERPETUAL_HOURLY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        months = sum(max(1, ceil((item.end - item.start).total_seconds() / (86400 * 28)))
                     for item in request.missing)
        estimate_symbols = getattr(self.archive, "estimated_symbol_count", lambda _root: 700)
        instruments = len(request.instruments) if request.instruments else estimate_symbols(self.root / "source")
        return AcquisitionEstimate(months * instruments, cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Binance USD-M perpetual connector received an unsupported acquisition request")
        symbols = tuple(request.instruments) or self.archive.discover_symbols(self.root / "source")
        invalid = tuple(symbol for symbol in symbols if not symbol.endswith("USDT") or "_" in symbol)
        if invalid:
            raise ValueError(f"USD-M perpetual acquisition received invalid symbols: {', '.join(invalid)}")
        if isinstance(self.archive, BinanceUsdmPerpetualHourlyArchiveProvider):
            return self._acquire_columnar(request, symbols)
        raw = []
        for missing in request.missing:
            if isinstance(self.archive, BinanceUsdmPerpetualHourlyArchiveProvider):
                raw.extend(self.archive.fetch(
                    symbols, missing.start, missing.end, self.root / "source",
                    actual_archives=not bool(request.instruments),
                ))
            else:
                raw.extend(self.archive.fetch(symbols, missing.start, missing.end, self.root / "source"))
        progress = getattr(self.archive, "progress", lambda _event: None)
        progress({"stage": "organize", "event": "start", "raw_rows": len(raw)})
        rows = _usdm_hourly_ohlcv(raw)
        raw.clear()
        del raw
        if not rows:
            raise RuntimeError("Binance USD-M perpetual archive returned no hourly rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, rows,
            primary_key=("venue", "instrument_id", "period_start", "interval"),
            order_by=(),
        )
        release_id = content_release_id_from_rows(BINANCE_USDM_PERPETUAL_HOURLY, rows)
        lineage = {
            "lineage_version": 2, "dataset_id": release_id,
            "producer": {"name": type(self).__name__,
                         "transform": "binance_usdm_perpetual_kline_to_market_ohlcv", "version": "1"},
            "source": {"provider": "binance", "venue": "binance", "dataset": "usdm_klines",
                       "transport": "public_archive", "authentication": "none"},
            "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(),
                                 "boundary": "[start,end)"} for item in request.missing],
            "universe": {
                "kind": "bounded" if request.instruments else "full-market", "symbols": list(symbols),
                "selection": ("explicit acquisition instruments" if request.instruments else
                              "historical archive plus current exchange metadata"),
            },
            "point_in_time_safe": True,
        }
        manifest = write_intraday_dataset(
            self.root / release_path(BINANCE_USDM_PERPETUAL_HOURLY, release_id), rows,
            dataset_id=release_id, schema=_usdm_hourly_schema(), lineage=lineage,
            interval=timedelta(hours=1),
            capabilities=capabilities_payload(BINANCE_USDM_PERPETUAL_HOURLY, release_id),
        )
        release = publish_release(
            self.root, BINANCE_USDM_PERPETUAL_HOURLY, release_id, manifest,
            provider="binance", venue="binance",
            transform_id="binance.usdm_perpetual.kline.ohlcv", transform_version="1",
            quality_level=QualityLevel.BACKTEST,
        )
        progress({"stage": "organize", "event": "complete", "rows": len(rows),
                  "release_id": release.release_id})
        return release

    def _acquire_columnar(self, request: AcquisitionRequest, symbols: tuple[str, ...]) -> DatasetRelease:
        staging = self.root / "tmp" / "columnar" / f"binance-usdm-1h-{uuid4().hex}"
        try:
            stats = _stage_usdm_hourly_archives(
                self.archive, symbols, request.missing, self.root / "source", staging,
                actual_archives=not bool(request.instruments),
            )
            if int(stats["rows"]) <= 0:
                raise RuntimeError("Binance USD-M perpetual archive returned no hourly rows")
            lineage = {
                "lineage_version": 2,
                "producer": {"name": type(self).__name__,
                             "transform": "binance_usdm_perpetual_kline_to_market_ohlcv", "version": "2"},
                "source": {"provider": "binance", "venue": "binance", "dataset": "usdm_klines",
                           "transport": "public_archive", "authentication": "none"},
                "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(),
                                     "boundary": "[start,end)"} for item in request.missing],
                "universe": {
                    "kind": "bounded" if request.instruments else "full-market", "symbols": list(symbols),
                    "selection": ("explicit acquisition instruments" if request.instruments else
                                  "historical archive plus current exchange metadata"),
                },
                "point_in_time_safe": True,
                "publishing": {"engine": "duckdb-parquet", "staged_files": stats["staged_files"]},
            }
            result = publish_intraday_staging_parquet(
                self.root, BINANCE_USDM_PERPETUAL_HOURLY, staging,
                schema=_usdm_hourly_schema(), lineage=lineage,
                interval=timedelta(hours=1),
                capabilities=capabilities_payload(BINANCE_USDM_PERPETUAL_HOURLY, "pending"),
                provider="binance", venue="binance",
                transform_id="binance.usdm_perpetual.kline.ohlcv", transform_version="2",
                quality_level=QualityLevel.BACKTEST,
                primary_key=("venue", "instrument_id", "period_start", "interval"),
                order_by=("period_start", "instrument_id"),
            )
            progress = getattr(self.archive, "progress", lambda _event: None)
            progress({"stage": "organize", "event": "complete", "rows": stats["rows"],
                      "release_id": result.release.release_id})
            return result.release
        finally:
            shutil.rmtree(staging, ignore_errors=True)


class BinanceOptionQuotesDatasetConnector:
    provider = "binance"

    def __init__(self, root: str | Path = "data", archive: BinanceOptionsEohArchiveProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or BinanceOptionsEohArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_OPTION_QUOTES_HOURLY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Binance option quote connector received an unsupported acquisition request")
        raw = []
        for missing in request.missing:
            last = (missing.end - timedelta(microseconds=1)).date()
            raw.extend(self.archive.fetch("BTCUSDT", missing.start.date(), last, self.root / "source"))
        rows = normalize_eoh_rows(raw)
        if not rows:
            raise RuntimeError("Binance option archive returned no rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, rows, primary_key=("period_start", "instrument_id"),
            order_by=("period_start", "instrument_id"),
        )
        release_id = content_release_id(BTC_OPTION_QUOTES_HOURLY, rows)
        lineage = {
            "lineage_version": 2, "dataset_id": release_id,
            "producer": {"name": type(self).__name__, "transform": "binance_option_eoh_to_canonical_quotes", "version": "2"},
            "source": {"provider": "binance", "venue": "binance", "dataset": "option_eoh_summary",
                       "transport": "public_archive", "authentication": "none"},
            "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                                for item in request.missing],
            "pricing_fields": {"implied_volatility": "vendor", "greeks": "vendor"},
            "point_in_time_safe": True,
        }
        manifest = write_intraday_dataset(
            self.root / release_path(BTC_OPTION_QUOTES_HOURLY, release_id), rows, dataset_id=release_id,
            schema=_option_quote_schema(), lineage=lineage, interval=timedelta(hours=1),
            capabilities=capabilities_payload(BTC_OPTION_QUOTES_HOURLY, release_id),
        )
        return publish_release(
            self.root, BTC_OPTION_QUOTES_HOURLY, release_id, manifest, provider="binance", venue="binance",
            transform_id="binance.option_eoh.quotes", transform_version="2", quality_level=QualityLevel.STUDY,
        )


def _stage_usdm_hourly_archives(
    archive: BinanceUsdmPerpetualHourlyArchiveProvider,
    symbols: tuple[str, ...],
    missing_ranges,
    source_root: Path,
    staging_root: Path,
    *,
    actual_archives: bool,
) -> dict[str, object]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    records: list[dict[str, object]] = []
    for missing in missing_ranges:
        plan = archive.acquisition_plan(symbols, missing.start, missing.end, source_root, actual_archives=actual_archives)
        plan_records = list(plan.pop("records"))
        records.extend({**record, "_window_start": missing.start, "_window_end": missing.end} for record in plan_records)
    archive.progress({"stage": "plan", "event": "complete", **_archive_plan_summary(symbols, records, source_root)})

    def stage_record(record: dict[str, object]):
        symbol = str(record["symbol"])
        payload = _record_payload(source_root, record)
        receipt = payload.with_name("receipt.json")
        cached = payload.exists()
        try:
            content = payload.read_bytes() if cached else download(str(record["url"]))
            rows = _canonical_usdm_rows_from_zip(
                content, symbol, record["_window_start"], record["_window_end"],
            )
        except (BadZipFile, StopIteration):
            if not cached:
                return "failed", record, 0, "downloaded archive is not a valid ZIP"
            payload.unlink(missing_ok=True)
            receipt.unlink(missing_ok=True)
            try:
                content = download(str(record["url"]))
                rows = _canonical_usdm_rows_from_zip(
                    content, symbol, record["_window_start"], record["_window_end"],
                )
                cached = False
            except Exception as error:
                return "failed", record, 0, f"cache repair failed: {type(error).__name__}: {error}"
        except Exception as error:
            return "failed", record, 0, f"{type(error).__name__}: {error}"
        if not cached:
            payload.parent.mkdir(parents=True, exist_ok=True)
            temporary = payload.with_suffix(".zip.part")
            temporary.write_bytes(content)
            temporary.replace(payload)
        if not receipt.exists():
            period = record["period"]
            period_end = (period + timedelta(days=1) if record["kind"] == "daily" else
                          datetime(period.year + (period.month == 12), 1 if period.month == 12 else period.month + 1, 1).date())
            write_json(receipt, _receipt(
                "binance", "usdm_klines", str(record["url"]),
                {"symbol": symbol, "interval": "1h", "archive_kind": record["kind"]},
                content, period, period_end,
            ))
        if rows:
            partition = staging_root / f"event_year={int(record['year']):04d}" / f"event_month={int(record['month']):02d}"
            partition.mkdir(parents=True, exist_ok=True)
            suffix = f"{int(record['day']):02d}" if record.get("day") is not None else "month"
            target = partition / f"{symbol}-{record['kind']}-{suffix}.parquet"
            table = pa.Table.from_pylist(rows, schema=_usdm_hourly_arrow_schema(pa))
            pq.write_table(table, target, compression="zstd")
        return "cached" if cached else "downloaded", record, len(rows), None

    counts = {"downloaded": 0, "cached": 0, "unavailable": 0, "failed": 0}
    failures = []
    total_rows = 0
    completed = 0
    archive.progress({"stage": "download", "event": "start", "total": len(records),
                      "symbols": len(symbols), "months": len({(item["year"], item["month"]) for item in records})})
    with ThreadPoolExecutor(max_workers=8) as executor:
        iterator = iter(records)
        pending = {}
        if not archive.stop_event.is_set():
            for _ in range(min(8, len(records))):
                record = next(iterator, None)
                if record is not None:
                    pending[executor.submit(stage_record, record)] = record
        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                status, record, rows, error = future.result()
                completed += 1
                counts[status] += 1
                total_rows += int(rows)
                symbol = str(record["symbol"])
                if error:
                    failures.append(f"{symbol} {record['period']}: {error}")
                archive.progress({"stage": "download", "event": "progress", "completed": completed,
                                  "total": len(records), **counts, "rows": total_rows,
                                  "current": f"{symbol} {record['period']}", "status": status,
                                  "year": record["year"], "month": record["month"]})
                if not archive.stop_event.is_set():
                    next_record = next(iterator, None)
                    if next_record is not None:
                        pending[executor.submit(stage_record, next_record)] = next_record
    archive.progress({"stage": "download", "event": "complete", "completed": completed,
                      "total": len(records), **counts, "rows": total_rows,
                      "stopped": archive.stop_event.is_set()})
    if archive.stop_event.is_set():
        raise GracefulShutdown(
            f"Stopped cleanly after {completed}/{len(records)} archive files; rerun the same command to resume"
        )
    if failures:
        preview = "; ".join(failures[:5])
        raise RuntimeError(
            f"Binance archive download left {len(failures)} failed partitions; rerun the same command to resume. "
            f"First failures: {preview}"
        )
    return {"rows": total_rows, "staged_files": completed, **counts}


def _archive_plan_summary(symbols: tuple[str, ...], records: list[dict[str, object]], source_root: Path) -> dict[str, object]:
    months = sorted({(int(item["year"]), int(item["month"])) for item in records})
    matrix = []
    cached_monthly = cached_daily = 0
    for year, month in months:
        month_records = [item for item in records if int(item["year"]) == year and int(item["month"]) == month]
        monthly = sum(1 for item in month_records if item["kind"] == "monthly" and _record_payload(source_root, item).exists())
        daily = sum(1 for item in month_records if item["kind"] == "daily" and _record_payload(source_root, item).exists())
        cached_monthly += monthly
        cached_daily += daily
        matrix.append({"year": year, "month": month, "tasks": len(month_records),
                       "cached_monthly": monthly, "cached_daily_files": daily})
    cached_total = cached_monthly + cached_daily
    return {
        "symbols": len(symbols),
        "planned_symbols": len({item["symbol"] for item in records}),
        "months": len(months),
        "total_tasks": len(records),
        "cached_monthly": cached_monthly,
        "cached_daily_files": cached_daily,
        "uncached_files": len(records) - cached_total,
        "matrix": matrix,
        "records": records,
    }


def _canonical_usdm_rows_from_zip(content: bytes, symbol: str, start: datetime, end: datetime) -> list[dict[str, object]]:
    rows = []
    for row_symbol, row in _zipped_rows(content, symbol):
        if not row or not row[0].isdigit():
            continue
        timestamp = int(row[0])
        divisor = 1_000_000 if timestamp > 10_000_000_000_000 else 1000
        period_start = datetime.fromtimestamp(timestamp / divisor, timezone.utc)
        if not start <= period_start < end:
            continue
        period_end = period_start + timedelta(hours=1)
        rows.append({
            "period_start": period_start,
            "period_end": period_end,
            "event_time": period_end,
            "available_time": period_end,
            "venue": "binance",
            "instrument_id": f"crypto:binance:perpetual:{row_symbol}",
            "symbol": row_symbol,
            "product": "usdm-perpetual",
            "interval": "PT1H",
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "quote_volume": float(row[7]),
            "trade_count": int(row[8]),
            "taker_buy_base_volume": float(row[9]),
            "taker_buy_quote_volume": float(row[10]),
        })
    return rows


def _usdm_hourly_arrow_schema(pa):
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
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("quote_volume", pa.float64()),
        pa.field("trade_count", pa.int64()),
        pa.field("taker_buy_base_volume", pa.float64()),
        pa.field("taker_buy_quote_volume", pa.float64()),
    ])


def _ohlcv(values):
    return [
        {"period_start": utc_midnight(day), "period_end": utc_midnight(day + timedelta(days=1)),
         "event_time": utc_midnight(day + timedelta(days=1)), "available_time": utc_midnight(day + timedelta(days=1)),
         "venue": "binance", "instrument_id": "BTC-USDT", "interval": "P1D",
         "open": value["open"], "high": value["high"], "low": value["low"], "close": value["close"],
         "volume": value.get("volume", "")}
        for day in sorted(values) for value in [values[day]]
    ]


def _usdm_hourly_ohlcv(values):
    rows = []
    for value in values:
        start = value["period_start"]
        end = start + timedelta(hours=1)
        rows.append({
            "period_start": start.isoformat().replace("+00:00", "Z"),
            "period_end": end.isoformat().replace("+00:00", "Z"),
            "event_time": end.isoformat().replace("+00:00", "Z"),
            "available_time": end.isoformat().replace("+00:00", "Z"),
            "venue": "binance", "instrument_id": f"crypto:binance:perpetual:{value['symbol']}",
            "symbol": value["symbol"], "product": "usdm-perpetual", "interval": "PT1H",
            "open": float(value["open"]), "high": float(value["high"]),
            "low": float(value["low"]), "close": float(value["close"]),
            "volume": float(value["volume"]), "quote_volume": float(value["quote_volume"]),
            "trade_count": int(value["trade_count"]),
            "taker_buy_base_volume": float(value["taker_buy_base_volume"]),
            "taker_buy_quote_volume": float(value["taker_buy_quote_volume"]),
        })
    return rows


def _days(request: AcquisitionRequest) -> int:
    return sum(max(1, ceil((item.end - item.start).total_seconds() / 86400)) for item in request.missing)


def _schema():
    return {
        "schema_id": BTC_SPOT_DAILY.schema_id, "schema_version": 1,
        "time_boundary": "[period_start,period_end)",
        "primary_key": ["venue", "instrument_id", "period_start", "interval"],
        "columns": {
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "venue": {"type": "string"}, "instrument_id": {"type": "string"},
            "interval": {"type": "duration"}, "open": {"type": "number", "unit": "USDT_per_BTC"},
            "high": {"type": "number", "unit": "USDT_per_BTC"},
            "low": {"type": "number", "unit": "USDT_per_BTC"},
            "close": {"type": "number", "unit": "USDT_per_BTC"},
            "volume": {"type": "number", "unit": "BTC"},
        },
    }


def _usdm_hourly_schema():
    return {
        "schema_id": BINANCE_USDM_PERPETUAL_HOURLY.schema_id, "schema_version": 1,
        "time_boundary": "[period_start,period_end)",
        "primary_key": ["venue", "instrument_id", "period_start", "interval"],
        "columns": {
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "venue": {"type": "string"}, "instrument_id": {"type": "string"},
            "symbol": {"type": "string"}, "product": {"type": "string"},
            "interval": {"type": "duration"},
            "open": {"type": "number"}, "high": {"type": "number"},
            "low": {"type": "number"}, "close": {"type": "number"},
            "volume": {"type": "number"}, "quote_volume": {"type": "number", "unit": "USDT"},
            "trade_count": {"type": "integer"},
            "taker_buy_base_volume": {"type": "number"},
            "taker_buy_quote_volume": {"type": "number", "unit": "USDT"},
        },
    }


def _option_quote_schema():
    return {
        "schema_id": BTC_OPTION_QUOTES_HOURLY.schema_id, "schema_version": 1,
        "time_boundary": "[period_start,period_end)", "primary_key": ["period_start", "instrument_id"],
        "columns": {
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "venue": {"type": "string"}, "underlying_id": {"type": "string"},
            "instrument_id": {"type": "string"}, "expiry": {"type": "datetime", "timezone": "UTC"},
            "option_right": {"type": "enum", "values": ["call", "put"]},
            "strike": {"type": "number", "unit": "USDT_per_BTC"},
            "best_bid_price": {"type": "nullable_number"}, "best_ask_price": {"type": "nullable_number"},
            "bid_iv": {"type": "nullable_number", "unit": "absolute_volatility"},
            "ask_iv": {"type": "nullable_number", "unit": "absolute_volatility"},
            "mark_price": {"type": "number"}, "mark_iv": {"type": "number", "unit": "absolute_volatility"},
            "vendor_delta": {"type": "number"}, "vendor_gamma": {"type": "number"},
            "vendor_vega": {"type": "number"}, "vendor_theta": {"type": "number"},
            "volume_contracts": {"type": "number"}, "open_interest_contracts": {"type": "number"},
        },
    }
