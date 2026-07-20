from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable
from zoneinfo import ZoneInfo

from kairos.backtest.calendar import TradingCalendar
from kairos.storage.data_lake import write_json

from .vendor_archive import MassiveFlatFileBatchDownloader, MassiveFlatFileClient, request_fingerprint


_REQUIRED_COLUMNS = {"ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"}
_TRANSFORM_VERSION = 2


@dataclass(frozen=True, slots=True)
class OpraInventoryEntry:
    trading_date: date
    file_key: str
    request_fingerprint: str
    path: Path
    receipt_path: Path
    bytes: int
    sha256: str


class OptionDailyOhlcvPipeline:
    """Governed OPRA daily OHLCV inventory and one option-root Parquet dataset."""

    def __init__(self, lake_root: str | Path, option_root: str, flat_files: MassiveFlatFileClient | None = None, *, calendar: TradingCalendar | None = None) -> None:
        self.root = Path(lake_root)
        self.option_root = option_root.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.]+", self.option_root):
            raise ValueError("option_root must be an OCC root")
        self.option_pattern = re.compile(rf"^O:{re.escape(self.option_root)}(\d{{6}})([CP])(\d{{8}})$")
        self.flat_files = flat_files
        self.calendar = calendar or TradingCalendar()

    def prepare(self, dataset_id: str, start: date, end: date) -> dict[str, object]:
        if not dataset_id.strip():
            raise ValueError("dataset_id cannot be empty")
        inventory, inventory_path, inventory_hash = self.build_inventory(start, end)
        target = self.root / "curated" / "provider=massive" / f"dataset={dataset_id}"
        manifest_path = target / "manifest.json"
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("inventory_sha256") == inventory_hash and existing.get("transform_version") == _TRANSFORM_VERSION:
                return existing
            raise ValueError(f"dataset ID {dataset_id!r} already refers to different immutable source inventory")

        pa, pq = _pyarrow()
        rows_by_month: dict[str, list[dict[str, object]]] = {}
        input_rows = option_rows = 0
        invalid: list[dict[str, object]] = []
        observed_dates: set[date] = set()
        for entry in inventory:
            with gzip.open(entry.path, "rt", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if not _REQUIRED_COLUMNS <= set(reader.fieldnames or ()):
                    raise ValueError(f"OPRA daily OHLCV schema mismatch: {entry.path}")
                for source_row, row in enumerate(reader, start=2):
                    input_rows += 1
                    if not str(row.get("ticker", "")).startswith(f"O:{self.option_root}"):
                        continue
                    try:
                        normalized = _normalize(row, entry.trading_date, self.option_root, self.option_pattern)
                    except (InvalidOperation, KeyError, TypeError, ValueError) as error:
                        invalid.append({"file_key": entry.file_key, "source_row": source_row, "ticker": row.get("ticker"), "error": str(error)})
                        if len(invalid) >= 100:
                            raise ValueError(f"{self.option_root} daily OHLCV exceeded 100 invalid rows")
                        continue
                    rows_by_month.setdefault(entry.trading_date.strftime("%Y-%m"), []).append(normalized)
                    observed_dates.add(entry.trading_date)
                    option_rows += 1
        if invalid:
            quarantine = self.root / "quarantine" / "provider=massive" / f"dataset={dataset_id}"
            quarantine.mkdir(parents=True, exist_ok=True)
            write_json(quarantine / "invalid-daily-ohlcv.json", {"rows": invalid})
            raise ValueError(f"{self.option_root} daily OHLCV contains {len(invalid)} invalid rows")
        if not option_rows:
            raise ValueError(f"OPRA inventory contains no {self.option_root} daily OHLCV")

        files = []
        for month, rows in sorted(rows_by_month.items()):
            table = pa.Table.from_pylist(rows, schema=_schema(pa))
            row_hash = sha256(json.dumps(rows, default=_json_default, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            directory = target / f"year={month[:4]}" / f"month={month[5:]}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"part-{row_hash[:24]}.parquet"
            pq.write_table(table, path, compression="zstd", use_dictionary=True)
            files.append({"path": str(path.relative_to(target)), "month": month, "rows": len(rows), "bytes": path.stat().st_size, "sha256": _file_hash(path)})

        representatives = _representatives(rows_by_month)
        representatives_path = target / "daily_representatives.parquet"
        pq.write_table(pa.Table.from_pylist(representatives), representatives_path, compression="zstd", use_dictionary=True)
        files.append({"path": representatives_path.name, "rows": len(representatives), "bytes": representatives_path.stat().st_size, "sha256": _file_hash(representatives_path), "role": "gold_daily_representatives"})

        expected_dates = {item.trading_date for item in inventory}
        quality = {
            "publishable": observed_dates == expected_dates and not invalid,
            "input_rows": input_rows, "option_rows": option_rows, "option_root": self.option_root, "invalid_rows": len(invalid),
            "expected_trading_days": len(expected_dates), "observed_trading_days": len(observed_dates),
            "missing_trading_days": sorted(item.isoformat() for item in expected_dates - observed_dates),
        }
        if not quality["publishable"]:
            raise ValueError(f"{self.option_root} daily OHLCV dataset failed publish gate")
        dataset_hash = sha256(json.dumps({"inventory_sha256": inventory_hash, "transform_version": _TRANSFORM_VERSION, "files": files}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest = {
            "manifest_version": 1, "transform_version": _TRANSFORM_VERSION, "dataset_id": dataset_id, "format": "parquet", "compression": "zstd",
            "source": "massive.opra.day_aggs_v1", "option_root": self.option_root, "boundary": "[start,end)", "start": start.isoformat(), "end": end.isoformat(),
            "inventory_path": str(inventory_path.relative_to(self.root)), "inventory_sha256": inventory_hash,
            "rows": option_rows, "daily_representative_rows": len(representatives), "files": files,
            "dataset_sha256": dataset_hash, "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(target / "schema.json", {"schema_version": 1, "columns": [field.name for field in _schema(pa)]})
        write_json(target / "lineage.json", {
            "provider": "massive", "api_base": "https://api.massiveprivateserver.site", "resource": MassiveFlatFileBatchDownloader.PREFIX,
            "option_root": self.option_root,
            "inventory_path": manifest["inventory_path"], "inventory_sha256": inventory_hash,
            "transform": f"stream gzip CSV; filter O:{self.option_root}; parse OCC identity; validate OHLC; next-day 11:00 America/New_York available_time; monthly ZSTD Parquet",
            "visibility": {"event_clock": "window_start", "available_time": "next natural day 11:00 America/New_York per Massive OPRA daily aggregates plan recency"},
        })
        write_json(target / "coverage.json", {"start": start.isoformat(), "end": end.isoformat(), "boundary": "[start,end)", "trading_days": len(expected_dates), "calendar": "US_SECURITIES"})
        write_json(target / "quality.json", quality)
        write_json(manifest_path, manifest)
        return manifest

    def build_inventory(self, start: date, end: date) -> tuple[tuple[OpraInventoryEntry, ...], Path, str]:
        if not start < end:
            raise ValueError("inventory requires [start,end) with start < end")
        days = self.calendar.trading_days_between(start, end - timedelta(days=1))
        entries = []
        missing = []
        for trading_day in days:
            key = MassiveFlatFileBatchDownloader.file_key(trading_day)
            path = self._local_file(key)
            if path is None:
                missing.append(key)
                continue
            receipt_path = path.parent / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            actual_hash = _file_hash(path)
            if actual_hash != receipt.get("sha256"):
                raise ValueError(f"Flat File SHA-256 mismatch: {key}")
            if int(receipt.get("bytes", -1)) != path.stat().st_size:
                raise ValueError(f"Flat File byte count mismatch: {key}")
            entries.append(OpraInventoryEntry(
                trading_day, key, path.parent.name.removeprefix("request_id="), path, receipt_path,
                path.stat().st_size, actual_hash,
            ))
        if missing:
            raise FileNotFoundError(f"OPRA daily OHLCV inventory is missing {len(missing)} trading days; first={missing[0]}")
        primitive = [{
            "trading_date": item.trading_date.isoformat(), "file_key": item.file_key,
            "request_fingerprint": item.request_fingerprint, "path": str(item.path.relative_to(self.root)),
            "receipt_path": str(item.receipt_path.relative_to(self.root)), "bytes": item.bytes, "sha256": item.sha256,
        } for item in entries]
        inventory_hash = sha256(json.dumps(primitive, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path = self.root / "reference" / "provider=massive" / "opra_daily_ohlcv" / f"year={start.year}" / f"inventory-{inventory_hash}.json"
        if not path.exists():
            write_json(path, {"inventory_version": 1, "resource": MassiveFlatFileBatchDownloader.PREFIX, "start": start.isoformat(), "end": end.isoformat(), "boundary": "[start,end)", "sha256": inventory_hash, "entries": primitive})
        return tuple(entries), path, inventory_hash

    def _local_file(self, file_key: str) -> Path | None:
        if self.flat_files is not None:
            return self.flat_files.local_file(file_key)
        directory = self.root / "source" / "provider=massive" / "resource=flat-files" / f"request_id={request_fingerprint(file_key, {})}"
        target, receipt_path = directory / Path(file_key).name, directory / "receipt.json"
        if not target.exists() or not receipt_path.exists():
            return None
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if receipt.get("status") != "complete" or receipt.get("file_key") != file_key or int(receipt.get("bytes", -1)) != target.stat().st_size:
            return None
        return target


class SpxwDailyOhlcvPipeline(OptionDailyOhlcvPipeline):
    def __init__(self, lake_root: str | Path, flat_files: MassiveFlatFileClient | None = None, *, calendar: TradingCalendar | None = None) -> None:
        super().__init__(lake_root, "SPXW", flat_files, calendar=calendar)


__all__ = [
    "OpraInventoryEntry",
    "OptionDailyOhlcvPipeline",
    "SpxwDailyOhlcvPipeline",
]


def _normalize(row: dict[str, str], trading_day: date, option_root: str, option_pattern: re.Pattern[str]) -> dict[str, object]:
    ticker = row["ticker"]
    match = option_pattern.match(ticker)
    if not match:
        raise ValueError(f"invalid {option_root} OCC ticker: {ticker}")
    expiry = datetime.strptime(match.group(1), "%y%m%d").date()
    right = "call" if match.group(2) == "C" else "put"
    strike = Decimal(match.group(3)) / Decimal("1000")
    values = {name: Decimal(row[name]) for name in ("open", "high", "low", "close", "volume")}
    transactions = int(row["transactions"])
    if any(values[name] < 0 for name in values) or transactions < 0:
        raise ValueError("prices, volume and transactions must be non-negative")
    if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
        raise ValueError("invalid OHLC ordering")
    window_start = datetime.fromtimestamp(int(row["window_start"]) / 1_000_000_000, tz=timezone.utc)
    if window_start.astimezone(TradingCalendar().timezone).date() != trading_day:
        raise ValueError("window_start does not match file trading date")
    available_time = datetime.combine(trading_day + timedelta(days=1), time(11), ZoneInfo("America/New_York")).astimezone(timezone.utc)
    return {
        "instrument_id": f"option:us:{ticker.removeprefix('O:')}", "ticker": ticker,
        "event_date": trading_day, "window_start": window_start, "available_time": available_time, "expiry": expiry,
        "dte_calendar": (expiry - trading_day).days, "right": right, "strike": strike,
        "open": values["open"], "high": values["high"], "low": values["low"], "close": values["close"],
        "volume": values["volume"], "transactions": transactions,
    }


def _representatives(rows_by_month: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    by_day: dict[date, list[dict[str, object]]] = {}
    for rows in rows_by_month.values():
        for row in rows:
            by_day.setdefault(row["event_date"], []).append(row)  # type: ignore[arg-type]
    output = []
    for trading_day, rows in sorted(by_day.items()):
        calls = [row for row in rows if row["right"] == "call"]
        puts = [row for row in rows if row["right"] == "put"]
        top_call = max(calls, key=lambda row: (row["volume"], row["transactions"], row["ticker"])) if calls else None
        top_put = max(puts, key=lambda row: (row["volume"], row["transactions"], row["ticker"])) if puts else None
        zero_dte = [row for row in rows if row["dte_calendar"] == 0]
        paired: dict[Decimal, dict[str, dict[str, object]]] = {}
        for row in zero_dte:
            paired.setdefault(row["strike"], {})[str(row["right"])] = row  # type: ignore[index]
        forwards = [(strike + sides["call"]["close"] - sides["put"]["close"], strike, sides)
                    for strike, sides in paired.items() if {"call", "put"} <= set(sides)]
        forward = _median([item[0] for item in forwards]) if forwards else None
        atm_sides = min(forwards, key=lambda item: (abs(item[1] - forward), item[1]))[2] if forward is not None else {}
        result: dict[str, object] = {"event_date": trading_day, "synthetic_forward_0dte": forward}
        for label, row in (("top_call", top_call), ("top_put", top_put), ("atm_0dte_call", atm_sides.get("call")), ("atm_0dte_put", atm_sides.get("put"))):
            result[f"{label}_ticker"] = row["ticker"] if row else None
            result[f"{label}_close"] = row["close"] if row else None
            result[f"{label}_volume"] = row["volume"] if row else None
            result[f"{label}_transactions"] = row["transactions"] if row else None
            result[f"{label}_strike"] = row["strike"] if row else None
        result["total_volume"] = sum((row["volume"] for row in rows), Decimal("0"))
        result["total_transactions"] = sum(int(row["transactions"]) for row in rows)
        result["active_contracts"] = len(rows)
        output.append(result)
    return output


def _median(values: Iterable[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _schema(pa):
    decimal = pa.decimal128(20, 6)
    return pa.schema([
        ("instrument_id", pa.string()), ("ticker", pa.string()), ("event_date", pa.date32()),
        ("window_start", pa.timestamp("ns", tz="UTC")), ("available_time", pa.timestamp("ns", tz="UTC")),
        ("expiry", pa.date32()), ("dte_calendar", pa.int32()),
        ("right", pa.string()), ("strike", decimal), ("open", decimal), ("high", decimal),
        ("low", decimal), ("close", decimal), ("volume", decimal), ("transactions", pa.int64()),
    ])


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: object):
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    raise TypeError(type(value).__name__)


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("OPRA daily OHLCV requires the 'data' optional dependency") from error
    return pa, pq
