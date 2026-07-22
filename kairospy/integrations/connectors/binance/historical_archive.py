from __future__ import annotations

import csv
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import re
from threading import Event
from typing import Callable
from urllib.error import HTTPError
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from kairospy.data.http import download, download_json
from kairospy.infrastructure.storage.data_lake import sha256_bytes, utc_midnight, write_json


class BinanceSpotArchiveProvider:
    base_url = "https://data.binance.vision/data/spot/monthly/klines"

    def fetch_daily(self, symbol: str, start: date, end: date, source_root: Path) -> dict[date, dict[str, float]]:
        months, cursor = [], date(start.year, start.month, 1)
        while cursor <= end:
            months.append(cursor)
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)

        def month_rows(month):
            name = f"{symbol}-1d-{month:%Y-%m}.zip"
            partition = source_root / "provider=binance" / "dataset=spot_klines" / f"symbol={symbol}" / "interval=1d" / f"event_year={month.year:04d}" / f"event_month={month.month:02d}"
            payload = partition / "payload.zip"
            try:
                content = payload.read_bytes() if payload.exists() else download(f"{self.base_url}/{symbol}/1d/{name}")
            except Exception:
                return []
            if not payload.exists():
                partition.mkdir(parents=True, exist_ok=True); payload.write_bytes(content)
                next_month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
                write_json(partition / "receipt.json", _receipt("binance", "spot_klines", f"{self.base_url}/{symbol}/1d/{name}",
                           {"symbol": symbol, "interval": "1d"}, content, month, next_month))
            with ZipFile(BytesIO(content)) as zipped:
                member = next(item for item in zipped.namelist() if item.endswith(".csv"))
                with zipped.open(member) as raw:
                    return list(csv.reader(TextIOWrapper(raw, encoding="utf-8")))

        values = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            for rows in executor.map(month_rows, months):
                for row in rows:
                    timestamp = int(row[0]); divisor = 1_000_000 if timestamp > 10_000_000_000_000 else 1000
                    day = datetime.fromtimestamp(timestamp / divisor, timezone.utc).date()
                    if start <= day <= end:
                        values[day] = {"open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                                       "close": float(row[4]), "volume": float(row[5])}
        return values


class BinanceUsdmPerpetualHourlyArchiveProvider:
    """Official Binance public archive reader for the full USD-M perpetual market."""

    archive_url = "https://data.binance.vision/data/futures/um/monthly/klines"
    daily_archive_url = "https://data.binance.vision/data/futures/um/daily/klines"
    listing_url = "https://s3.dualstack.ap-northeast-1.amazonaws.com/data.binance.vision"
    exchange_info_url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    listing_prefix = "data/futures/um/monthly/klines/"
    daily_listing_prefix = "data/futures/um/daily/klines/"
    _perpetual_symbol = re.compile(r"^[A-Z0-9]+USDT$")
    _monthly_key = re.compile(
        r"^data/futures/um/monthly/klines/(?P<symbol>[A-Z0-9]+USDT)/1h/"
        r"(?P=symbol)-1h-(?P<year>\d{4})-(?P<month>\d{2})\.zip$"
    )
    _daily_key = re.compile(
        r"^data/futures/um/daily/klines/(?P<symbol>[A-Z0-9]+USDT)/1h/"
        r"(?P=symbol)-1h-(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\.zip$"
    )

    def __init__(self, progress: Callable[[dict[str, object]], None] | None = None,
                 stop_event: Event | None = None) -> None:
        self.progress = progress or (lambda _event: None)
        self.stop_event = stop_event or Event()

    def estimated_symbol_count(self, source_root: Path) -> int:
        cache = (source_root / "provider=binance" / "dataset=usdm_perpetual_reference"
                 / "symbol_catalog.json")
        if cache.exists():
            payload = json.loads(cache.read_text(encoding="utf-8"))
            symbols = payload.get("symbols", ())
            if isinstance(symbols, list) and symbols:
                return len(symbols)
        return 700

    def acquisition_plan(self, symbols: tuple[str, ...], start: datetime, end: datetime,
                         source_root: Path, *, actual_archives: bool = True) -> dict[str, object]:
        months = _months_between(start, end)
        selected = set(symbols)
        if actual_archives:
            records = [item for item in self._monthly_archive_records(source_root)
                       if item["symbol"] in selected and start.date() <= item["period"] < end.date()]
            last_month = months[-1]
            monthly_symbols = {item["symbol"] for item in records
                               if item["year"] == last_month.year and item["month"] == last_month.month}
            daily_symbols = tuple(symbol for symbol in symbols if symbol not in monthly_symbols)
            if daily_symbols:
                records.extend(self._daily_archive_records(daily_symbols, last_month, source_root))
            records = [item for item in records if start.date() <= item["period"] < end.date()]
        else:
            records = [_planned_monthly_record(symbol, month) for month in months for symbol in symbols]
        matrix = []
        cached_monthly = 0
        cached_daily = 0
        for month in months:
            month_records = [item for item in records if item["year"] == month.year and item["month"] == month.month]
            monthly = sum(1 for item in month_records if item["kind"] == "monthly"
                          and _record_payload(source_root, item).exists())
            daily = sum(1 for item in month_records if item["kind"] == "daily"
                        and _record_payload(source_root, item).exists())
            cached_monthly += monthly
            cached_daily += daily
            matrix.append({"year": month.year, "month": month.month, "tasks": len(month_records),
                           "cached_monthly": monthly, "cached_daily_files": daily})
        cached_total = cached_monthly + cached_daily
        return {
            "symbols": len(symbols), "planned_symbols": len({item["symbol"] for item in records}),
            "months": len(months), "total_tasks": len(records),
            "cached_monthly": cached_monthly, "cached_daily_files": cached_daily,
            "uncached_files": len(records) - cached_total, "matrix": matrix, "records": records,
        }

    def _monthly_archive_records(self, source_root: Path) -> list[dict[str, object]]:
        cache = (source_root / "provider=binance" / "dataset=usdm_perpetual_reference"
                 / "monthly_archive_manifest_1h.json")
        cached = _fresh_manifest(cache, max_age=timedelta(hours=6))
        if cached is not None:
            return [_restore_record(item) for item in cached.get("records", [])]
        symbol_cache = (source_root / "provider=binance" / "dataset=usdm_perpetual_reference"
                        / "symbol_catalog.json")
        saved = _load_manifest(symbol_cache)
        symbols = tuple(str(item) for item in (saved or {}).get("symbols", ())) or self._archive_symbols()
        self.progress({"stage": "index", "event": "start", "kind": "monthly", "symbols": len(symbols)})

        def symbol_keys(symbol):
            if self.stop_event.is_set():
                return []
            return self._list_keys(f"{self.listing_prefix}{symbol}/1h/", report_pages=False)

        try:
            records = []
            with ThreadPoolExecutor(max_workers=12) as executor:
                for completed, keys in _bounded_map(executor, symbols, symbol_keys, self.stop_event):
                    for key in keys:
                        match = self._monthly_key.fullmatch(key)
                        if not match:
                            continue
                        year, month = int(match["year"]), int(match["month"])
                        records.append({
                            "kind": "monthly", "symbol": match["symbol"], "year": year, "month": month,
                            "day": None, "period": date(year, month, 1), "key": key,
                            "url": f"https://data.binance.vision/{key}",
                        })
                    self.progress({"stage": "index", "event": "progress", "kind": "monthly",
                                   "completed": completed, "total": len(symbols), "records": len(records)})
            if self.stop_event.is_set():
                raise GracefulShutdown("Binance archive indexing stopped cleanly; rerun the same command to resume")
        except Exception:
            stale = _load_manifest(cache)
            if stale is None:
                raise
            records = [_restore_record(item) for item in stale.get("records", [])]
            self.progress({"stage": "index", "event": "stale-cache", "kind": "monthly",
                           "records": len(records)})
            return records
        write_json(cache, {"manifest_version": 1, "fetched_at": datetime.now(timezone.utc).isoformat(),
                           "records": [_serializable_record(item) for item in records]})
        self.progress({"stage": "index", "event": "complete", "kind": "monthly",
                       "records": len(records)})
        return records

    def _daily_archive_records(self, symbols: tuple[str, ...], month: date,
                               source_root: Path) -> list[dict[str, object]]:
        cache = (source_root / "provider=binance" / "dataset=usdm_perpetual_reference"
                 / f"daily_archive_manifest_1h_{month:%Y_%m}.json")
        cached = _fresh_manifest(cache, max_age=timedelta(hours=1))
        if cached is not None and set(cached.get("symbols", ())) >= set(symbols):
            return [_restore_record(item) for item in cached.get("records", [])
                    if item.get("symbol") in set(symbols)]
        self.progress({"stage": "index", "event": "start", "kind": "daily",
                       "symbols": len(symbols), "month": f"{month:%Y-%m}"})

        def symbol_keys(symbol):
            if self.stop_event.is_set():
                return []
            prefix = f"{self.daily_listing_prefix}{symbol}/1h/{symbol}-1h-{month:%Y-%m}"
            return self._list_keys(prefix, report_pages=False)

        records = []
        with ThreadPoolExecutor(max_workers=12) as executor:
            for completed, keys in _bounded_map(executor, symbols, symbol_keys, self.stop_event):
                for key in keys:
                    match = self._daily_key.fullmatch(key)
                    if not match:
                        continue
                    year, value_month, day = int(match["year"]), int(match["month"]), int(match["day"])
                    records.append({
                        "kind": "daily", "symbol": match["symbol"], "year": year,
                        "month": value_month, "day": day, "period": date(year, value_month, day),
                        "key": key, "url": f"https://data.binance.vision/{key}",
                    })
                self.progress({"stage": "index", "event": "progress", "kind": "daily",
                               "completed": completed, "total": len(symbols), "records": len(records)})
        if self.stop_event.is_set():
            raise GracefulShutdown("Binance archive indexing stopped cleanly; rerun the same command to resume")
        write_json(cache, {"manifest_version": 1, "fetched_at": datetime.now(timezone.utc).isoformat(),
                           "symbols": list(symbols),
                           "records": [_serializable_record(item) for item in records]})
        self.progress({"stage": "index", "event": "complete", "kind": "daily",
                       "records": len(records)})
        return records

    def _list_keys(self, prefix: str, *, report_pages: bool = True) -> list[str]:
        token = None
        keys = []
        page = 0
        while True:
            if self.stop_event.is_set():
                raise GracefulShutdown("Binance archive indexing stopped cleanly; rerun the same command to resume")
            params = {"list-type": 2, "prefix": prefix, "max-keys": 1000}
            if token:
                params["continuation-token"] = token
            root = ElementTree.fromstring(download(self.listing_url, params))
            namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            contents = root.findall("s3:Contents/s3:Key", namespace) or root.findall("Contents/Key")
            keys.extend(item.text or "" for item in contents)
            page += 1
            if report_pages:
                self.progress({"stage": "index", "event": "page", "page": page,
                               "keys": len(keys), "prefix": prefix})
            truncated = root.findtext("s3:IsTruncated", default="false", namespaces=namespace)
            if truncated.lower() != "true":
                return keys
            token = root.findtext("s3:NextContinuationToken", default="", namespaces=namespace)
            if not token:
                raise RuntimeError("Binance object listing was truncated without a continuation token")

    def discover_symbols(self, source_root: Path) -> tuple[str, ...]:
        cache = (source_root / "provider=binance" / "dataset=usdm_perpetual_reference"
                 / "symbol_catalog.json")
        if cache.exists():
            saved = json.loads(cache.read_text(encoding="utf-8"))
            symbols = tuple(str(item) for item in saved.get("symbols", ()) if self._perpetual_symbol.fullmatch(str(item)))
            if symbols:
                return tuple(sorted(symbols))
        archive_symbols: set[str] = set()
        errors: list[str] = []
        try:
            archive_symbols.update(self._archive_symbols())
        except Exception as error:
            errors.append(f"archive-listing: {type(error).__name__}: {error}")
        try:
            payload = download_json(self.exchange_info_url, {})
            archive_symbols.update(
                str(item["symbol"]) for item in payload.get("symbols", [])
                if item.get("contractType") == "PERPETUAL" and item.get("quoteAsset") == "USDT"
            )
        except Exception as error:
            errors.append(f"exchange-info: {type(error).__name__}: {error}")
        symbols = tuple(sorted(item for item in archive_symbols if self._perpetual_symbol.fullmatch(item)))
        if not symbols:
            raise RuntimeError("unable to discover Binance USD-M perpetual symbols: " + "; ".join(errors))
        write_json(cache, {
            "provider": "binance", "product": "usdm-perpetual", "quote_asset": "USDT",
            "discovered_at": datetime.now(timezone.utc).isoformat(), "symbols": list(symbols),
            "discovery_errors": errors,
        })
        return symbols

    def fetch(self, symbols: tuple[str, ...], start: datetime, end: datetime,
              source_root: Path, *, actual_archives: bool = False) -> list[dict[str, object]]:
        plan = self.acquisition_plan(symbols, start, end, source_root, actual_archives=actual_archives)
        records = list(plan.pop("records"))
        self.progress({"stage": "plan", "event": "complete", **plan})

        def archive_rows(record):
            symbol = str(record["symbol"])
            payload = _record_payload(source_root, record)
            receipt = payload.with_name("receipt.json")
            cached = payload.exists()
            try:
                content = payload.read_bytes() if cached else download(str(record["url"]))
                rows = _zipped_rows(content, symbol)
            except (BadZipFile, StopIteration):
                if not cached:
                    return "failed", record, [], "downloaded archive is not a valid ZIP"
                payload.unlink(missing_ok=True); receipt.unlink(missing_ok=True)
                try:
                    content = download(str(record["url"]))
                    rows = _zipped_rows(content, symbol)
                    cached = False
                except Exception as error:
                    return "failed", record, [], f"cache repair failed: {type(error).__name__}: {error}"
            except Exception as error:
                return "failed", record, [], f"{type(error).__name__}: {error}"
            if not cached:
                payload.parent.mkdir(parents=True, exist_ok=True)
                temporary = payload.with_suffix(".zip.part")
                temporary.write_bytes(content); temporary.replace(payload)
            if not receipt.exists():
                period = record["period"]
                period_end = (period + timedelta(days=1) if record["kind"] == "daily" else
                              date(period.year + (period.month == 12), 1 if period.month == 12 else period.month + 1, 1))
                write_json(receipt, _receipt(
                    "binance", "usdm_klines", str(record["url"]),
                    {"symbol": symbol, "interval": "1h", "archive_kind": record["kind"]},
                    content, period, period_end,
                ))
            return "cached" if cached else "downloaded", record, rows, None

        result: list[dict[str, object]] = []
        counts = {"downloaded": 0, "cached": 0, "unavailable": 0, "failed": 0}
        failures = []
        self.progress({"stage": "download", "event": "start", "total": len(records),
                       "symbols": len(symbols), "months": plan["months"]})
        completed = 0
        with ThreadPoolExecutor(max_workers=12) as executor:
            iterator = iter(records)
            pending = {}
            if not self.stop_event.is_set():
                for _ in range(min(12, len(records))):
                    record = next(iterator, None)
                    if record is not None:
                        pending[executor.submit(archive_rows, record)] = record
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    status, record, rows, error = future.result()
                    completed += 1
                    counts[status] += 1
                    symbol = str(record["symbol"])
                    if error:
                        failures.append(f"{symbol} {record['period']}: {error}")
                    for row_symbol, row in rows:
                        if not row or not row[0].isdigit():
                            continue
                        timestamp = int(row[0])
                        divisor = 1_000_000 if timestamp > 10_000_000_000_000 else 1000
                        period_start = datetime.fromtimestamp(timestamp / divisor, timezone.utc)
                        if start <= period_start < end:
                            result.append({
                                "symbol": row_symbol, "period_start": period_start,
                                "open": row[1], "high": row[2], "low": row[3], "close": row[4],
                                "volume": row[5], "close_timestamp": int(row[6]),
                                "quote_volume": row[7], "trade_count": int(row[8]),
                                "taker_buy_base_volume": row[9], "taker_buy_quote_volume": row[10],
                            })
                    self.progress({"stage": "download", "event": "progress", "completed": completed,
                                   "total": len(records), **counts, "rows": len(result),
                                   "current": f"{symbol} {record['period']}", "status": status,
                                   "year": record["year"], "month": record["month"]})
                    if not self.stop_event.is_set():
                        next_record = next(iterator, None)
                        if next_record is not None:
                            pending[executor.submit(archive_rows, next_record)] = next_record
        self.progress({"stage": "download", "event": "complete", "completed": completed,
                       "total": len(records), **counts, "rows": len(result),
                       "stopped": self.stop_event.is_set()})
        if self.stop_event.is_set():
            raise GracefulShutdown(
                f"Stopped cleanly after {completed}/{len(records)} archive files; rerun the same command to resume"
            )
        if failures:
            preview = "; ".join(failures[:5])
            raise RuntimeError(
                f"Binance archive download left {len(failures)} failed partitions; rerun the same command to resume. "
                f"First failures: {preview}"
            )
        return result

    def _archive_symbols(self) -> tuple[str, ...]:
        token = None
        symbols: set[str] = set()
        while True:
            params = {"list-type": 2, "delimiter": "/", "prefix": self.listing_prefix, "max-keys": 1000}
            if token:
                params["continuation-token"] = token
            root = ElementTree.fromstring(download(self.listing_url, params))
            namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            prefixes = root.findall("s3:CommonPrefixes/s3:Prefix", namespace)
            if not prefixes:
                prefixes = root.findall("CommonPrefixes/Prefix")
            for item in prefixes:
                parts = (item.text or "").rstrip("/").split("/")
                if parts:
                    symbols.add(parts[-1])
            truncated = root.findtext("s3:IsTruncated", default="false", namespaces=namespace)
            if truncated.lower() != "true":
                break
            token = root.findtext("s3:NextContinuationToken", default="", namespaces=namespace)
            if not token:
                raise RuntimeError("Binance archive listing was truncated without a continuation token")
        return tuple(sorted(symbols))


class GracefulShutdown(RuntimeError):
    pass


def _receipt(provider, dataset, url, params, content, start, end):
    return {"receipt_version": 1, "provider": provider, "dataset": dataset,
            "request": {"url": url, "parameters": params, "window": {"start": utc_midnight(start), "end": utc_midnight(end), "boundary": "[start,end)"}},
            "response": {"status": 200, "bytes": len(content), "sha256": sha256_bytes(content), "download_status": "complete"},
            "authentication": "none"}


def _months_between(start: datetime, end: datetime) -> list[date]:
    normalized_end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
    last = (normalized_end - timedelta(microseconds=1)).date()
    months, cursor = [], date(start.year, start.month, 1)
    while cursor <= last:
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return months


def _planned_monthly_record(symbol: str, month: date) -> dict[str, object]:
    key = f"data/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{month:%Y-%m}.zip"
    return {"kind": "monthly", "symbol": symbol, "year": month.year, "month": month.month,
            "day": None, "period": month, "key": key, "url": f"https://data.binance.vision/{key}"}


def _record_payload(source_root: Path, record: dict[str, object]) -> Path:
    base = (source_root / "provider=binance" / "dataset=usdm_klines"
            / f"symbol={record['symbol']}" / "interval=1h"
            / f"event_year={int(record['year']):04d}" / f"event_month={int(record['month']):02d}")
    if record["kind"] == "daily":
        base = base / f"event_day={int(record['day']):02d}"
    return base / "payload.zip"


def _serializable_record(record: dict[str, object]) -> dict[str, object]:
    return {**record, "period": record["period"].isoformat()}


def _restore_record(record: dict[str, object]) -> dict[str, object]:
    return {**record, "period": date.fromisoformat(str(record["period"]))}


def _load_manifest(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _fresh_manifest(path: Path, *, max_age: timedelta) -> dict[str, object] | None:
    value = _load_manifest(path)
    if value is None:
        return None
    try:
        fetched = datetime.fromisoformat(str(value["fetched_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return value if datetime.now(timezone.utc) - fetched <= max_age else None


def _bounded_map(executor: ThreadPoolExecutor, items, function, stop_event: Event,
                 maximum_in_flight: int = 12):
    iterator = iter(items)
    pending = {}
    if not stop_event.is_set():
        for _ in range(maximum_in_flight):
            item = next(iterator, None)
            if item is None:
                break
            pending[executor.submit(function, item)] = item
    completed = 0
    while pending:
        done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
        for future in done:
            pending.pop(future)
            completed += 1
            yield completed, future.result()
            if not stop_event.is_set():
                item = next(iterator, None)
                if item is not None:
                    pending[executor.submit(function, item)] = item


def _zipped_rows(content: bytes, symbol: str) -> list[tuple[str, list[str]]]:
    with ZipFile(BytesIO(content)) as zipped:
        member = next(item for item in zipped.namelist() if item.endswith(".csv"))
        with zipped.open(member) as raw:
            return [(symbol, row) for row in csv.reader(TextIOWrapper(raw, encoding="utf-8"))]
